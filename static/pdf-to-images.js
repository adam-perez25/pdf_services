const pdfImagesForm = document.querySelector("#pdf-images-form");
const pdfImagesInput = document.querySelector("#pdf-images-input");
const pdfImagesDropzone = document.querySelector("#pdf-images-dropzone");
const pdfImagesFileName = document.querySelector("#pdf-images-file-name");
const pdfImagesFormat = document.querySelector("#pdf-images-format");
const pdfImagesDpi = document.querySelector("#pdf-images-dpi");
const pdfImagesBaseName = document.querySelector("#pdf-images-base-name");
const pdfImagesStatus = document.querySelector("#pdf-images-status");
const setStatus = statusSetter(pdfImagesStatus);

let selectedFile = null;

pdfImagesInput.addEventListener("change", () => setPdfImagesFile(pdfImagesInput.files[0] || null));
setupDropzone(pdfImagesDropzone, (files) => setPdfImagesFile(files.find(isPdf) || null));

function setPdfImagesFile(file) {
    selectedFile = file;
    pdfImagesFileName.textContent = file ? file.name : "o arrástralo aquí";
    if (file) pdfImagesBaseName.value = file.name.replace(/\.pdf$/i, "");
}

pdfImagesForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!selectedFile) {
        setStatus("Selecciona un PDF.", true);
        return;
    }

    const baseName = pdfImagesBaseName.value.trim();
    if (!baseName) {
        setStatus("Escribe un nombre base para las imágenes.", true);
        pdfImagesBaseName.focus();
        return;
    }

    const formData = new FormData();
    formData.append("file", selectedFile, selectedFile.name);
    formData.append("image_format", pdfImagesFormat.value);
    formData.append("dpi", pdfImagesDpi.value);
    formData.append("base_name", baseName);
    setStatus("Convirtiendo las páginas... los documentos largos pueden tardar un poco.", false);

    try {
        const response = await fetch("/api/pdf-to-images", { method: "POST", body: formData });
        if (!response.ok) throw new Error(await errorMessage(response, "No se pudo convertir el PDF."));

        const blob = await response.blob();
        const filename = downloadName(response) || `${baseName}.${blob.type === "application/zip" ? "zip" : pdfImagesFormat.value}`;
        downloadBlob(blob, filename);
        resetPdfImagesForm();
        setStatus(blob.type === "application/zip"
            ? `Imágenes creadas y descargadas en ${filename}.`
            : "Imagen creada y descargada.", false);
    } catch (error) {
        setStatus(error.message, true);
    }
});

function resetPdfImagesForm() {
    selectedFile = null;
    pdfImagesInput.value = "";
    pdfImagesFileName.textContent = "o arrástralo aquí";
    pdfImagesBaseName.value = "pdf-images";
}
