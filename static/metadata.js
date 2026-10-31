const metadataForm = document.querySelector("#metadata-form");
const metadataInput = document.querySelector("#metadata-input");
const metadataDropzone = document.querySelector("#metadata-dropzone");
const metadataFileName = document.querySelector("#metadata-file-name");
const metadataDetails = document.querySelector("#metadata-details");
const metadataRemoveAll = document.querySelector("#metadata-remove-all");
const metadataFields = document.querySelector("#metadata-fields");
const metadataOutputName = document.querySelector("#metadata-output-name");
const metadataStatus = document.querySelector("#metadata-status");
const setStatus = statusSetter(metadataStatus);
const progress = uploadProgress(metadataStatus);
const FIELD_NAMES = ["title", "author", "subject", "keywords"];
const DEFAULT_OUTPUT_NAME = "documento-metadatos.pdf";

let selectedFile = null;
// Ignores the answer for a file that was replaced while it was being read.
let readToken = 0;

metadataInput.addEventListener("change", () => setMetadataFile(metadataInput.files[0] || null));
setupDropzone(metadataDropzone, (files) => setMetadataFile(files.find(isPdf) || null));
metadataRemoveAll.addEventListener("change", () => {
    metadataFields.disabled = metadataRemoveAll.checked;
});

function fieldInput(name) {
    return document.querySelector(`#metadata-field-${name}`);
}

async function setMetadataFile(file) {
    selectedFile = file;
    const token = ++readToken;
    metadataFileName.textContent = file ? file.name : "o arrástralo aquí";
    FIELD_NAMES.forEach((name) => { fieldInput(name).value = ""; });
    metadataDetails.hidden = true;
    if (!file) return;
    metadataOutputName.value = file.name.replace(/\.pdf$/i, "") + "-metadatos.pdf";

    const formData = new FormData();
    formData.append("file", file, file.name);
    setStatus("Leyendo los metadatos...", false);
    try {
        const response = await postForm("/api/metadata/read", formData, progress);
        if (!response.ok) throw new Error(await errorMessage(response, "No se pudieron leer los metadatos."));
        const data = JSON.parse(await response.text());
        if (token !== readToken) return;
        FIELD_NAMES.forEach((name) => { fieldInput(name).value = data[name] || ""; });
        showDetails(data);
        setStatus("", false);
    } catch (error) {
        if (token === readToken) setStatus(error.message, true);
    }
}

function showDetails(data) {
    const parts = [`${data.pages} ${data.pages === 1 ? "página" : "páginas"}`];
    if (data.creator) parts.push(`Creado con: ${data.creator}`);
    if (data.producer) parts.push(`Generado por: ${data.producer}`);
    if (data.creation_date) parts.push(`Creado: ${formatDate(data.creation_date)}`);
    if (data.modification_date) parts.push(`Modificado: ${formatDate(data.modification_date)}`);
    metadataDetails.textContent = parts.join(" · ");
    metadataDetails.hidden = false;
}

function formatDate(value) {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? value : date.toLocaleString("es-ES");
}

metadataForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!selectedFile) {
        setStatus("Selecciona un PDF.", true);
        return;
    }

    const name = metadataOutputName.value.trim();
    if (!name) {
        setStatus("Escribe un nombre para el PDF resultante.", true);
        metadataOutputName.focus();
        return;
    }

    const formData = new FormData();
    formData.append("file", selectedFile, selectedFile.name);
    formData.append("remove_all", metadataRemoveAll.checked ? "true" : "false");
    if (!metadataRemoveAll.checked) {
        FIELD_NAMES.forEach((field) => formData.append(field, fieldInput(field).value.trim()));
    }
    formData.append("output_name", name);
    setStatus("Guardando los metadatos...", false);

    try {
        const response = await postForm("/api/metadata", formData, progress);
        if (!response.ok) throw new Error(await errorMessage(response, "No se pudieron editar los metadatos."));

        downloadBlob(await response.blob(), withPdfExtension(name));
        setStatus(metadataRemoveAll.checked ? "Metadatos eliminados. PDF descargado." : "Metadatos guardados. PDF descargado.", false);
        resetMetadataForm();
    } catch (error) {
        setStatus(error.message, true);
    }
});

function resetMetadataForm() {
    selectedFile = null;
    readToken++;
    metadataInput.value = "";
    metadataFileName.textContent = "o arrástralo aquí";
    metadataOutputName.value = DEFAULT_OUTPUT_NAME;
    metadataRemoveAll.checked = false;
    metadataFields.disabled = false;
    metadataDetails.hidden = true;
    FIELD_NAMES.forEach((field) => { fieldInput(field).value = ""; });
}
