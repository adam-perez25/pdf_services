const fileInput = document.querySelector("#file-input");
const dropzone = document.querySelector("#dropzone");
const fileList = document.querySelector("#file-list");
const emptyState = document.querySelector("#empty-state");
const fileCount = document.querySelector("#file-count");
const mergeForm = document.querySelector("#merge-form");
const outputName = document.querySelector("#output-name");
const status = document.querySelector("#status");

let selectedFiles = [];
let draggedIndex = null;

fileInput.addEventListener("change", () => {
    selectedFiles = [...fileInput.files];
    renderFiles();
});

dropzone.addEventListener("dragover", (event) => {
    event.preventDefault();
    dropzone.classList.add("is-dragging");
});

dropzone.addEventListener("dragleave", () => dropzone.classList.remove("is-dragging"));
dropzone.addEventListener("drop", (event) => {
    event.preventDefault();
    dropzone.classList.remove("is-dragging");
    selectedFiles = [...event.dataTransfer.files].filter(isPdf);
    renderFiles();
});

function renderFiles() {
    fileList.innerHTML = "";
    emptyState.hidden = selectedFiles.length > 0;
    fileCount.textContent = `${selectedFiles.length} ${selectedFiles.length === 1 ? "archivo" : "archivos"}`;

    selectedFiles.forEach((file, index) => {
        const item = document.createElement("li");
        item.className = "file-item";
        item.draggable = true;
        item.innerHTML = `
            <span class="file-number">${String(index + 1).padStart(2, "0")}</span>
            <span class="file-name" title="${escapeHtml(file.name)}">${escapeHtml(file.name)}</span>
            <button class="move-button" type="button" data-action="up" aria-label="Subir ${escapeHtml(file.name)}">&#8593;</button>
            <button class="move-button" type="button" data-action="down" aria-label="Bajar ${escapeHtml(file.name)}">&#8595;</button>
            <button class="remove-button" type="button" aria-label="Eliminar ${escapeHtml(file.name)}">&times;</button>
        `;

        item.addEventListener("dragstart", () => {
            draggedIndex = index;
            item.classList.add("is-dragging");
        });
        item.addEventListener("dragend", () => item.classList.remove("is-dragging"));
        item.addEventListener("dragover", (event) => event.preventDefault());
        item.addEventListener("drop", (event) => {
            event.stopPropagation();
            moveFile(draggedIndex, index);
        });
        item.querySelector('[data-action="up"]').addEventListener("click", () => moveFile(index, index - 1));
        item.querySelector('[data-action="down"]').addEventListener("click", () => moveFile(index, index + 1));
        item.querySelector(".remove-button").addEventListener("click", () => {
            selectedFiles.splice(index, 1);
            renderFiles();
        });
        fileList.appendChild(item);
    });
}

function moveFile(fromIndex, toIndex) {
    if (fromIndex === null || toIndex < 0 || toIndex >= selectedFiles.length || fromIndex === toIndex) return;
    const [file] = selectedFiles.splice(fromIndex, 1);
    selectedFiles.splice(toIndex, 0, file);
    renderFiles();
}

mergeForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (selectedFiles.length === 0) {
        setStatus("Selecciona al menos un PDF.", true);
        return;
    }

    const name = outputName.value.trim();
    if (!name) {
        setStatus("Escribe un nombre para el PDF resultante.", true);
        outputName.focus();
        return;
    }

    const formData = new FormData();
    selectedFiles.forEach((file) => formData.append("files", file, file.name));
    formData.append("output_name", name);
    setStatus("Uniendo documentos...", false);

    try {
        const response = await fetch("/api/merge", { method: "POST", body: formData });
        if (!response.ok) throw new Error((await response.json()).detail || "No se pudo crear el PDF.");

        downloadBlob(await response.blob(), name.toLowerCase().endsWith(".pdf") ? name : `${name}.pdf`);
        selectedFiles = [];
        outputName.value = "merged.pdf";
        renderFiles();
        setStatus("PDF creado y descargado.", false);
    } catch (error) {
        setStatus(error.message, true);
    }
});

function setStatus(message, isError) {
    status.textContent = message;
    status.classList.toggle("is-error", isError);
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

function escapeHtml(value) {
    return value.replace(/[&<>\"']/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#039;" })[character]);
}

renderFiles();
