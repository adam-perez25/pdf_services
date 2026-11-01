const pdfaForm = document.querySelector("#pdfa-form");
const pdfaMode = document.querySelector("#pdfa-mode");
const pdfaInput = document.querySelector("#pdfa-input");
const pdfaDropzone = document.querySelector("#pdfa-dropzone");
const pdfaFileName = document.querySelector("#pdfa-file-name");
const pdfaPartField = document.querySelector("#pdfa-part-field");
const pdfaPart = document.querySelector("#pdfa-part");
const pdfaOutputName = document.querySelector("#pdfa-output-name");
const pdfaSubmitLabel = document.querySelector("#pdfa-submit-label");
const pdfaStatus = document.querySelector("#pdfa-status");
const setStatus = statusSetter(pdfaStatus);
const progress = uploadProgress(pdfaStatus);

const PDFA_MODES = {
    pdfa: {
        endpoint: "/api/pdfa",
        suffix: "-pdfa",
        defaultName: "documento-pdfa.pdf",
        submit: "Convertir y descargar",
        working: "Convirtiendo a PDF/A... puede tardar un poco en documentos grandes.",
        done: "PDF/A descargado.",
        failed: "No se pudo convertir el PDF a PDF/A.",
    },
    repair: {
        endpoint: "/api/repair",
        suffix: "-reparado",
        defaultName: "documento-reparado.pdf",
        submit: "Reparar y descargar",
        working: "Reparando el PDF...",
        done: "PDF reparado y descargado.",
        failed: "No se pudo reparar el PDF.",
    },
};

let selectedFile = null;

pdfaInput.addEventListener("change", () => setPdfaFile(pdfaInput.files[0] || null));
setupDropzone(pdfaDropzone, (files) => setPdfaFile(files.find(isPdf) || null));
pdfaMode.addEventListener("change", applyMode);
applyMode();

function currentMode() {
    return PDFA_MODES[pdfaMode.value];
}

function applyMode() {
    pdfaPartField.hidden = pdfaMode.value !== "pdfa";
    pdfaSubmitLabel.textContent = currentMode().submit;
    updateOutputName();
    setStatus("", false);
}

function setPdfaFile(file) {
    selectedFile = file;
    pdfaFileName.textContent = file ? file.name : "o arrástralo aquí";
    updateOutputName();
}

function updateOutputName() {
    const mode = currentMode();
    pdfaOutputName.value = selectedFile
        ? selectedFile.name.replace(/\.pdf$/i, "") + mode.suffix + ".pdf"
        : mode.defaultName;
}

pdfaForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const mode = currentMode();
    if (!selectedFile) {
        setStatus("Selecciona un PDF.", true);
        return;
    }

    const name = pdfaOutputName.value.trim();
    if (!name) {
        setStatus("Escribe un nombre para el PDF resultante.", true);
        pdfaOutputName.focus();
        return;
    }

    const formData = new FormData();
    formData.append("file", selectedFile, selectedFile.name);
    if (pdfaMode.value === "pdfa") formData.append("part", pdfaPart.value);
    formData.append("output_name", name);
    setStatus(mode.working, false);

    try {
        const response = await postForm(mode.endpoint, formData, progress);
        if (!response.ok) throw new Error(await errorMessage(response, mode.failed));

        downloadBlob(await response.blob(), withPdfExtension(name));
        setStatus(mode.done, false);
        resetPdfaForm();
    } catch (error) {
        setStatus(error.message, true);
    }
});

function resetPdfaForm() {
    selectedFile = null;
    pdfaInput.value = "";
    pdfaFileName.textContent = "o arrástralo aquí";
    updateOutputName();
}
