const pdfImagesForm = document.querySelector("#pdf-images-form");
const pdfImagesInput = document.querySelector("#pdf-images-input");
const pdfImagesDropzone = document.querySelector("#pdf-images-dropzone");
const pdfImagesFileName = document.querySelector("#pdf-images-file-name");
const pdfImagesFormat = document.querySelector("#pdf-images-format");
const pdfImagesDpi = document.querySelector("#pdf-images-dpi");
const pdfImagesBaseName = document.querySelector("#pdf-images-base-name");
const pdfImagesStatus = document.querySelector("#pdf-images-status");

let selectedFile = null;

pdfImagesInput.addEventListener("change", () => setPdfImagesFile(pdfImagesInput.files[0] || null));
pdfImagesDropzone.addEventListener("dragover", (event) => {
    event.preventDefault();
    pdfImagesDropzone.classList.add("is-dragging");
});
pdfImagesDropzone.addEventListener("dragleave", () => pdfImagesDropzone.classList.remove("is-dragging"));
pdfImagesDropzone.addEventListener("drop", (event) => {
    event.preventDefault();
    pdfImagesDropzone.classList.remove("is-dragging");
    setPdfImagesFile([...event.dataTransfer.files].find(isPdf) || null);
});

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
        if (!response.ok) throw new Error((await response.json()).detail || "No se pudo convertir el PDF.");

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

function downloadName(response) {
    // Names with spaces or accents arrive encoded as filename*=utf-8''...
    const header = response.headers.get("Content-Disposition") || "";
    const encoded = header.match(/filename\*=utf-8''([^;]+)/i);
    if (encoded) return decodeURIComponent(encoded[1]);
    const plain = header.match(/filename="([^"]+)"/);
    return plain ? plain[1] : null;
}

function resetPdfImagesForm() {
    selectedFile = null;
    pdfImagesInput.value = "";
    pdfImagesFileName.textContent = "o arrástralo aquí";
    pdfImagesBaseName.value = "pdf-images";
}

function setStatus(message, isError) {
    pdfImagesStatus.textContent = message;
    pdfImagesStatus.classList.toggle("is-error", isError);
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
