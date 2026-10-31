// Flujo de trabajo: chains the existing tools in the browser. Each step posts
// the previous step's result to that tool's own /api endpoint, so every step
// keeps its validation, size limits and error messages. Only the final result
// is downloaded.

const workflowInput = document.querySelector("#workflow-input");
const workflowDropzone = document.querySelector("#workflow-dropzone");
const workflowFiles = document.querySelector("#workflow-files");
const workflowEmptyFiles = document.querySelector("#workflow-empty-files");
const workflowFileCount = document.querySelector("#workflow-file-count");
const workflowForm = document.querySelector("#workflow-form");
const workflowSteps = document.querySelector("#workflow-steps");
const workflowEmptySteps = document.querySelector("#workflow-empty-steps");
const workflowStepCount = document.querySelector("#workflow-step-count");
const workflowAddSelect = document.querySelector("#workflow-add-select");
const workflowAddButton = document.querySelector("#workflow-add-button");
const workflowOutputName = document.querySelector("#workflow-output-name");
const workflowSubmit = document.querySelector("#workflow-submit");
const workflowStatus = document.querySelector("#workflow-status");
const workflowFallback = document.querySelector("#workflow-fallback");
const workflowFallbackButton = document.querySelector("#workflow-fallback-button");
const setStatus = statusSetter(workflowStatus);
const progress = uploadProgress(workflowStatus);

const STATE_LABELS = { pending: "Pendiente", running: "En curso", done: "Hecho", error: "Error" };

// ---- Small DOM builders for step options (no innerHTML with user text) ----

function field(labelText, control) {
    const label = document.createElement("label");
    label.className = "name-field";
    const span = document.createElement("span");
    span.textContent = labelText;
    label.append(span, control);
    return label;
}

function selectControl(options, selected) {
    const select = document.createElement("select");
    options.forEach(([value, text]) => {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = text;
        option.selected = value === selected;
        select.appendChild(option);
    });
    return select;
}

function numberControl(value, min, max, step) {
    const input = document.createElement("input");
    input.type = "number";
    input.value = value;
    input.min = min;
    input.max = max;
    input.step = step;
    return input;
}

function textControl(type, maxLength, placeholder = "") {
    const input = document.createElement("input");
    input.type = type;
    input.maxLength = maxLength;
    input.autocomplete = type === "password" ? "new-password" : "off";
    if (placeholder) input.placeholder = placeholder;
    return input;
}

function checkControl(labelText) {
    const label = document.createElement("label");
    label.className = "check-field";
    const input = document.createElement("input");
    input.type = "checkbox";
    const span = document.createElement("span");
    span.textContent = labelText;
    label.append(input, span);
    return { label, input };
}

function formatSize(bytes) {
    if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
    return `${(bytes / (1024 * 1024)).toLocaleString("es-ES", { maximumFractionDigits: 1 })} MB`;
}

// ---- Step definitions ----
//
// Each entry describes one tool:
//   id, title          unique key and the label shown to the user
//   endpoint           the existing /api endpoint that does the work
//   working, failed    status text while running and the fallback error
//   last: true         the step must stay at the end (nothing can follow it)
//   build(container)   adds the option controls to container and returns
//                      fill(formData), which appends the tool's form fields and
//                      returns an error message (and focuses the field) when an
//                      option is invalid, or "" when everything is fine.
//   afterResponse      optional: returns a note to show on the step card.
// Adding a new tool is one more entry here.

const MERGE_STEP = {
    id: "merge",
    title: "Unir PDFs",
    endpoint: "/api/merge",
    working: "Uniendo documentos...",
    failed: "No se pudieron unir los PDFs.",
    fixed: true,
    build() {
        return () => "";
    },
};

const STEP_DEFINITIONS = [
    {
        id: "numbers",
        title: "Numerar páginas",
        endpoint: "/api/stamp",
        working: "Numerando páginas...",
        failed: "No se pudo numerar el PDF.",
        build(container) {
            const position = selectControl([
                ["bottom-center", "Abajo, centrado"],
                ["bottom-right", "Abajo, a la derecha"],
                ["bottom-left", "Abajo, a la izquierda"],
                ["top-center", "Arriba, centrado"],
                ["top-right", "Arriba, a la derecha"],
                ["top-left", "Arriba, a la izquierda"],
            ], "bottom-center");
            const format = selectControl([
                ["n", "1"],
                ["n-of-total", "1 / N"],
                ["page-n-of-total", "Página 1 de N"],
            ], "n");
            const start = numberControl("1", "0", "100000", "1");
            const size = numberControl("12", "6", "72", "1");
            container.append(
                field("Posición", position),
                field("Formato", format),
                field("Empezar a numerar en", start),
                field("Tamaño de letra (6-72 pt)", size),
            );
            return (formData) => {
                formData.append("mode", "numbers");
                formData.append("position", position.value);
                formData.append("number_format", format.value);
                formData.append("start_number", start.value);
                formData.append("font_size", size.value);
                return "";
            };
        },
    },
    {
        id: "watermark",
        title: "Marca de agua",
        endpoint: "/api/stamp",
        working: "Añadiendo la marca de agua...",
        failed: "No se pudo añadir la marca de agua.",
        build(container) {
            const text = textControl("text", 100, "Ejemplo: BORRADOR");
            const size = numberControl("60", "8", "200", "1");
            const opacity = numberControl("0.3", "0.1", "1", "0.1");
            const rotation = selectControl([["45", "Diagonal (45°)"], ["0", "Horizontal"]], "45");
            container.append(
                field("Texto de la marca de agua", text),
                field("Tamaño de letra (8-200 pt; se reduce si no cabe)", size),
                field("Opacidad (0,1-1)", opacity),
                field("Orientación", rotation),
            );
            return (formData) => {
                const value = text.value.trim();
                if (!value) {
                    text.focus();
                    return "Escribe el texto de la marca de agua.";
                }
                formData.append("mode", "watermark");
                formData.append("text", value);
                formData.append("font_size", size.value);
                formData.append("opacity", opacity.value);
                formData.append("rotation", rotation.value);
                return "";
            };
        },
    },
    {
        id: "metadata",
        title: "Editar o borrar metadatos",
        endpoint: "/api/metadata",
        working: "Guardando los metadatos...",
        failed: "No se pudieron editar los metadatos.",
        build(container) {
            const removeAll = checkControl("Eliminar todos los metadatos");
            removeAll.input.checked = true;
            const fieldset = document.createElement("fieldset");
            fieldset.className = "metadata-fields";
            fieldset.disabled = true;
            const inputs = {
                title: textControl("text", 500),
                author: textControl("text", 500),
                subject: textControl("text", 500),
                keywords: textControl("text", 500),
            };
            fieldset.append(
                field("Título", inputs.title),
                field("Autor", inputs.author),
                field("Asunto", inputs.subject),
                field("Palabras clave", inputs.keywords),
            );
            removeAll.input.addEventListener("change", () => {
                fieldset.disabled = removeAll.input.checked;
            });
            const hint = document.createElement("p");
            hint.className = "workflow-step-note";
            hint.textContent = "Si no marcas «Eliminar todos», los campos vacíos se borran del documento.";
            container.append(removeAll.label, fieldset, hint);
            return (formData) => {
                formData.append("remove_all", removeAll.input.checked ? "true" : "false");
                if (!removeAll.input.checked) {
                    Object.entries(inputs).forEach(([name, input]) => formData.append(name, input.value.trim()));
                }
                return "";
            };
        },
    },
    {
        id: "compress",
        title: "Comprimir",
        endpoint: "/api/compress",
        working: "Comprimiendo... los documentos escaneados grandes pueden tardar un poco.",
        failed: "No se pudo comprimir el PDF.",
        build(container) {
            const level = selectControl([
                ["strong", "Máxima: archivo más pequeño (imágenes a 72 ppp)"],
                ["recommended", "Recomendada: buena para leer en pantalla (150 ppp)"],
                ["high", "Alta calidad: buena para imprimir (300 ppp)"],
            ], "recommended");
            container.append(field("Nivel de compresión", level));
            return (formData) => {
                formData.append("level", level.value);
                return "";
            };
        },
        afterResponse(response) {
            if (response.headers.get("X-Compression-Applied") !== "true") {
                return "Ya estaba optimizado: comprimirlo no lo reducía, así que se mantiene sin cambios.";
            }
            const original = Number(response.headers.get("X-Original-Size"));
            const compressed = Number(response.headers.get("X-Compressed-Size"));
            const saved = Math.round((1 - compressed / original) * 100);
            return `${formatSize(original)} → ${formatSize(compressed)} (−${saved} %).`;
        },
    },
    {
        id: "protect",
        title: "Proteger con contraseña",
        endpoint: "/api/protect",
        working: "Protegiendo el PDF...",
        failed: "No se pudo proteger el PDF.",
        last: true,
        build(container) {
            // The password only travels in the multipart body, never in a URL,
            // and is never logged.
            const password = textControl("password", 127);
            const confirm = textControl("password", 127);
            container.append(field("Contraseña nueva", password), field("Repite la contraseña", confirm));
            return (formData) => {
                if (!password.value) {
                    password.focus();
                    return "Escribe una contraseña.";
                }
                if (password.value !== confirm.value) {
                    confirm.focus();
                    return "Las contraseñas no coinciden.";
                }
                formData.append("password", password.value);
                return "";
            };
        },
        clear(container) {
            container.querySelectorAll('input[type="password"]').forEach((input) => { input.value = ""; });
        },
    },
    // OCR (being added on another branch) would be one more entry, e.g.:
    // {
    //     id: "ocr",
    //     title: "Reconocer texto (OCR)",
    //     endpoint: "/api/ocr",
    //     working: "Reconociendo el texto... puede tardar varios minutos.",
    //     failed: "No se pudo reconocer el texto.",
    //     build(container) {
    //         const language = selectControl([["spa", "Español"], ["eng", "Inglés"]], "spa");
    //         container.append(field("Idioma", language));
    //         return (formData) => { formData.append("language", language.value); return ""; };
    //     },
    // },
];

// ---- State ----

let selectedFiles = [];
let draggedIndex = null;
let steps = []; // user-chosen steps: { definition, element, options, fill, stateLabel, note }
let mergeStep = null; // created on demand when there is more than one file
let running = false;
let nameEdited = false;
let lastResult = null; // { blob, title } of the last successful step

// Any change to the files or steps makes a kept partial result stale: drop it
// so the fallback button cannot download the output of an older chain.
function forgetLastResult() {
    lastResult = null;
    workflowFallback.hidden = true;
}

// ---- Files (same list behaviour as merge.js) ----

workflowInput.addEventListener("change", () => {
    setFiles([...workflowInput.files]);
    workflowInput.value = "";
});
setupDropzone(workflowDropzone, (files) => setFiles(files.filter(isPdf)));

function setFiles(files) {
    if (running) return;
    selectedFiles = files;
    forgetLastResult();
    renderFiles();
}

function renderFiles() {
    workflowFiles.innerHTML = "";
    workflowEmptyFiles.hidden = selectedFiles.length > 0;
    workflowFileCount.textContent = `${selectedFiles.length} ${selectedFiles.length === 1 ? "archivo" : "archivos"}`;

    selectedFiles.forEach((file, index) => {
        const item = document.createElement("li");
        item.className = "file-item";
        item.draggable = true;
        item.innerHTML = `
            <span class="file-number">${String(index + 1).padStart(2, "0")}</span>
            <span class="file-name" title="${escapeHtml(file.name)}">${escapeHtml(file.name)}</span>
            <button class="move-button" type="button" data-action="up" aria-label="Subir ${escapeHtml(file.name)}">&#8593;</button>
            <button class="move-button" type="button" data-action="down" aria-label="Bajar ${escapeHtml(file.name)}">&#8595;</button>
            <button class="remove-button" type="button" aria-label="Eliminar ${escapeHtml(file.name)}">&times;</button>
        `;
        item.addEventListener("dragstart", () => {
            draggedIndex = index;
            item.classList.add("is-dragging");
        });
        item.addEventListener("dragend", () => {
            item.classList.remove("is-dragging");
            draggedIndex = null;
        });
        item.addEventListener("dragover", (event) => event.preventDefault());
        item.addEventListener("drop", (event) => {
            event.stopPropagation();
            moveFile(draggedIndex, index);
            draggedIndex = null;
        });
        item.querySelector('[data-action="up"]').addEventListener("click", () => moveFile(index, index - 1));
        item.querySelector('[data-action="down"]').addEventListener("click", () => moveFile(index, index + 1));
        item.querySelector(".remove-button").addEventListener("click", () => {
            if (running) return;
            selectedFiles.splice(index, 1);
            forgetLastResult();
            renderFiles();
        });
        workflowFiles.appendChild(item);
    });

    if (!nameEdited) workflowOutputName.value = defaultOutputName();
    renderSteps();
}

function moveFile(fromIndex, toIndex) {
    if (running || fromIndex === null || toIndex < 0 || toIndex >= selectedFiles.length || fromIndex === toIndex) return;
    const [file] = selectedFiles.splice(fromIndex, 1);
    selectedFiles.splice(toIndex, 0, file);
    forgetLastResult();
    renderFiles();
}

function baseName() {
    return selectedFiles.length ? selectedFiles[0].name.replace(/\.pdf$/i, "") || "documento" : "documento";
}

function defaultOutputName() {
    return `${baseName()}-procesado.pdf`;
}

workflowOutputName.addEventListener("input", () => {
    nameEdited = workflowOutputName.value.trim() !== "";
});

// ---- Steps ----

function createStep(definition) {
    const element = document.createElement("li");
    element.className = "workflow-step";

    const header = document.createElement("div");
    header.className = definition.fixed ? "workflow-step-header is-fixed" : "workflow-step-header";
    const number = document.createElement("span");
    number.className = "file-number";
    const title = document.createElement("strong");
    title.className = "workflow-step-title";
    title.textContent = definition.title;
    const stateLabel = document.createElement("span");
    stateLabel.className = "workflow-step-state";
    header.append(number, title, stateLabel);

    const step = { definition, element, number, stateLabel };

    if (!definition.fixed) {
        const up = moveButton("up", `Subir ${definition.title}`, "↑");
        const down = moveButton("down", `Bajar ${definition.title}`, "↓");
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "remove-button";
        remove.setAttribute("aria-label", `Quitar ${definition.title}`);
        remove.textContent = "×";
        up.addEventListener("click", () => moveStep(step, -1));
        down.addEventListener("click", () => moveStep(step, 1));
        remove.addEventListener("click", () => removeStep(step));
        header.append(up, down, remove);
        step.up = up;
        step.down = down;
    }

    const options = document.createElement("div");
    options.className = "workflow-step-options";
    const note = document.createElement("p");
    note.className = "workflow-step-note";
    note.hidden = true;
    element.append(header, options, note);

    step.options = options;
    step.note = note;
    step.fill = definition.build(options);
    if (!options.children.length) options.hidden = true;
    setStepState(step, "pending");
    return step;
}

function moveButton(action, label, symbol) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "move-button";
    button.dataset.action = action;
    button.setAttribute("aria-label", label);
    button.textContent = symbol;
    return button;
}

function setStepState(step, state, note = "") {
    step.stateLabel.textContent = STATE_LABELS[state];
    step.element.dataset.state = state;
    step.note.textContent = note;
    step.note.hidden = !note;
}

// A step can move up or down unless that would put anything after a "last" step.
function canMove(index, offset) {
    const target = index + offset;
    if (target < 0 || target >= steps.length) return false;
    const moving = steps[index].definition;
    const other = steps[target].definition;
    if (offset > 0 && other.last) return false;
    if (offset < 0 && moving.last) return false;
    return true;
}

function moveStep(step, offset) {
    if (running) return;
    const index = steps.indexOf(step);
    if (!canMove(index, offset)) return;
    steps.splice(index, 1);
    steps.splice(index + offset, 0, step);
    forgetLastResult();
    renderSteps();
}

function removeStep(step) {
    if (running) return;
    steps = steps.filter((item) => item !== step);
    forgetLastResult();
    renderSteps();
}

function addStep(id) {
    const definition = STEP_DEFINITIONS.find((item) => item.id === id);
    if (!definition || running || steps.some((item) => item.definition.id === id)) return;
    const step = createStep(definition);
    // New steps go before any step that must stay last.
    const lastIndex = steps.findIndex((item) => item.definition.last);
    if (definition.last || lastIndex === -1) steps.push(step);
    else steps.splice(lastIndex, 0, step);
    forgetLastResult();
    renderSteps();
}

workflowAddButton.addEventListener("click", () => addStep(workflowAddSelect.value));

// The full chain: merge first when there is more than one file.
function chain() {
    if (selectedFiles.length > 1) {
        mergeStep ??= createStep(MERGE_STEP);
        return [mergeStep, ...steps];
    }
    return steps;
}

function renderSteps() {
    const all = chain();
    workflowSteps.replaceChildren(...all.map((step) => step.element));
    all.forEach((step, index) => {
        step.number.textContent = String(index + 1).padStart(2, "0");
    });
    steps.forEach((step, index) => {
        step.up.disabled = !canMove(index, -1);
        step.down.disabled = !canMove(index, 1);
    });
    workflowEmptySteps.hidden = all.length > 0;
    workflowStepCount.textContent = `${all.length} ${all.length === 1 ? "paso" : "pasos"}`;

    const available = STEP_DEFINITIONS.filter((definition) => !steps.some((step) => step.definition.id === definition.id));
    workflowAddSelect.replaceChildren(...available.map((definition) => {
        const option = document.createElement("option");
        option.value = definition.id;
        option.textContent = definition.title;
        return option;
    }));
    workflowAddSelect.disabled = available.length === 0;
    workflowAddButton.disabled = available.length === 0;
}

// ---- Running the chain ----

workflowForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (running) return;
    workflowFallback.hidden = true;
    lastResult = null;

    if (selectedFiles.length === 0) {
        setStatus("Selecciona al menos un PDF.", true);
        return;
    }
    const all = chain();
    if (all.length === 0) {
        setStatus("Añade al menos un paso.", true);
        return;
    }
    const name = workflowOutputName.value.trim();
    if (!name) {
        setStatus("Escribe un nombre para el PDF resultante.", true);
        workflowOutputName.focus();
        return;
    }

    // Validate every step's options before sending anything.
    const forms = [];
    for (const step of all) {
        const formData = new FormData();
        const problem = step.fill(formData);
        if (problem) {
            setStatus(`${step.definition.title}: ${problem}`, true);
            return;
        }
        formData.append("output_name", name);
        forms.push(formData);
    }

    all.forEach((step) => setStepState(step, "pending"));
    setRunning(true);
    const inputName = `${baseName()}.pdf`;
    let current = selectedFiles.length > 1 ? null : selectedFiles[0];
    let finalResponse = null;

    try {
        for (const [index, step] of all.entries()) {
            const formData = forms[index];
            if (step.definition === MERGE_STEP) {
                selectedFiles.forEach((file) => formData.append("files", file, file.name));
            } else {
                formData.append("file", current, inputName);
            }
            setStepState(step, "running");
            setStatus(`Paso ${index + 1} de ${all.length}: ${step.definition.working}`, false);

            let response;
            try {
                response = await postForm(step.definition.endpoint, formData, progress);
                if (!response.ok) throw new Error(await errorMessage(response, step.definition.failed));
            } catch (error) {
                setStepState(step, "error", error.message);
                throw new Error(`Paso ${index + 1} (${step.definition.title}): ${error.message}`);
            }

            current = await response.blob();
            finalResponse = response;
            lastResult = { blob: current, title: step.definition.title };
            setStepState(step, "done", step.definition.afterResponse?.(response) || "");
        }

        downloadBlob(current, downloadName(finalResponse) || withPdfExtension(name));
        setStatus("Flujo completado. PDF descargado.", false);
    } catch (error) {
        setStatus(error.message, true);
        if (lastResult) {
            workflowFallbackButton.textContent = `Descargar el resultado tras «${lastResult.title}»`;
            workflowFallback.hidden = false;
        }
    } finally {
        all.forEach((step) => step.definition.clear?.(step.options));
        setRunning(false);
    }
});

workflowFallbackButton.addEventListener("click", () => {
    if (lastResult) downloadBlob(lastResult.blob, `${baseName()}-parcial.pdf`);
});

function setRunning(value) {
    running = value;
    workflowSubmit.disabled = value;
    workflowAddButton.disabled = value || workflowAddSelect.disabled;
    workflowInput.disabled = value;
}

renderFiles();
