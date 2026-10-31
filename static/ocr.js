const ocrForm = document.querySelector("#ocr-form");
const ocrInput = document.querySelector("#ocr-input");
const ocrDropzone = document.querySelector("#ocr-dropzone");
const ocrFileName = document.querySelector("#ocr-file-name");
const ocrLanguage = document.querySelector("#ocr-language");
const ocrOutputName = document.querySelector("#ocr-output-name");
const ocrStatus = document.querySelector("#ocr-status");
const setStatus = statusSetter(ocrStatus);
const progress = uploadProgress(ocrStatus);
const defaultOcrName = "documento-ocr.pdf";

let selectedFile = null;
let ocrRunning = false;

ocrInput.addEventListener("change", () => setOcrFile(ocrInput.files[0] || null));
setupDropzone(ocrDropzone, (files) => setOcrFile(files.find(isPdf) || null));

function setOcrFile(file) {
    selectedFile = file;
    ocrFileName.textContent = file ? file.name : "o arrástralo aquí";
    if (file) ocrOutputName.value = file.name.replace(/\.pdf$/i, "") + "-ocr.pdf";
}

ocrForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (ocrRunning) return;
    if (!selectedFile) {
        setStatus("Selecciona un PDF.", true);
        return;
    }

    const name = ocrOutputName.value.trim();
    if (!name) {
        setStatus("Escribe un nombre para el PDF resultante.", true);
        ocrOutputName.focus();
        return;
    }

    const formData = new FormData();
    formData.append("file", selectedFile, selectedFile.name);
    formData.append("language", ocrLanguage.value);
    formData.append("output_name", name);
    setStatus("Reconociendo el texto... puede tardar varios segundos por página.", false);

    ocrRunning = true;
    try {
        const response = await postForm("/api/ocr", formData, progress);
        if (!response.ok) throw new Error(await errorMessage(response, "No se pudo aplicar el OCR al PDF."));

        downloadBlob(await response.blob(), withPdfExtension(name));
        setStatus("PDF con texto buscable descargado.", false);
        resetOcrForm();
    } catch (error) {
        setStatus(error.message, true);
    } finally {
        ocrRunning = false;
    }
});

function resetOcrForm() {
    selectedFile = null;
    ocrInput.value = "";
    ocrFileName.textContent = "o arrástralo aquí";
    ocrOutputName.value = defaultOcrName;
}
