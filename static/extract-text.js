const extractForm = document.querySelector("#extract-text-form");
const extractInput = document.querySelector("#extract-text-input");
const extractDropzone = document.querySelector("#extract-text-dropzone");
const extractFileName = document.querySelector("#extract-text-file-name");
const extractPages = document.querySelector("#extract-text-pages");
const extractSeparator = document.querySelector("#extract-text-separator");
const extractOutputName = document.querySelector("#extract-text-output-name");
const extractStatus = document.querySelector("#extract-text-status");
const previewField = document.querySelector("#extract-text-preview-field");
const previewLabel = document.querySelector("#extract-text-preview-label");
const previewText = document.querySelector("#extract-text-preview");
const setStatus = statusSetter(extractStatus);
const progress = uploadProgress(extractStatus);
const PREVIEW_CHARACTERS = 2000;
const DEFAULT_TEXT_NAME = "texto.txt";

let selectedFile = null;

extractInput.addEventListener("change", () => setExtractFile(extractInput.files[0] || null));
setupDropzone(extractDropzone, (files) => setExtractFile(files.find(isPdf) || null));

function setExtractFile(file) {
    selectedFile = file;
    extractFileName.textContent = file ? file.name : "o arrástralo aquí";
    if (file) extractOutputName.value = file.name.replace(/\.pdf$/i, "") + ".txt";
}

function withTxtExtension(name) {
    return name.toLowerCase().endsWith(".txt") ? name : `${name}.txt`;
}

extractForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!selectedFile) {
        setStatus("Selecciona un PDF.", true);
        return;
    }

    const name = extractOutputName.value.trim() || DEFAULT_TEXT_NAME;
    const formData = new FormData();
    formData.append("file", selectedFile, selectedFile.name);
    formData.append("pages", extractPages.value.trim());
    formData.append("page_separator", extractSeparator.value);
    formData.append("output_name", name);
    previewField.hidden = true;
    setStatus("Extrayendo el texto...", false);

    try {
        const response = await postForm("/api/extract-text", formData, progress);
        if (!response.ok) throw new Error(await errorMessage(response, "No se pudo extraer el texto del PDF."));

        const blob = await response.blob();
        downloadBlob(blob, downloadName(response) || withTxtExtension(name));

        const pageCount = Number(response.headers.get("X-Page-Count"));
        const characterCount = Number(response.headers.get("X-Character-Count"));
        const pagesLabel = pageCount === 1 ? "1 página" : `${pageCount.toLocaleString("es-ES")} páginas`;
        setStatus(`Texto descargado: ${characterCount.toLocaleString("es-ES")} caracteres de ${pagesLabel}.`, false);

        // Only the start of the file is read for the preview; textContent and
        // value never interpret the text as HTML.
        const start = await blob.slice(0, PREVIEW_CHARACTERS * 4).text();
        const preview = [...start].slice(0, PREVIEW_CHARACTERS).join("");
        previewText.value = preview;
        previewLabel.textContent = preview.length < start.length || blob.size > PREVIEW_CHARACTERS * 4
            ? `Vista previa (primeros ${PREVIEW_CHARACTERS.toLocaleString("es-ES")} caracteres)`
            : "Vista previa";
        previewField.hidden = false;
        resetExtractForm();
    } catch (error) {
        setStatus(error.message, true);
    }
});

function resetExtractForm() {
    selectedFile = null;
    extractInput.value = "";
    extractFileName.textContent = "o arrástralo aquí";
    extractOutputName.value = DEFAULT_TEXT_NAME;
}
