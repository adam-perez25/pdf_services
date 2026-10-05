const wordForm = document.querySelector("#word-form");
const wordInput = document.querySelector("#word-input");
const wordDropzone = document.querySelector("#word-dropzone");
const wordFileName = document.querySelector("#word-file-name");
const wordOutputName = document.querySelector("#word-output-name");
const wordStatus = document.querySelector("#word-status");

let selectedWordFile = null;

wordInput.addEventListener("change", () => setWordFile(wordInput.files[0] || null));
wordDropzone.addEventListener("dragover", (event) => {
    event.preventDefault();
    wordDropzone.classList.add("is-dragging");
});
wordDropzone.addEventListener("dragleave", () => wordDropzone.classList.remove("is-dragging"));
wordDropzone.addEventListener("drop", (event) => {
    event.preventDefault();
    wordDropzone.classList.remove("is-dragging");
    setWordFile([...event.dataTransfer.files].find(isWordFile) || null);
});

function setWordFile(file) {
    selectedWordFile = file;
    wordFileName.textContent = selectedWordFile ? selectedWordFile.name : "o arrástralo aquí";
}

wordForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!selectedWordFile) {
        setStatus("Selecciona un documento Word.", true);
        return;
    }

    const name = wordOutputName.value.trim();
    if (!name) {
        setStatus("Escribe un nombre para el PDF resultante.", true);
        wordOutputName.focus();
        return;
    }

    const filename = name.toLowerCase().endsWith(".pdf") ? name : `${name}.pdf`;
    const formData = new FormData();
    formData.append("file", selectedWordFile, selectedWordFile.name);
    formData.append("output_name", name);

    try {
        const saveHandle = await chooseSaveTarget(filename);
        setStatus("Convirtiendo documento...", false);
        const response = await fetch("/api/word-to-pdf", { method: "POST", body: formData });
        if (!response.ok) throw new Error((await response.json()).detail || "No se pudo convertir el documento.");
        await savePdf(await response.blob(), filename, saveHandle);
        selectedWordFile = null;
        wordInput.value = "";
        wordFileName.textContent = "o arrástralo aquí";
        wordOutputName.value = "document.pdf";
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
    wordStatus.textContent = message;
    wordStatus.classList.toggle("is-error", isError);
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

    if (!window.showSaveFilePicker) {
        downloadBlob(blob, filename);
        return;
    }

    downloadBlob(blob, filename);
}

function isWordFile(file) {
    return /\.(doc|docx)$/i.test(file.name);
}
