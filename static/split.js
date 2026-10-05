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

let selectedFile = null;

splitFileInput.addEventListener("change", () => setSplitFile(splitFileInput.files[0] || null));
splitMode.addEventListener("change", updateSplitModeFields);
splitRanges.addEventListener("input", renderRangeNameFields);

splitDropzone.addEventListener("dragover", (event) => {
    event.preventDefault();
    splitDropzone.classList.add("is-dragging");
});
splitDropzone.addEventListener("dragleave", () => splitDropzone.classList.remove("is-dragging"));
splitDropzone.addEventListener("drop", (event) => {
    event.preventDefault();
    splitDropzone.classList.remove("is-dragging");
    setSplitFile([...event.dataTransfer.files].find(isPdf) || null);
});

function setSplitFile(file) {
    selectedFile = file;
    splitFileName.textContent = selectedFile ? selectedFile.name : "o arrástralo aquí";
}

function updateSplitModeFields() {
    const rangesMode = splitMode.value === "ranges";
    splitRangesField.hidden = !rangesMode;
    splitBaseNameField.hidden = rangesMode;
    splitRangeNames.hidden = !rangesMode;
    if (rangesMode) renderRangeNameFields();
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
        const rangeLabel = range.start === range.end ? `Página ${range.start}` : `Páginas ${range.start}-${range.end}`;
        const name = previousNames[index] || `split-${range.start}-${range.end}`;
        return `<label class="name-field range-name-field">
            <span>Nombre para ${rangeLabel}</span>
            <input type="text" data-range-name="${index}" value="${escapeHtml(name)}" maxlength="120" autocomplete="off">
        </label>`;
    }).join("");
    splitRangeNames.hidden = ranges.length === 0;
}

splitForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!selectedFile) {
        setSplitStatus("Selecciona un PDF.", true);
        return;
    }

    try {
        setSplitStatus("Leyendo el documento...", false);
        const pageCount = await getPageCount(selectedFile);
        const ranges = splitMode.value === "pages"
            ? Array.from({ length: pageCount }, (_, index) => ({ start: index + 1, end: index + 1 }))
            : parseRanges(splitRanges.value, pageCount);
        const names = splitMode.value === "pages" ? null : ranges.map((range, index) => {
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
            const response = await requestSplit(selectedFile, outputName, range);
            downloadBlob(await response.blob(), outputName.toLowerCase().endsWith(".pdf") ? outputName : `${outputName}.pdf`);
        }

        resetSplitForm();
        setSplitStatus(`${ranges.length} PDF${ranges.length === 1 ? " creado" : " creados"} y descargado${ranges.length === 1 ? "" : "s"}.`, false);
    } catch (error) {
        setSplitStatus(error.message, true);
    }
});

async function getPageCount(file) {
    const formData = new FormData();
    formData.append("file", file, file.name);
    const response = await fetch("/api/pdf-info", { method: "POST", body: formData });
    if (!response.ok) throw new Error((await response.json()).detail || "No se pudo leer el PDF.");
    return (await response.json()).pages;
}

function parseRanges(value, pageCount) {
    const ranges = value.split(",").map((part) => part.trim()).filter(Boolean).map((part) => {
        const match = part.match(/^(\d+)(?:\s*-\s*(\d+))?$/);
        if (!match) throw new Error(`Rango inválido: ${part}`);
        const start = Number(match[1]);
        const end = Number(match[2] || match[1]);
        if (start < 1 || end < start || end > pageCount) throw new Error(`Los rangos deben estar entre 1 y ${pageCount}.`);
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
    if (!response.ok) throw new Error((await response.json()).detail || "No se pudo dividir el PDF.");
    return response;
}

function resetSplitForm() {
    selectedFile = null;
    splitFileInput.value = "";
    splitFileName.textContent = "o arrástralo aquí";
    splitOutputName.value = "split-pdf";
    splitRanges.value = "";
    splitRangeNames.innerHTML = "";
    splitMode.value = "pages";
    updateSplitModeFields();
}

function setSplitStatus(message, isError) {
    splitStatus.textContent = message;
    splitStatus.classList.toggle("is-error", isError);
}

function downloadBlob(blob, filename) {
    const downloadUrl = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = downloadUrl;
    link.download = filename;
    link.click();
    URL.revokeObjectURL(downloadUrl);
}

function isPdf(file) {
    return file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf");
}

function escapeHtml(value) {
    return value.replace(/[&<>\"']/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#039;" })[character]);
}

updateSplitModeFields();
