const compressForm = document.querySelector("#compress-form");
const compressInput = document.querySelector("#compress-input");
const compressDropzone = document.querySelector("#compress-dropzone");
const compressFileName = document.querySelector("#compress-file-name");
const compressLevel = document.querySelector("#compress-level");
const compressOutputName = document.querySelector("#compress-output-name");
const compressStatus = document.querySelector("#compress-status");

let selectedFile = null;

compressInput.addEventListener("change", () => setCompressFile(compressInput.files[0] || null));
compressDropzone.addEventListener("dragover", (event) => {
    event.preventDefault();
    compressDropzone.classList.add("is-dragging");
});
compressDropzone.addEventListener("dragleave", () => compressDropzone.classList.remove("is-dragging"));
compressDropzone.addEventListener("drop", (event) => {
    event.preventDefault();
    compressDropzone.classList.remove("is-dragging");
    setCompressFile([...event.dataTransfer.files].find(isPdf) || null);
});

function setCompressFile(file) {
    selectedFile = file;
    compressFileName.textContent = file ? `${file.name} · ${formatSize(file.size)}` : "o arrástralo aquí";
    if (file) compressOutputName.value = file.name.replace(/\.pdf$/i, "") + "-compressed.pdf";
}

compressForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!selectedFile) {
        setStatus("Selecciona un PDF.", true);
        return;
    }

    const name = compressOutputName.value.trim();
    if (!name) {
        setStatus("Escribe un nombre para el PDF resultante.", true);
        compressOutputName.focus();
        return;
    }

    const formData = new FormData();
    formData.append("file", selectedFile, selectedFile.name);
    formData.append("level", compressLevel.value);
    formData.append("output_name", name);
    setStatus("Comprimiendo... los documentos escaneados grandes pueden tardar un poco.", false);

    try {
        const response = await fetch("/api/compress", { method: "POST", body: formData });
        if (!response.ok) throw new Error((await response.json()).detail || "No se pudo comprimir el PDF.");

        downloadBlob(await response.blob(), name.toLowerCase().endsWith(".pdf") ? name : `${name}.pdf`);
        const original = Number(response.headers.get("X-Original-Size"));
        const compressed = Number(response.headers.get("X-Compressed-Size"));
        if (response.headers.get("X-Compression-Applied") === "true") {
            const saved = Math.round((1 - compressed / original) * 100);
            setStatus(`PDF comprimido y descargado: ${formatSize(original)} → ${formatSize(compressed)} (−${saved} %).`, false);
        } else {
            setStatus("Este PDF ya estaba optimizado: comprimirlo no lo reducía, así que se ha descargado el original.", false);
        }
        resetCompressForm();
    } catch (error) {
        setStatus(error.message, true);
    }
});

function resetCompressForm() {
    selectedFile = null;
    compressInput.value = "";
    compressFileName.textContent = "o arrástralo aquí";
    compressOutputName.value = "compressed.pdf";
}

function formatSize(bytes) {
    if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
    return `${(bytes / (1024 * 1024)).toLocaleString("es-ES", { maximumFractionDigits: 1 })} MB`;
}

function setStatus(message, isError) {
    compressStatus.textContent = message;
    compressStatus.classList.toggle("is-error", isError);
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
