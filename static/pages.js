const pagesFileInput = document.querySelector("#pages-file-input");
const pagesDropzone = document.querySelector("#pages-dropzone");
const pagesFileName = document.querySelector("#pages-file-name");
const pagesToolbar = document.querySelector("#pages-toolbar");
const pagesReset = document.querySelector("#pages-reset");
const pagesEmpty = document.querySelector("#pages-empty");
const pageGrid = document.querySelector("#page-grid");
const pageCount = document.querySelector("#page-count");
const pagesForm = document.querySelector("#pages-form");
const pagesOutputName = document.querySelector("#pages-output-name");
const pagesStatus = document.querySelector("#pages-status");
const pagePreview = document.querySelector("#page-preview");
const pagePreviewTitle = document.querySelector("#page-preview-title");
const pagePreviewPosition = document.querySelector("#page-preview-position");
const pagePreviewImage = document.querySelector("#page-preview-image");

let selectedFile = null;
let thumbnails = [];
let pages = [];
let drag = null;
let suppressClick = false;
let previewIndex = null;
let previewUrls = new Map();

pagesFileInput.addEventListener("change", () => {
    if (pagesFileInput.files[0]) loadFile(pagesFileInput.files[0]);
});

pagesDropzone.addEventListener("dragover", (event) => {
    event.preventDefault();
    pagesDropzone.classList.add("is-dragging");
});

pagesDropzone.addEventListener("dragleave", () => pagesDropzone.classList.remove("is-dragging"));
pagesDropzone.addEventListener("drop", (event) => {
    event.preventDefault();
    pagesDropzone.classList.remove("is-dragging");
    const file = [...event.dataTransfer.files].find(isPdf);
    if (file) loadFile(file);
});

pagesToolbar.querySelectorAll("[data-rotate-all]").forEach((button) => {
    button.addEventListener("click", () => {
        const degrees = Number(button.dataset.rotateAll);
        pages.forEach((page) => { page.rotation += degrees; });
        renderPages();
    });
});

pagesReset.addEventListener("click", () => {
    resetPages();
    renderPages();
});

async function loadFile(file) {
    selectedFile = file;
    previewUrls.forEach((url) => URL.revokeObjectURL(url));
    previewUrls = new Map();
    pagesFileName.textContent = file.name;
    pagesOutputName.value = file.name.replace(/\.pdf$/i, "") + "-edited.pdf";
    thumbnails = [];
    pages = [];
    renderPages();
    setStatus("Generando vistas previas...", false);

    try {
        const formData = new FormData();
        formData.append("file", file, file.name);
        const response = await fetch("/api/pdf-thumbnails", { method: "POST", body: formData });
        if (!response.ok) throw new Error((await response.json()).detail || "No se pudo leer el PDF.");
        if (selectedFile !== file) return;

        thumbnails = (await response.json()).thumbnails;
        resetPages();
        renderPages();
        setStatus("", false);
    } catch (error) {
        selectedFile = null;
        pagesFileName.textContent = "o arrástralo aquí";
        setStatus(error.message, true);
    }
}

function resetPages() {
    pages = thumbnails.map((_, index) => ({ page: index + 1, rotation: 0 }));
}

function renderPages() {
    pageGrid.innerHTML = "";
    pagesEmpty.hidden = pages.length > 0;
    pagesEmpty.textContent = selectedFile && thumbnails.length > 0
        ? "Has eliminado todas las páginas. Pulsa Restablecer para recuperarlas."
        : "Selecciona un PDF para ver sus páginas.";
    pagesToolbar.hidden = thumbnails.length === 0;
    const removed = thumbnails.length - pages.length;
    pageCount.textContent = `${pages.length} ${pages.length === 1 ? "página" : "páginas"}`
        + (removed > 0 ? ` · ${removed} eliminada${removed === 1 ? "" : "s"}` : "");

    pages.forEach((page, index) => {
        const item = document.createElement("li");
        item.className = "page-card";
        item.dataset.index = index;
        item.innerHTML = `
            <button class="page-thumb" type="button" aria-label="Ampliar página ${page.page}">
                <img src="${thumbnails[page.page - 1]}" alt="Página ${page.page}" style="transform: rotate(${page.rotation}deg)">
            </button>
            <div class="page-meta">
                <span class="file-number">${String(index + 1).padStart(2, "0")}</span>
                <small>pág. ${page.page}</small>
            </div>
            <div class="page-actions">
                <button class="move-button" type="button" data-action="left" aria-label="Mover página ${page.page} antes">&#8592;</button>
                <button class="move-button" type="button" data-action="ccw" aria-label="Girar página ${page.page} a la izquierda">&#8634;</button>
                <button class="move-button" type="button" data-action="cw" aria-label="Girar página ${page.page} a la derecha">&#8635;</button>
                <button class="remove-button" type="button" data-action="remove" aria-label="Eliminar página ${page.page}">&times;</button>
                <button class="move-button" type="button" data-action="right" aria-label="Mover página ${page.page} después">&#8594;</button>
            </div>
        `;

        item.addEventListener("pointerdown", (event) => startDrag(event, item));
        item.querySelector(".page-thumb").addEventListener("click", () => {
            if (!suppressClick) openPreview(index);
        });
        item.querySelector('[data-action="left"]').addEventListener("click", () => movePage(index, index - 1));
        item.querySelector('[data-action="right"]').addEventListener("click", () => movePage(index, index + 1));
        item.querySelector('[data-action="ccw"]').addEventListener("click", () => rotatePage(index, -90));
        item.querySelector('[data-action="cw"]').addEventListener("click", () => rotatePage(index, 90));
        item.querySelector('[data-action="remove"]').addEventListener("click", () => {
            pages.splice(index, 1);
            renderPages();
        });
        pageGrid.appendChild(item);
    });
}

function movePage(fromIndex, toIndex) {
    if (fromIndex === null || toIndex < 0 || toIndex >= pages.length || fromIndex === toIndex) return;
    const [page] = pages.splice(fromIndex, 1);
    pages.splice(toIndex, 0, page);
    renderPages();
}

function rotatePage(index, degrees) {
    pages[index].rotation += degrees;
    renderPages();
}

// Drag to reorder with pointer events so it works with both mouse and touch.
// A mouse drag starts after a small movement; a touch drag needs a short
// press-and-hold so normal swipes still scroll the page.
const DRAG_THRESHOLD = 6;
const TOUCH_HOLD_MS = 250;
const EDGE_SCROLL_ZONE = 70;

function startDrag(event, item) {
    if (drag || event.button !== 0 || event.target.closest(".page-actions")) return;
    drag = {
        item,
        pointerId: event.pointerId,
        isTouch: event.pointerType !== "mouse",
        startX: event.clientX,
        startY: event.clientY,
        x: event.clientX,
        y: event.clientY,
        active: false,
        ghost: null,
        holdTimer: null,
        scrollFrame: null,
    };
    if (drag.isTouch) drag.holdTimer = setTimeout(activateDrag, TOUCH_HOLD_MS);
    document.addEventListener("pointermove", moveDrag);
    document.addEventListener("pointerup", endDrag);
    document.addEventListener("pointercancel", cancelDrag);
}

function activateDrag() {
    const rect = drag.item.getBoundingClientRect();
    drag.offsetX = drag.startX - rect.left;
    drag.offsetY = drag.startY - rect.top;
    drag.ghost = drag.item.cloneNode(true);
    drag.ghost.classList.add("page-ghost");
    drag.ghost.style.width = `${rect.width}px`;
    document.body.appendChild(drag.ghost);
    drag.item.classList.add("is-placeholder");
    document.body.classList.add("is-reordering");
    drag.active = true;
    if (navigator.vibrate && drag.isTouch) navigator.vibrate(15);
    positionGhost();
    drag.scrollFrame = requestAnimationFrame(autoScroll);
}

function moveDrag(event) {
    if (event.pointerId !== drag.pointerId) return;
    drag.x = event.clientX;
    drag.y = event.clientY;
    const distance = Math.hypot(drag.x - drag.startX, drag.y - drag.startY);

    if (!drag.active) {
        if (drag.isTouch && distance > DRAG_THRESHOLD) cancelDrag();
        else if (!drag.isTouch && distance > DRAG_THRESHOLD) activateDrag();
        return;
    }

    event.preventDefault();
    positionGhost();
    placeAtPointer();
}

function positionGhost() {
    drag.ghost.style.transform = `translate(${drag.x - drag.offsetX}px, ${drag.y - drag.offsetY}px) rotate(2deg)`;
}

function placeAtPointer() {
    const target = document.elementFromPoint(drag.x, drag.y)?.closest(".page-card");
    if (!target || target === drag.item || target.parentElement !== pageGrid) return;
    const cards = [...pageGrid.children];
    const movingForward = cards.indexOf(target) > cards.indexOf(drag.item);
    pageGrid.insertBefore(drag.item, movingForward ? target.nextSibling : target);
}

function autoScroll() {
    if (!drag?.active) return;
    if (drag.y < EDGE_SCROLL_ZONE) window.scrollBy(0, -12);
    else if (drag.y > window.innerHeight - EDGE_SCROLL_ZONE) window.scrollBy(0, 12);
    placeAtPointer();
    drag.scrollFrame = requestAnimationFrame(autoScroll);
}

function endDrag(event) {
    if (event.pointerId !== drag.pointerId) return;
    const wasActive = drag.active;
    const newOrder = [...pageGrid.children].map((card) => pages[Number(card.dataset.index)]);
    finishDrag();
    if (!wasActive) return;

    // The click that follows pointerup must not open the preview.
    suppressClick = true;
    setTimeout(() => { suppressClick = false; }, 0);
    pages = newOrder;
    renderPages();
}

function cancelDrag() {
    const wasActive = drag.active;
    finishDrag();
    if (wasActive) renderPages();
}

function finishDrag() {
    clearTimeout(drag.holdTimer);
    cancelAnimationFrame(drag.scrollFrame);
    drag.ghost?.remove();
    drag.item.classList.remove("is-placeholder");
    document.body.classList.remove("is-reordering");
    document.removeEventListener("pointermove", moveDrag);
    document.removeEventListener("pointerup", endDrag);
    document.removeEventListener("pointercancel", cancelDrag);
    drag = null;
}

pageGrid.addEventListener("click", (event) => {
    if (!suppressClick) return;
    event.preventDefault();
    event.stopPropagation();
}, true);

// Stop the page from scrolling under a touch drag; this listener must not be passive.
document.addEventListener("touchmove", (event) => {
    if (drag?.active) event.preventDefault();
}, { passive: false });
pageGrid.addEventListener("contextmenu", (event) => {
    if (drag) event.preventDefault();
});

function openPreview(index) {
    previewIndex = index;
    renderPreview();
    if (!pagePreview.open) pagePreview.showModal();
}

async function renderPreview() {
    const page = pages[previewIndex];
    pagePreviewTitle.textContent = `Página ${page.page}`;
    pagePreviewPosition.textContent = `POSICIÓN ${previewIndex + 1} DE ${pages.length}`;
    pagePreviewImage.alt = `Página ${page.page}`;
    pagePreviewImage.style.transform = `rotate(${page.rotation}deg)`;
    pagePreviewImage.classList.toggle("is-sideways", Math.abs(page.rotation / 90) % 2 === 1);
    pagePreview.querySelector('[data-preview="prev"]').disabled = previewIndex === 0;
    pagePreview.querySelector('[data-preview="next"]').disabled = previewIndex === pages.length - 1;

    // Show the thumbnail straight away and swap in the sharp render once it arrives.
    pagePreviewImage.src = previewUrls.get(page.page) || thumbnails[page.page - 1];
    if (previewUrls.has(page.page)) return;

    try {
        const file = selectedFile;
        const formData = new FormData();
        formData.append("file", file, file.name);
        formData.append("page", page.page);
        const response = await fetch("/api/pdf-page-preview", { method: "POST", body: formData });
        if (!response.ok) throw new Error((await response.json()).detail || "No se pudo cargar la vista previa.");
        const url = URL.createObjectURL(await response.blob());
        if (selectedFile !== file) {
            URL.revokeObjectURL(url);
            return;
        }
        previewUrls.set(page.page, url);
        if (pagePreview.open && pages[previewIndex] === page) pagePreviewImage.src = url;
    } catch (error) {
        setStatus(error.message, true);
    }
}

pagePreview.querySelector('[data-preview="prev"]').addEventListener("click", () => {
    if (previewIndex > 0) openPreview(previewIndex - 1);
});
pagePreview.querySelector('[data-preview="next"]').addEventListener("click", () => {
    if (previewIndex < pages.length - 1) openPreview(previewIndex + 1);
});
pagePreview.querySelector('[data-preview="ccw"]').addEventListener("click", () => {
    rotatePage(previewIndex, -90);
    renderPreview();
});
pagePreview.querySelector('[data-preview="cw"]').addEventListener("click", () => {
    rotatePage(previewIndex, 90);
    renderPreview();
});
document.querySelector("#page-preview-close").addEventListener("click", () => pagePreview.close());
pagePreview.addEventListener("click", (event) => {
    if (event.target === pagePreview) pagePreview.close();
});
pagePreview.addEventListener("keydown", (event) => {
    if (event.key === "ArrowLeft" && previewIndex > 0) openPreview(previewIndex - 1);
    if (event.key === "ArrowRight" && previewIndex < pages.length - 1) openPreview(previewIndex + 1);
});

pagesForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!selectedFile || thumbnails.length === 0) {
        setStatus("Selecciona un PDF.", true);
        return;
    }
    if (pages.length === 0) {
        setStatus("El PDF debe conservar al menos una página.", true);
        return;
    }

    const name = pagesOutputName.value.trim();
    if (!name) {
        setStatus("Escribe un nombre para el PDF resultante.", true);
        pagesOutputName.focus();
        return;
    }

    const formData = new FormData();
    formData.append("file", selectedFile, selectedFile.name);
    formData.append("pages", JSON.stringify(pages.map((page) => ({
        page: page.page,
        rotation: ((page.rotation % 360) + 360) % 360,
    }))));
    formData.append("output_name", name);
    setStatus("Guardando cambios...", false);

    try {
        const response = await fetch("/api/edit-pages", { method: "POST", body: formData });
        if (!response.ok) throw new Error((await response.json()).detail || "No se pudo crear el PDF.");

        downloadBlob(await response.blob(), name.toLowerCase().endsWith(".pdf") ? name : `${name}.pdf`);
        setStatus("PDF creado y descargado.", false);
    } catch (error) {
        setStatus(error.message, true);
    }
});

function setStatus(message, isError) {
    pagesStatus.textContent = message;
    pagesStatus.classList.toggle("is-error", isError);
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

renderPages();
