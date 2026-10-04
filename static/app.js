const serviceMenu = document.querySelector("#service-menu");
const mergeView = document.querySelector("#merge-view");
const splitView = document.querySelector("#split-view");
const serviceButtons = document.querySelectorAll("[data-service]");
const backButtons = document.querySelectorAll("[data-back]");
const fileInput = document.querySelector("#file-input");
const dropzone = document.querySelector("#dropzone");
const fileList = document.querySelector("#file-list");
const emptyState = document.querySelector("#empty-state");
const fileCount = document.querySelector("#file-count");
const mergeForm = document.querySelector("#merge-form");
const outputName = document.querySelector("#output-name");
const status = document.querySelector("#status");
const splitForm = document.querySelector("#split-form");
const splitFileInput = document.querySelector("#split-file-input");
const splitDropzone = document.querySelector("#split-dropzone");
const splitFileName = document.querySelector("#split-file-name");
const splitMode = document.querySelector("#split-mode");
const splitRangesField = document.querySelector("#split-ranges-field");
const splitRanges = document.querySelector("#split-ranges");
const splitRangeNames = document.querySelector("#split-range-names");
const splitBaseNameField = document.querySelector("#split-base-name-field");
const splitOutputName = document.querySelector("#split-output-name");
const splitStatus = document.querySelector("#split-status");

let selectedFiles = [];
let draggedIndex = null;
let selectedSplitFile = null;

serviceButtons.forEach((button) => {
    button.addEventListener("click", () => showService(button.dataset.service));
});

backButtons.forEach((button) => {
    button.addEventListener("click", showServiceMenu);
});

function showService(service) {
    serviceMenu.hidden = true;
    mergeView.hidden = service !== "merge";
    splitView.hidden = service !== "split";
    window.scrollTo({ top: 0, behavior: "smooth" });
}

function showServiceMenu() {
    serviceMenu.hidden = false;
    mergeView.hidden = true;
    splitView.hidden = true;
    window.scrollTo({ top: 0, behavior: "smooth" });
}

fileInput.addEventListener("change", () => {
    selectedFiles = [...fileInput.files];
    renderFiles();
});

dropzone.addEventListener("dragover", (event) => {
    event.preventDefault();
    dropzone.classList.add("is-dragging");
});

dropzone.addEventListener("dragleave", () => dropzone.classList.remove("is-dragging"));
dropzone.addEventListener("drop", (event) => {
    event.preventDefault();
    dropzone.classList.remove("is-dragging");
    selectedFiles = [...event.dataTransfer.files].filter((file) => file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf"));
    renderFiles();
});

splitFileInput.addEventListener("change", () => {
    setSplitFile(splitFileInput.files[0] || null);
});

splitMode.addEventListener("change", () => {
    updateSplitModeFields();
});

splitRanges.addEventListener("input", renderRangeNameFields);

function updateSplitModeFields() {
    const rangesMode = splitMode.value === "ranges";
    splitRangesField.hidden = !rangesMode;
    splitBaseNameField.hidden = rangesMode;
    if (rangesMode) renderRangeNameFields();
    else splitRangeNames.hidden = true;
}

function renderRangeNameFields() {
    if (splitMode.value !== "ranges") return;
    const previousNames = [...splitRangeNames.querySelectorAll("input")].map((input) => input.value);
    let ranges;
    try {
        ranges = parseRanges(splitRanges.value, Number.MAX_SAFE_INTEGER);
    } catch {
        splitRangeNames.innerHTML = "";
        splitRangeNames.hidden = true;
        return;
    }

    splitRangeNames.innerHTML = ranges.map((range, index) => {
        const rangeLabel = range.start === range.end
            ? `Página ${range.start}`
            : `Páginas ${range.start}-${range.end}`;
        const defaultName = `split-${range.start}-${range.end}`;
        const name = previousNames[index] || defaultName;
        return `<label class="name-field range-name-field">
            <span>Nombre para ${rangeLabel}</span>
            <input type="text" data-range-name="${index}" value="${escapeHtml(name)}" maxlength="120" autocomplete="off">
        </label>`;
    }).join("");
    splitRangeNames.hidden = ranges.length === 0;
}

splitDropzone.addEventListener("dragover", (event) => {
    event.preventDefault();
    splitDropzone.classList.add("is-dragging");
});

splitDropzone.addEventListener("dragleave", () => splitDropzone.classList.remove("is-dragging"));
splitDropzone.addEventListener("drop", (event) => {
    event.preventDefault();
    splitDropzone.classList.remove("is-dragging");
    const file = [...event.dataTransfer.files].find((candidate) => candidate.type === "application/pdf" || candidate.name.toLowerCase().endsWith(".pdf"));
    setSplitFile(file || null);
});

function setSplitFile(file) {
    selectedSplitFile = file;
    splitFileName.textContent = selectedSplitFile ? selectedSplitFile.name : "o arrástralo aquí";
}

function renderFiles() {
    fileList.innerHTML = "";
    emptyState.hidden = selectedFiles.length > 0;
    fileCount.textContent = `${selectedFiles.length} ${selectedFiles.length === 1 ? "archivo" : "archivos"}`;

    selectedFiles.forEach((file, index) => {
        const item = document.createElement("li");
        item.className = "file-item";
        item.draggable = true;
        item.dataset.index = index;
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
        item.addEventListener("dragend", () => item.classList.remove("is-dragging"));
        item.addEventListener("dragover", (event) => event.preventDefault());
        item.addEventListener("drop", (event) => {
            event.stopPropagation();
            moveFile(draggedIndex, index);
        });
        item.querySelector('[data-action="up"]').addEventListener("click", () => moveFile(index, index - 1));
        item.querySelector('[data-action="down"]').addEventListener("click", () => moveFile(index, index + 1));
        item.querySelector(".remove-button").addEventListener("click", () => {
            selectedFiles.splice(index, 1);
            renderFiles();
        });
        fileList.appendChild(item);
    });
}

function moveFile(fromIndex, toIndex) {
    if (fromIndex === null || toIndex < 0 || toIndex >= selectedFiles.length || fromIndex === toIndex) return;
    const [file] = selectedFiles.splice(fromIndex, 1);
    selectedFiles.splice(toIndex, 0, file);
    renderFiles();
}

mergeForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (selectedFiles.length === 0) {
        setStatus("Selecciona al menos un PDF.", true);
        return;
    }

    const name = outputName.value.trim();
    if (!name) {
        setStatus("Escribe un nombre para el PDF resultante.", true);
        outputName.focus();
        return;
    }

    const formData = new FormData();
    selectedFiles.forEach((file) => formData.append("files", file, file.name));
    formData.append("output_name", name);
    setStatus("Uniendo documentos...", false);

    try {
        const response = await fetch("/api/merge", { method: "POST", body: formData });
        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || "No se pudo crear el PDF.");
        }

        const blob = await response.blob();
        const downloadUrl = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = downloadUrl;
        link.download = name.toLowerCase().endsWith(".pdf") ? name : `${name}.pdf`;
        link.click();
        URL.revokeObjectURL(downloadUrl);
        selectedFiles = [];
        outputName.value = "merged.pdf";
        renderFiles();
        setStatus("PDF creado y descargado.", false);
    } catch (error) {
        setStatus(error.message, true);
    }
});

splitForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!selectedSplitFile) {
        setSplitStatus("Selecciona un PDF.", true);
        return;
    }

    try {
        setSplitStatus("Leyendo el documento...", false);
        const pageCount = await getPageCount(selectedSplitFile);
        const ranges = splitMode.value === "pages"
            ? Array.from({ length: pageCount }, (_, index) => ({ start: index + 1, end: index + 1 }))
            : parseRanges(splitRanges.value, pageCount);
        const names = splitMode.value === "pages"
            ? null
            : ranges.map((range, index) => {
                const input = splitRangeNames.querySelector(`[data-range-name="${index}"]`);
                const name = input ? input.value.trim() : "";
                if (!name) throw new Error(`Escribe un nombre para el rango ${range.start}-${range.end}.`);
                return name;
            });
        const baseName = splitOutputName.value.trim();
        if (splitMode.value === "pages" && !baseName) {
            setSplitStatus("Escribe un nombre base para los PDFs.", true);
            splitOutputName.focus();
            return;
        }

        for (let index = 0; index < ranges.length; index += 1) {
            const range = ranges[index];
            setSplitStatus(`Descargando PDF ${index + 1} de ${ranges.length}...`, false);
            const suffix = range.start === range.end
                ? `page-${String(range.start).padStart(3, "0")}`
                : `pages-${String(range.start).padStart(3, "0")}-${String(range.end).padStart(3, "0")}`;
            const outputName = names ? names[index] : `${baseName}-${suffix}`;
            const response = await requestSplit(selectedSplitFile, outputName, range);
            const downloadName = outputName.toLowerCase().endsWith(".pdf") ? outputName : `${outputName}.pdf`;
            downloadBlob(await response.blob(), downloadName);
        }

        selectedSplitFile = null;
        splitFileInput.value = "";
        splitFileName.textContent = "o arrástralo aquí";
        splitOutputName.value = "split-pdf";
        splitRanges.value = "";
        splitRangeNames.innerHTML = "";
        splitRangeNames.hidden = true;
        splitMode.value = "pages";
        updateSplitModeFields();
        setSplitStatus(`${ranges.length} PDF${ranges.length === 1 ? " creado" : " creados"} y descargado${ranges.length === 1 ? "" : "s"}.`, false);
    } catch (error) {
        setSplitStatus(error.message, true);
    }
});

async function getPageCount(file) {
    const formData = new FormData();
    formData.append("file", file, file.name);
    const response = await fetch("/api/pdf-info", { method: "POST", body: formData });
    if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || "No se pudo leer el PDF.");
    }
    return (await response.json()).pages;
}

function parseRanges(value, pageCount) {
    const ranges = value.split(",").map((part) => part.trim()).filter(Boolean).map((part) => {
        const match = part.match(/^(\d+)(?:\s*-\s*(\d+))?$/);
        if (!match) throw new Error(`Rango inválido: ${part}`);
        const start = Number(match[1]);
        const end = Number(match[2] || match[1]);
        if (start < 1 || end < start || end > pageCount) {
            throw new Error(`Los rangos deben estar entre 1 y ${pageCount}.`);
        }
        return { start, end };
    });
    if (ranges.length === 0) throw new Error("Escribe al menos un rango de páginas.");
    return ranges;
}

async function requestSplit(file, outputName, range) {
    const formData = new FormData();
    formData.append("file", file, file.name);
    formData.append("output_name", outputName);
    formData.append("start_page", range.start);
    formData.append("end_page", range.end);
    const response = await fetch("/api/split", { method: "POST", body: formData });
    if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || "No se pudo dividir el PDF.");
    }
    return response;
}

function downloadBlob(blob, filename) {
    const downloadUrl = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = downloadUrl;
    link.download = filename;
    link.click();
    URL.revokeObjectURL(downloadUrl);
}

function setStatus(message, isError) {
    status.textContent = message;
    status.classList.toggle("is-error", isError);
}

function setSplitStatus(message, isError) {
    splitStatus.textContent = message;
    splitStatus.classList.toggle("is-error", isError);
}

function escapeHtml(value) {
    return value.replace(/[&<>\"']/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#039;" })[character]);
}

renderFiles();
