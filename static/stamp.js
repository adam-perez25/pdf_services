const stampForm = document.querySelector("#stamp-form");
const stampInput = document.querySelector("#stamp-input");
const stampDropzone = document.querySelector("#stamp-dropzone");
const stampFileName = document.querySelector("#stamp-file-name");
const stampMode = document.querySelector("#stamp-mode");
const stampNumbersOptions = document.querySelector("#stamp-numbers-options");
const stampWatermarkOptions = document.querySelector("#stamp-watermark-options");
const stampPosition = document.querySelector("#stamp-position");
const stampNumberFormat = document.querySelector("#stamp-number-format");
const stampStartNumber = document.querySelector("#stamp-start-number");
const stampNumberSize = document.querySelector("#stamp-number-size");
const stampText = document.querySelector("#stamp-text");
const stampWatermarkSize = document.querySelector("#stamp-watermark-size");
const stampOpacity = document.querySelector("#stamp-opacity");
const stampRotation = document.querySelector("#stamp-rotation");
const stampOutputName = document.querySelector("#stamp-output-name");
const stampStatus = document.querySelector("#stamp-status");
const setStatus = statusSetter(stampStatus);
const progress = uploadProgress(stampStatus);
const stampSuffixes = { numbers: "numerado", watermark: "marca-de-agua" };

let selectedFile = null;

stampInput.addEventListener("change", () => setStampFile(stampInput.files[0] || null));
setupDropzone(stampDropzone, (files) => setStampFile(files.find(isPdf) || null));
stampMode.addEventListener("change", () => {
    const watermark = stampMode.value === "watermark";
    stampNumbersOptions.hidden = watermark;
    stampWatermarkOptions.hidden = !watermark;
    updateOutputName();
});

function setStampFile(file) {
    selectedFile = file;
    stampFileName.textContent = file ? file.name : "o arrástralo aquí";
    updateOutputName();
}

function updateOutputName() {
    const base = selectedFile ? selectedFile.name.replace(/\.pdf$/i, "") + "-" : "";
    stampOutputName.value = `${base}${stampSuffixes[stampMode.value]}.pdf`;
}

stampForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!selectedFile) {
        setStatus("Selecciona un PDF.", true);
        return;
    }

    const name = stampOutputName.value.trim();
    if (!name) {
        setStatus("Escribe un nombre para el PDF resultante.", true);
        stampOutputName.focus();
        return;
    }

    const mode = stampMode.value;
    const formData = new FormData();
    formData.append("file", selectedFile, selectedFile.name);
    formData.append("mode", mode);
    formData.append("output_name", name);
    if (mode === "numbers") {
        formData.append("position", stampPosition.value);
        formData.append("number_format", stampNumberFormat.value);
        formData.append("start_number", stampStartNumber.value);
        formData.append("font_size", stampNumberSize.value);
    } else {
        const text = stampText.value.trim();
        if (!text) {
            setStatus("Escribe el texto de la marca de agua.", true);
            stampText.focus();
            return;
        }
        formData.append("text", text);
        formData.append("font_size", stampWatermarkSize.value);
        formData.append("opacity", stampOpacity.value);
        formData.append("rotation", stampRotation.value);
    }
    setStatus(mode === "numbers" ? "Numerando páginas..." : "Añadiendo la marca de agua...", false);

    try {
        const response = await postForm("/api/stamp", formData, progress);
        if (!response.ok) throw new Error(await errorMessage(response, "No se pudo modificar el PDF."));

        downloadBlob(await response.blob(), withPdfExtension(name));
        setStatus(mode === "numbers" ? "PDF numerado y descargado." : "Marca de agua añadida y PDF descargado.", false);
        resetStampForm();
    } catch (error) {
        setStatus(error.message, true);
    }
});

function resetStampForm() {
    selectedFile = null;
    stampInput.value = "";
    stampFileName.textContent = "o arrástralo aquí";
    updateOutputName();
}
