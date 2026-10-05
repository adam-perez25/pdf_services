const imagesForm = document.querySelector("#images-form");
const imagesInput = document.querySelector("#images-input");
const imagesDropzone = document.querySelector("#images-dropzone");
const imagesList = document.querySelector("#images-list");
const imagesEmpty = document.querySelector("#images-empty");
const imagesOutputName = document.querySelector("#images-output-name");
const imagesStatus = document.querySelector("#images-status");

let selectedImages = [];
let draggedIndex = null;

imagesInput.addEventListener("change", () => {
    selectedImages = [...imagesInput.files].filter(isImage);
    renderImages();
});

imagesDropzone.addEventListener("dragover", (event) => {
    event.preventDefault();
    imagesDropzone.classList.add("is-dragging");
});
imagesDropzone.addEventListener("dragleave", () => imagesDropzone.classList.remove("is-dragging"));
imagesDropzone.addEventListener("drop", (event) => {
    event.preventDefault();
    imagesDropzone.classList.remove("is-dragging");
    selectedImages = [...event.dataTransfer.files].filter(isImage);
    renderImages();
});

function renderImages() {
    imagesList.innerHTML = "";
    imagesEmpty.hidden = selectedImages.length > 0;

    selectedImages.forEach((image, index) => {
        const item = document.createElement("li");
        item.className = "image-item";
        item.draggable = true;
        const previewUrl = URL.createObjectURL(image);
        item.innerHTML = `
            <span class="file-number">${String(index + 1).padStart(2, "0")}</span>
            <img class="image-preview" src="${previewUrl}" alt="">
            <span class="file-name" title="${escapeHtml(image.name)}">${escapeHtml(image.name)}</span>
            <button class="move-button" type="button" data-action="up" aria-label="Subir ${escapeHtml(image.name)}">&#8593;</button>
            <button class="move-button" type="button" data-action="down" aria-label="Bajar ${escapeHtml(image.name)}">&#8595;</button>
            <button class="remove-button" type="button" aria-label="Eliminar ${escapeHtml(image.name)}">&times;</button>
        `;

        item.addEventListener("dragstart", () => {
            draggedIndex = index;
            item.classList.add("is-dragging");
        });
        item.addEventListener("dragend", () => {
            item.classList.remove("is-dragging");
            URL.revokeObjectURL(previewUrl);
        });
        item.addEventListener("dragover", (event) => event.preventDefault());
        item.addEventListener("drop", (event) => {
            event.stopPropagation();
            moveImage(draggedIndex, index);
        });
        item.querySelector('[data-action="up"]').addEventListener("click", () => moveImage(index, index - 1));
        item.querySelector('[data-action="down"]').addEventListener("click", () => moveImage(index, index + 1));
        item.querySelector(".remove-button").addEventListener("click", () => {
            selectedImages.splice(index, 1);
            renderImages();
        });
        imagesList.appendChild(item);
    });
}

function moveImage(fromIndex, toIndex) {
    if (fromIndex === null || toIndex < 0 || toIndex >= selectedImages.length || fromIndex === toIndex) return;
    const [image] = selectedImages.splice(fromIndex, 1);
    selectedImages.splice(toIndex, 0, image);
    renderImages();
}

imagesForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (selectedImages.length === 0) {
        setStatus("Selecciona al menos una imagen.", true);
        return;
    }

    const name = imagesOutputName.value.trim();
    if (!name) {
        setStatus("Escribe un nombre para el PDF resultante.", true);
        imagesOutputName.focus();
        return;
    }

    const formData = new FormData();
    selectedImages.forEach((image) => formData.append("images", image, image.name));
    formData.append("output_name", name);
    setStatus("Convirtiendo imágenes...", false);

    try {
        const response = await fetch("/api/images-to-pdf", { method: "POST", body: formData });
        if (!response.ok) throw new Error((await response.json()).detail || "No se pudo crear el PDF.");
        downloadBlob(await response.blob(), name.toLowerCase().endsWith(".pdf") ? name : `${name}.pdf`);
        selectedImages = [];
        imagesInput.value = "";
        imagesOutputName.value = "images.pdf";
        renderImages();
        setStatus("PDF creado y descargado.", false);
    } catch (error) {
        setStatus(error.message, true);
    }
});

function setStatus(message, isError) {
    imagesStatus.textContent = message;
    imagesStatus.classList.toggle("is-error", isError);
}

function downloadBlob(blob, filename) {
    const downloadUrl = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = downloadUrl;
    link.download = filename;
    link.click();
    URL.revokeObjectURL(downloadUrl);
}

function isImage(file) {
    return file.type.startsWith("image/");
}

function escapeHtml(value) {
    return value.replace(/[&<>\"']/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#039;" })[character]);
}

renderImages();
