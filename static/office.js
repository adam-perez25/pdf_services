// Shared by the Word, Excel and PowerPoint pages. Each page describes its own
// conversion with data attributes on the form: data-endpoint, data-extensions
// (comma-separated, without dots), data-default-name and data-missing-file.
const officeForm = document.querySelector("#office-form");
const officeInput = document.querySelector("#office-input");
const officeDropzone = document.querySelector("#office-dropzone");
const officeFileName = document.querySelector("#office-file-name");
const officeOutputName = document.querySelector("#office-output-name");
const officeStatus = document.querySelector("#office-status");

const { endpoint, extensions, defaultName, missingFile } = officeForm.dataset;
const extensionPattern = new RegExp(`\\.(${extensions.split(",").join("|")})$`, "i");

const emptyFileLabel = officeFileName.textContent;
let selectedOfficeFile = null;

officeInput.addEventListener("change", () => setOfficeFile(officeInput.files[0] || null));
officeDropzone.addEventListener("dragover", (event) => {
    event.preventDefault();
    officeDropzone.classList.add("is-dragging");
});
officeDropzone.addEventListener("dragleave", () => officeDropzone.classList.remove("is-dragging"));
officeDropzone.addEventListener("drop", (event) => {
    event.preventDefault();
    officeDropzone.classList.remove("is-dragging");
    setOfficeFile([...event.dataTransfer.files].find(isOfficeFile) || null);
});

function setOfficeFile(file) {
    selectedOfficeFile = file;
    officeFileName.textContent = selectedOfficeFile ? selectedOfficeFile.name : emptyFileLabel;
}

officeForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!selectedOfficeFile) {
        setStatus(missingFile, true);
        return;
    }

    const name = officeOutputName.value.trim();
    if (!name) {
        setStatus("Escribe un nombre para el PDF resultante.", true);
        officeOutputName.focus();
        return;
    }

    const filename = name.toLowerCase().endsWith(".pdf") ? name : `${name}.pdf`;
    const formData = new FormData();
    formData.append("file", selectedOfficeFile, selectedOfficeFile.name);
    formData.append("output_name", name);

    try {
        const saveHandle = await chooseSaveTarget(filename);
        setStatus("Convirtiendo documento...", false);
        const response = await fetch(endpoint, { method: "POST", body: formData });
        if (!response.ok) throw new Error((await response.json()).detail || "No se pudo convertir el documento.");
        await savePdf(await response.blob(), filename, saveHandle);
        selectedOfficeFile = null;
        officeInput.value = "";
        officeFileName.textContent = emptyFileLabel;
        officeOutputName.value = defaultName;
        setStatus("PDF creado y descargado.", false);
    } catch (error) {
        if (error.name === "AbortError") {
            setStatus("Guardado cancelado.", false);
            return;
        }
        setStatus(error.message, true);
    }
});

function setStatus(message, isError) {
    officeStatus.textContent = message;
    officeStatus.classList.toggle("is-error", isError);
}

function downloadBlob(blob, filename) {
    const downloadUrl = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = downloadUrl;
    link.download = filename;
    link.click();
    URL.revokeObjectURL(downloadUrl);
}

async function chooseSaveTarget(filename) {
    if (!window.showSaveFilePicker) return null;

    return window.showSaveFilePicker({
        suggestedName: filename,
        types: [{
            description: "Documento PDF",
            accept: { "application/pdf": [".pdf"] },
        }],
    });
}

async function savePdf(blob, filename, fileHandle) {
    if (fileHandle) {
        const writable = await fileHandle.createWritable();
        await writable.write(blob);
        await writable.close();
        return;
    }

    downloadBlob(blob, filename);
}

function isOfficeFile(file) {
    return extensionPattern.test(file.name);
}
