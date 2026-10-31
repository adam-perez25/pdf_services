// Firmar PDF: upload the PDF once to the document store (like the page
// organizer), draw or upload a signature, place it on pages of the preview and
// send the boxes to /api/documents/{id}/sign. Boxes are fractions (0-1) of the
// page as displayed, top-left origin; the server maps them onto the PDF.
const signFileInput = document.querySelector("#sign-file-input");
const signDropzone = document.querySelector("#sign-dropzone");
const signFileName = document.querySelector("#sign-file-name");
const signPageCount = document.querySelector("#sign-page-count");
const signMode = document.querySelector("#sign-mode");
const signDrawOptions = document.querySelector("#sign-draw-options");
const signUploadOptions = document.querySelector("#sign-upload-options");
const signPad = document.querySelector("#sign-pad");
const signColor = document.querySelector("#sign-color");
const signClear = document.querySelector("#sign-clear");
const signImageInput = document.querySelector("#sign-image-input");
const signImageDropzone = document.querySelector("#sign-image-dropzone");
const signImageName = document.querySelector("#sign-image-name");
const signCurrent = document.querySelector("#sign-current");
const signCurrentImage = document.querySelector("#sign-current-image");
const signPlacementCount = document.querySelector("#sign-placement-count");
const signEmpty = document.querySelector("#sign-empty");
const signEditor = document.querySelector("#sign-editor");
const signPrev = document.querySelector("#sign-prev");
const signNext = document.querySelector("#sign-next");
const signPageSelect = document.querySelector("#sign-page-select");
const signAdd = document.querySelector("#sign-add");
const signStage = document.querySelector("#sign-stage");
const signPageImage = document.querySelector("#sign-page-image");
const signPlacementList = document.querySelector("#sign-placements");
const signForm = document.querySelector("#sign-form");
const signOutputName = document.querySelector("#sign-output-name");
const signStatus = document.querySelector("#sign-status");
const setStatus = statusSetter(signStatus);
const progress = uploadProgress(signStatus);

const PREVIEW_WIDTH = 1200;
const INK_COLORS = { dark: "#1b1f24", blue: "#1f3f9e" };
// Pen width as a fraction of the pad width, so it looks the same when exported.
const PEN_WIDTH = 0.008;
const EXPORT_SCALE = 1000;
const MIN_BOX_WIDTH = 0.03;
const SIGNATURE_TYPES = ["image/png", "image/jpeg"];

let selectedFile = null;
let documentId = null;
let documentPages = 0;
let documentExpired = false;
let reuploading = null;
let currentPage = 1;
// Displayed width / height of each page, known once its preview has loaded.
const pageAspects = {};

let signature = null; // { blob, url, aspect, filename }
let uploadedSignature = null;
let strokes = [];
let activeStroke = null;
let placements = [];
let nextPlacementId = 1;
let selectedPlacement = null;
let boxDrag = null;

// ---------------------------------------------------------------- document

signFileInput.addEventListener("change", () => {
    if (signFileInput.files[0]) loadFile(signFileInput.files[0]);
});

setupDropzone(signDropzone, (files) => {
    const file = files.find(isPdf);
    if (file) loadFile(file);
});

async function loadFile(file) {
    selectedFile = file;
    discardDocument();
    documentExpired = false;
    documentPages = 0;
    placements = [];
    selectedPlacement = null;
    Object.keys(pageAspects).forEach((key) => delete pageAspects[key]);
    signFileName.textContent = file.name;
    signOutputName.value = file.name.replace(/\.pdf$/i, "") + "-firmado.pdf";
    renderEditor();
    setStatus("Subiendo el documento...", false);

    try {
        const uploaded = await uploadDocument(file);
        if (selectedFile !== file) {
            deleteDocument(uploaded.id);
            return;
        }
        documentId = uploaded.id;
        documentPages = uploaded.pages;
        currentPage = 1;
        renderPageOptions();
        renderEditor();
        showPage(1);
        setStatus("", false);
    } catch (error) {
        selectedFile = null;
        signFileName.textContent = "o arrástralo aquí";
        renderEditor();
        setStatus(error.message, true);
    }
}

async function uploadDocument(file) {
    const formData = new FormData();
    formData.append("file", file, file.name);
    const response = await postForm("/api/documents", formData, progress);
    if (!response.ok) throw new Error(await errorMessage(response, "No se pudo leer el PDF."));
    return response.json();
}

// The server deletes its copy after the download, after 30 minutes without use
// and on restart; the file is only sent again when the user keeps working.
function reuploadDocument() {
    if (!reuploading) {
        const file = selectedFile;
        reuploading = uploadDocument(file).then((uploaded) => {
            if (selectedFile !== file) {
                deleteDocument(uploaded.id);
                return;
            }
            documentId = uploaded.id;
            documentExpired = false;
        }).finally(() => { reuploading = null; });
    }
    return reuploading;
}

function reuploadIfExpired() {
    if (!documentExpired || !selectedFile || reuploading) return;
    reuploadDocument().then(() => showPage(currentPage)).catch((error) => setStatus(error.message, true));
}

function deleteDocument(id) {
    fetch(`/api/documents/${id}`, { method: "DELETE", keepalive: true }).catch(() => {});
}

function discardDocument() {
    if (documentId) deleteDocument(documentId);
    documentId = null;
}

window.addEventListener("pagehide", discardDocument);
window.addEventListener("pageshow", (event) => {
    if (!event.persisted || !selectedFile) return;
    documentExpired = true;
    reuploadIfExpired();
});

function renderPageOptions() {
    signPageSelect.innerHTML = "";
    for (let page = 1; page <= documentPages; page += 1) {
        const option = document.createElement("option");
        option.value = page;
        option.textContent = `${page} de ${documentPages}`;
        signPageSelect.appendChild(option);
    }
    signPageCount.textContent = `${documentPages} ${documentPages === 1 ? "página" : "páginas"}`;
}

function showPage(page) {
    if (!documentId || page < 1 || page > documentPages) return;
    if (page !== currentPage) selectedPlacement = null;
    currentPage = page;
    signPageSelect.value = String(page);
    signPrev.disabled = page === 1;
    signNext.disabled = page === documentPages;
    signPageImage.alt = `Página ${page}`;
    const url = `/api/documents/${documentId}/pages/${page}?width=${PREVIEW_WIDTH}`;
    if (signPageImage.getAttribute("src") !== url) {
        signStage.classList.add("is-loading");
        signPageImage.src = url;
    }
    renderBoxes();
    reuploadIfExpired();
}

signPageImage.addEventListener("load", () => {
    signStage.classList.remove("is-loading");
    if (signPageImage.naturalHeight > 0) {
        pageAspects[currentPage] = signPageImage.naturalWidth / signPageImage.naturalHeight;
    }
    renderBoxes();
});

signPageImage.addEventListener("error", async () => {
    const failedId = documentId;
    const check = await fetch(signPageImage.src).catch(() => null);
    if (check?.status === 410 && documentId === failedId) {
        documentExpired = true;
        reuploadIfExpired();
    }
});

signPrev.addEventListener("click", () => showPage(currentPage - 1));
signNext.addEventListener("click", () => showPage(currentPage + 1));
signPageSelect.addEventListener("change", () => showPage(Number(signPageSelect.value)));

// ---------------------------------------------------------------- signature

signMode.addEventListener("change", () => {
    const drawing = signMode.value === "draw";
    signDrawOptions.hidden = !drawing;
    signUploadOptions.hidden = drawing;
    if (drawing) {
        resizePad();
        useDrawing();
    } else {
        setSignature(uploadedSignature);
    }
});

function setSignature(value) {
    if (signature && signature !== uploadedSignature && signature.url) URL.revokeObjectURL(signature.url);
    signature = value;
    signCurrent.hidden = !signature;
    if (signature) {
        signCurrentImage.src = signature.url;
        fitPlacementsToSignature();
    }
    renderEditor();
}

// Keep each box's width and change its height to the new signature's shape.
function fitPlacementsToSignature() {
    placements.forEach((placement) => {
        placement.height = placement.width * placement.pageAspect / signature.aspect;
        if (placement.y + placement.height > 1) {
            placement.height = 1 - placement.y;
            placement.width = placement.height * signature.aspect / placement.pageAspect;
        }
    });
}

function imageAspect(url) {
    return new Promise((resolve, reject) => {
        const image = new Image();
        image.addEventListener("load", () => resolve(image.naturalWidth / image.naturalHeight));
        image.addEventListener("error", () => reject(new Error("No se pudo leer la imagen de la firma.")));
        image.src = url;
    });
}

async function loadSignatureImage(file) {
    if (!SIGNATURE_TYPES.includes(file.type)) {
        setStatus("La firma debe ser una imagen PNG o JPEG.", true);
        return;
    }
    const url = URL.createObjectURL(file);
    try {
        const aspect = await imageAspect(url);
        if (uploadedSignature) URL.revokeObjectURL(uploadedSignature.url);
        uploadedSignature = {
            blob: file,
            url,
            aspect,
            filename: file.type === "image/png" ? "firma.png" : "firma.jpg",
        };
        signImageName.textContent = file.name;
        if (signMode.value === "upload") setSignature(uploadedSignature);
        setStatus("", false);
    } catch (error) {
        URL.revokeObjectURL(url);
        setStatus(error.message, true);
    }
}

signImageInput.addEventListener("change", () => {
    if (signImageInput.files[0]) loadSignatureImage(signImageInput.files[0]);
});
setupDropzone(signImageDropzone, (files) => {
    const file = files.find((item) => SIGNATURE_TYPES.includes(item.type));
    if (file) loadSignatureImage(file);
    else setStatus("La firma debe ser una imagen PNG o JPEG.", true);
});

// Drawing pad. Points are stored as fractions of the pad width, so the drawing
// survives a resize and is exported at a fixed resolution.
const padContext = signPad.getContext("2d");

function resizePad() {
    const ratio = window.devicePixelRatio || 1;
    const width = signPad.clientWidth;
    const height = signPad.clientHeight;
    if (!width || !height) return;
    signPad.width = Math.round(width * ratio);
    signPad.height = Math.round(height * ratio);
    redrawPad();
}

function drawStrokes(context, list, scale, offsetX = 0, offsetY = 0) {
    context.strokeStyle = INK_COLORS[signColor.value];
    context.fillStyle = INK_COLORS[signColor.value];
    context.lineWidth = PEN_WIDTH * scale;
    context.lineCap = "round";
    context.lineJoin = "round";
    list.forEach((stroke) => {
        const points = stroke.map(([x, y]) => [(x - offsetX) * scale, (y - offsetY) * scale]);
        if (points.length === 1) {
            context.beginPath();
            context.arc(points[0][0], points[0][1], context.lineWidth / 2, 0, Math.PI * 2);
            context.fill();
            return;
        }
        context.beginPath();
        context.moveTo(...points[0]);
        for (let index = 1; index < points.length - 1; index += 1) {
            // Curve through the midpoints for a smoother line.
            const midX = (points[index][0] + points[index + 1][0]) / 2;
            const midY = (points[index][1] + points[index + 1][1]) / 2;
            context.quadraticCurveTo(points[index][0], points[index][1], midX, midY);
        }
        context.lineTo(...points[points.length - 1]);
        context.stroke();
    });
}

function redrawPad() {
    // The canvas is transparent; CSS gives the pad a light sheet in both
    // themes so dark ink stays readable. The exported signature has no background.
    padContext.clearRect(0, 0, signPad.width, signPad.height);
    drawStrokes(padContext, strokes, signPad.width);
}

function padPoint(event) {
    const rect = signPad.getBoundingClientRect();
    return [(event.clientX - rect.left) / rect.width, (event.clientY - rect.top) / rect.width];
}

signPad.addEventListener("pointerdown", (event) => {
    if ((event.button !== 0 && event.pointerType === "mouse") || !signPad.clientWidth) return;
    event.preventDefault();
    try { signPad.setPointerCapture(event.pointerId); } catch { /* pointer already gone */ }
    if (signPad.width !== Math.round(signPad.clientWidth * (window.devicePixelRatio || 1))) resizePad();
    activeStroke = { pointerId: event.pointerId, points: [padPoint(event)] };
    strokes.push(activeStroke.points);
    redrawPad();
});

signPad.addEventListener("pointermove", (event) => {
    if (!activeStroke || event.pointerId !== activeStroke.pointerId) return;
    event.preventDefault();
    const events = event.getCoalescedEvents ? event.getCoalescedEvents() : [event];
    (events.length ? events : [event]).forEach((item) => activeStroke.points.push(padPoint(item)));
    redrawPad();
});

function endStroke(event) {
    if (!activeStroke || event.pointerId !== activeStroke.pointerId) return;
    activeStroke = null;
    useDrawing();
}
signPad.addEventListener("pointerup", endStroke);
signPad.addEventListener("pointercancel", endStroke);

signClear.addEventListener("click", () => {
    strokes = [];
    redrawPad();
    useDrawing();
});

signColor.addEventListener("change", () => {
    redrawPad();
    useDrawing();
});

window.addEventListener("resize", resizePad);

// Crop the drawing to its ink and turn it into a transparent PNG.
function useDrawing() {
    if (signMode.value !== "draw") return;
    const points = strokes.flat();
    if (points.length === 0) {
        setSignature(null);
        return;
    }
    // A loop, not Math.min(...points): long drawings would overflow the stack.
    let [left, top] = points[0];
    let [right, bottom] = points[0];
    points.forEach(([x, y]) => {
        left = Math.min(left, x);
        top = Math.min(top, y);
        right = Math.max(right, x);
        bottom = Math.max(bottom, y);
    });
    const margin = PEN_WIDTH;
    left -= margin;
    top -= margin;
    right += margin;
    bottom += margin;
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.ceil((right - left) * EXPORT_SCALE));
    canvas.height = Math.max(1, Math.ceil((bottom - top) * EXPORT_SCALE));
    drawStrokes(canvas.getContext("2d"), strokes, EXPORT_SCALE, left, top);
    const aspect = canvas.width / canvas.height;
    const version = strokes;
    canvas.toBlob((blob) => {
        // Ignore a slow export that a newer drawing has replaced.
        if (!blob || version !== strokes || signMode.value !== "draw") return;
        setSignature({ blob, url: URL.createObjectURL(blob), aspect, filename: "firma.png" });
    }, "image/png");
}

// ---------------------------------------------------------------- placements

signAdd.addEventListener("click", () => {
    if (!signature) {
        setStatus("Primero dibuja o sube tu firma.", true);
        return;
    }
    const pageAspect = pageAspects[currentPage];
    if (!pageAspect) {
        setStatus("Espera a que cargue la página.", true);
        return;
    }
    let width = 0.3;
    let height = width * pageAspect / signature.aspect;
    if (height > 0.3) {
        height = 0.3;
        width = height * signature.aspect / pageAspect;
    }
    const placement = {
        id: nextPlacementId++,
        page: currentPage,
        x: (1 - width) / 2,
        y: Math.min(0.75, 1 - height),
        width,
        height,
        pageAspect,
    };
    placements.push(placement);
    selectedPlacement = placement.id;
    setStatus("", false);
    renderEditor();
    signStage.querySelector(`[data-id="${placement.id}"]`)?.focus();
    reuploadIfExpired();
});

function clamp(value, minimum, maximum) {
    return Math.min(Math.max(value, minimum), maximum);
}

function moveBox(placement, x, y) {
    placement.x = clamp(x, 0, 1 - placement.width);
    placement.y = clamp(y, 0, 1 - placement.height);
}

function resizeBox(placement, width) {
    // Keep the signature's shape and stay inside the page.
    const ratio = placement.pageAspect / signature.aspect;
    const maxWidth = Math.min(1 - placement.x, (1 - placement.y) / ratio);
    placement.width = clamp(width, Math.min(MIN_BOX_WIDTH, maxWidth), maxWidth);
    placement.height = placement.width * ratio;
}

function removePlacement(id) {
    placements = placements.filter((placement) => placement.id !== id);
    if (selectedPlacement === id) selectedPlacement = null;
    renderEditor();
}

function positionBox(box, placement) {
    // Set from script: the Content-Security-Policy blocks inline style attributes.
    box.style.left = `${placement.x * 100}%`;
    box.style.top = `${placement.y * 100}%`;
    box.style.width = `${placement.width * 100}%`;
    box.style.height = `${placement.height * 100}%`;
}

function renderBoxes() {
    signStage.querySelectorAll(".sign-box").forEach((box) => box.remove());
    if (!signature) return;
    placements.filter((placement) => placement.page === currentPage).forEach((placement) => {
        const box = document.createElement("div");
        box.className = "sign-box";
        box.classList.toggle("is-selected", placement.id === selectedPlacement);
        box.dataset.id = placement.id;
        box.tabIndex = 0;
        box.setAttribute("role", "group");
        box.setAttribute("aria-label", `Firma en la página ${placement.page}. Flechas para mover, + y - para el tamaño, Supr para quitarla.`);
        box.innerHTML = '<img alt="" draggable="false"><span class="sign-box-handle" aria-hidden="true"></span>';
        box.querySelector("img").src = signature.url;
        positionBox(box, placement);
        box.addEventListener("pointerdown", (event) => startBoxDrag(event, box, placement));
        box.addEventListener("keydown", (event) => boxKey(event, box, placement));
        box.addEventListener("focus", () => selectBox(placement.id));
        signStage.appendChild(box);
    });
}

function selectBox(id) {
    selectedPlacement = id;
    signStage.querySelectorAll(".sign-box").forEach((box) => {
        box.classList.toggle("is-selected", Number(box.dataset.id) === id);
    });
    signPlacementList.querySelectorAll(".sign-placement").forEach((item) => {
        item.classList.toggle("is-selected", Number(item.dataset.id) === id);
    });
}

function startBoxDrag(event, box, placement) {
    if (event.button !== 0 && event.pointerType === "mouse") return;
    event.preventDefault();
    box.focus({ preventScroll: true });
    try { box.setPointerCapture(event.pointerId); } catch { /* pointer already gone */ }
    boxDrag = {
        box,
        placement,
        pointerId: event.pointerId,
        resizing: event.target.classList.contains("sign-box-handle"),
        startX: event.clientX,
        startY: event.clientY,
        x: placement.x,
        y: placement.y,
        width: placement.width,
        rect: signStage.getBoundingClientRect(),
    };
    box.addEventListener("pointermove", moveBoxDrag);
    box.addEventListener("pointerup", endBoxDrag);
    box.addEventListener("pointercancel", endBoxDrag);
}

function moveBoxDrag(event) {
    if (!boxDrag || event.pointerId !== boxDrag.pointerId) return;
    event.preventDefault();
    const dx = (event.clientX - boxDrag.startX) / boxDrag.rect.width;
    const dy = (event.clientY - boxDrag.startY) / boxDrag.rect.height;
    if (boxDrag.resizing) resizeBox(boxDrag.placement, boxDrag.width + dx);
    else moveBox(boxDrag.placement, boxDrag.x + dx, boxDrag.y + dy);
    positionBox(boxDrag.box, boxDrag.placement);
}

function endBoxDrag(event) {
    if (!boxDrag || event.pointerId !== boxDrag.pointerId) return;
    const { box } = boxDrag;
    box.removeEventListener("pointermove", moveBoxDrag);
    box.removeEventListener("pointerup", endBoxDrag);
    box.removeEventListener("pointercancel", endBoxDrag);
    boxDrag = null;
}

function boxKey(event, box, placement) {
    const step = event.shiftKey ? 0.05 : 0.005;
    const moves = {
        ArrowLeft: [-step, 0],
        ArrowRight: [step, 0],
        ArrowUp: [0, -step],
        ArrowDown: [0, step],
    };
    if (moves[event.key]) {
        const [dx, dy] = moves[event.key];
        moveBox(placement, placement.x + dx, placement.y + dy);
    } else if (event.key === "+" || event.key === "=") {
        resizeBox(placement, placement.width * 1.05);
    } else if (event.key === "-" || event.key === "_") {
        resizeBox(placement, placement.width / 1.05);
    } else if (event.key === "Delete" || event.key === "Backspace") {
        event.preventDefault();
        removePlacement(placement.id);
        return;
    } else {
        return;
    }
    event.preventDefault();
    positionBox(box, placement);
}

function renderPlacementList() {
    signPlacementList.innerHTML = "";
    const sorted = [...placements].sort((a, b) => a.page - b.page || a.id - b.id);
    sorted.forEach((placement, index) => {
        const item = document.createElement("li");
        item.className = "sign-placement";
        item.classList.toggle("is-selected", placement.id === selectedPlacement);
        item.dataset.id = placement.id;
        item.innerHTML = `
            <span class="file-number">${String(index + 1).padStart(2, "0")}</span>
            <span class="file-name">Página ${placement.page}</span>
            <button class="tool-button" type="button" data-action="show">Ver</button>
            <button class="remove-button" type="button" data-action="remove" aria-label="Quitar la firma de la página ${placement.page}">&times;</button>
        `;
        item.querySelector('[data-action="show"]').addEventListener("click", () => {
            selectedPlacement = placement.id;
            showPage(placement.page);
            renderPlacementList();
            signStage.querySelector(`[data-id="${placement.id}"]`)?.focus();
        });
        item.querySelector('[data-action="remove"]').addEventListener("click", () => removePlacement(placement.id));
        signPlacementList.appendChild(item);
    });
    signPlacementCount.textContent = `${placements.length} ${placements.length === 1 ? "firma" : "firmas"}`;
}

function renderEditor() {
    const ready = Boolean(documentId || documentPages);
    signEditor.hidden = !ready;
    signEmpty.hidden = ready;
    signAdd.disabled = !signature;
    if (!ready) {
        signPageCount.textContent = "0 páginas";
        signPageImage.removeAttribute("src");
    }
    renderBoxes();
    renderPlacementList();
}

// ---------------------------------------------------------------- download

signForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!selectedFile || !documentId) {
        setStatus("Selecciona un PDF.", true);
        return;
    }
    if (!signature) {
        setStatus("Primero dibuja o sube tu firma.", true);
        return;
    }
    if (placements.length === 0) {
        setStatus("Coloca la firma en al menos una página.", true);
        return;
    }
    const name = signOutputName.value.trim();
    if (!name) {
        setStatus("Escribe un nombre para el PDF resultante.", true);
        signOutputName.focus();
        return;
    }

    const round = (value) => Math.round(value * 1e6) / 1e6;
    const formData = new FormData();
    formData.append("signature", signature.blob, signature.filename);
    formData.append("placements", JSON.stringify(placements.map((placement) => ({
        page: placement.page,
        x: round(placement.x),
        y: round(placement.y),
        width: round(placement.width),
        height: round(placement.height),
    }))));
    formData.append("output_name", name);
    setStatus("Firmando el PDF...", false);

    try {
        const send = () => fetch(`/api/documents/${documentId}/sign`, { method: "POST", body: formData });
        let response = await send();
        if (response.status === 410) {
            await reuploadDocument();
            response = await send();
        }
        if (!response.ok) throw new Error(await errorMessage(response, "No se pudo firmar el PDF."));
        // The server deleted its copy as part of the download.
        documentExpired = true;
        downloadBlob(await response.blob(), withPdfExtension(name));
        setStatus("PDF firmado y descargado.", false);
    } catch (error) {
        setStatus(error.message, true);
    }
});

resizePad();
renderEditor();
