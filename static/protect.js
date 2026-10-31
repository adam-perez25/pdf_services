const protectForm = document.querySelector("#protect-form");
const protectMode = document.querySelector("#protect-mode");
const protectInput = document.querySelector("#protect-input");
const protectDropzone = document.querySelector("#protect-dropzone");
const protectFileName = document.querySelector("#protect-file-name");
const protectPassword = document.querySelector("#protect-password");
const protectPasswordLabel = document.querySelector("#protect-password-label");
const protectConfirmField = document.querySelector("#protect-confirm-field");
const protectConfirm = document.querySelector("#protect-confirm");
const protectOutputName = document.querySelector("#protect-output-name");
const protectSubmitLabel = document.querySelector("#protect-submit-label");
const protectStatus = document.querySelector("#protect-status");
const setStatus = statusSetter(protectStatus);
const progress = uploadProgress(protectStatus);

const PROTECT_MODES = {
    protect: {
        endpoint: "/api/protect",
        suffix: "-protegido",
        defaultName: "protegido.pdf",
        passwordLabel: "Contraseña nueva",
        autocomplete: "new-password",
        submit: "Proteger y descargar",
        working: "Protegiendo el PDF...",
        done: "PDF protegido y descargado.",
        failed: "No se pudo proteger el PDF.",
    },
    unlock: {
        endpoint: "/api/unlock",
        suffix: "-desbloqueado",
        defaultName: "desbloqueado.pdf",
        passwordLabel: "Contraseña actual",
        autocomplete: "current-password",
        submit: "Desbloquear y descargar",
        working: "Desbloqueando el PDF...",
        done: "PDF desbloqueado y descargado.",
        failed: "No se pudo desbloquear el PDF.",
    },
};

let selectedFile = null;

protectInput.addEventListener("change", () => setProtectFile(protectInput.files[0] || null));
setupDropzone(protectDropzone, (files) => setProtectFile(files.find(isPdf) || null));
protectMode.addEventListener("change", applyMode);
applyMode();

function currentMode() {
    return PROTECT_MODES[protectMode.value];
}

function applyMode() {
    const mode = currentMode();
    const unlocking = protectMode.value === "unlock";
    protectPasswordLabel.textContent = mode.passwordLabel;
    protectPassword.autocomplete = mode.autocomplete;
    protectConfirmField.hidden = unlocking;
    protectSubmitLabel.textContent = mode.submit;
    protectPassword.value = "";
    protectConfirm.value = "";
    updateOutputName();
    setStatus("", false);
}

function setProtectFile(file) {
    selectedFile = file;
    protectFileName.textContent = file ? file.name : "o arrástralo aquí";
    updateOutputName();
}

function updateOutputName() {
    const mode = currentMode();
    protectOutputName.value = selectedFile
        ? selectedFile.name.replace(/\.pdf$/i, "") + mode.suffix + ".pdf"
        : mode.defaultName;
}

protectForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const mode = currentMode();
    if (!selectedFile) {
        setStatus("Selecciona un PDF.", true);
        return;
    }

    const password = protectPassword.value;
    if (!password) {
        setStatus("Escribe una contraseña.", true);
        protectPassword.focus();
        return;
    }
    if (protectMode.value === "protect" && password !== protectConfirm.value) {
        setStatus("Las contraseñas no coinciden.", true);
        protectConfirm.focus();
        return;
    }

    const name = protectOutputName.value.trim();
    if (!name) {
        setStatus("Escribe un nombre para el PDF resultante.", true);
        protectOutputName.focus();
        return;
    }

    // The password travels only in the multipart body, never in the URL.
    const formData = new FormData();
    formData.append("file", selectedFile, selectedFile.name);
    formData.append("password", password);
    formData.append("output_name", name);
    setStatus(mode.working, false);

    try {
        const response = await postForm(mode.endpoint, formData, progress);
        if (!response.ok) throw new Error(await errorMessage(response, mode.failed));

        downloadBlob(await response.blob(), withPdfExtension(name));
        setStatus(mode.done, false);
        resetProtectForm();
    } catch (error) {
        setStatus(error.message, true);
    }
});

function resetProtectForm() {
    selectedFile = null;
    protectInput.value = "";
    protectFileName.textContent = "o arrástralo aquí";
    protectPassword.value = "";
    protectConfirm.value = "";
    updateOutputName();
}
