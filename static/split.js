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
const setSplitStatus = statusSetter(splitStatus);
const progress = uploadProgress(splitStatus);

let selectedFile = null;

splitFileInput.addEventListener("change", () => setSplitFile(splitFileInput.files[0] || null));
splitMode.addEventListener("change", updateSplitModeFields);
splitRanges.addEventListener("input", renderRangeNameFields);

setupDropzone(splitDropzone, (files) => setSplitFile(files.find(isPdf) || null));

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
        // The page count is only known on the server, which validates the ranges.
        const ranges = splitMode.value === "pages" ? null : parseRanges(splitRanges.value, Number.MAX_SAFE_INTEGER);
        const names = ranges && ranges.map((range, index) => {
            const input = splitRangeNames.querySelector(`[data-range-name="${index}"]`);
            const name = input ? input.value.trim() : "";
            if (!name) throw new Error(`Escribe un nombre para el rango ${range.start}-${range.end}.`);
            return name;
        });
        const baseName = splitOutputName.value.trim();
        if (!ranges && !baseName) {
            setSplitStatus("Escribe un nombre base para los PDFs.", true);
            splitOutputName.focus();
            return;
        }

        setSplitStatus("Dividiendo el documento...", false);
        const response = await requestSplit(selectedFile, { ranges, names, baseName });
        const blob = await response.blob();
        const filename = downloadName(response) || (blob.type === "application/zip" ? "split.zip" : "split.pdf");
        downloadBlob(blob, filename);

        resetSplitForm();
        setSplitStatus(blob.type === "application/zip"
            ? `PDFs creados y descargados en ${filename}.`
            : "PDF creado y descargado.", false);
    } catch (error) {
        setSplitStatus(error.message, true);
    }
});

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

async function requestSplit(file, { ranges, names, baseName }) {
    const formData = new FormData();
    formData.append("file", file, file.name);
    formData.append("mode", ranges ? "ranges" : "pages");
    if (ranges) {
        formData.append("ranges", splitRanges.value);
        formData.append("names", JSON.stringify(names));
    } else {
        formData.append("base_name", baseName);
    }
    const response = await postForm("/api/split-zip", formData, progress);
    if (!response.ok) throw new Error(await errorMessage(response, "No se pudo dividir el PDF."));
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

updateSplitModeFields();
