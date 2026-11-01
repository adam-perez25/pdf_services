// Helpers shared by every tool page. Each page loads this file before its own
// script, so these functions are available as globals.

function setupDropzone(dropzone, onDrop) {
    dropzone.addEventListener("dragover", (event) => {
        event.preventDefault();
        dropzone.classList.add("is-dragging");
    });
    dropzone.addEventListener("dragleave", () => dropzone.classList.remove("is-dragging"));
    dropzone.addEventListener("drop", (event) => {
        event.preventDefault();
        dropzone.classList.remove("is-dragging");
        markStep(2);
        onDrop([...event.dataTransfer.files]);
    });
}

// The "1 Elige, 2 Ajusta, 3 Descarga" strip at the top of a tool page
// (ol.steps). Choosing a file moves it to step 2; a "done" status (a finished
// download) marks step 3.
function markStep(number) {
    document.querySelectorAll(".steps").forEach((strip) => {
        [...strip.children].forEach((step, index) => {
            step.classList.toggle("is-done", index + 1 < number || (number === 3 && index === 2));
            if (index + 1 === number) step.setAttribute("aria-current", "step");
            else step.removeAttribute("aria-current");
        });
    });
}

document.addEventListener("change", (event) => {
    if (event.target.matches?.('input[type="file"]') && event.target.files.length) markStep(2);
});

// state: true for an error, "done" when the result was downloaded (shown as
// the result panel), false for progress and other messages.
function statusSetter(element) {
    return (message, state) => {
        element.textContent = message;
        element.classList.toggle("is-error", state === true);
        element.classList.toggle("is-done", state === "done" && Boolean(message));
        if (state === "done") markStep(3);
    };
}

// Reorderable lists (merge, images, workflow steps). Each movable item is an
// li with data-index and a dragHandle() button. The handle drags with mouse or
// touch, showing a line where the item will land, and moves the item with the
// arrow keys. move(from, to) changes the data and re-renders; canMove(from, to)
// can refuse a position; describe(index) names an item for screen readers.
function dragHandle(label) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "drag-handle";
    button.setAttribute("aria-label", `${label}. Usa las flechas arriba y abajo para moverlo`);
    button.innerHTML = '<span aria-hidden="true">&#10303;</span>';
    return button;
}

function reorderableList(list, { move, canMove = () => true, describe }) {
    const announcer = document.createElement("p");
    announcer.className = "visually-hidden";
    announcer.setAttribute("aria-live", "assertive");
    list.after(announcer);
    const line = document.createElement("div");
    line.className = "drop-line";
    line.hidden = true;
    let drag = null;

    const items = () => [...list.querySelectorAll(":scope > li[data-index]")];
    const indexOf = (handle) => Number(handle.closest("li[data-index]").dataset.index);

    function moveAndFocus(from, to) {
        if (to === from || to < 0 || to >= items().length || !canMove(from, to)) return false;
        move(from, to);
        list.querySelector(`:scope > li[data-index="${to}"] .drag-handle`)?.focus();
        announcer.textContent = `${describe(to)}: posición ${to + 1} de ${items().length}.`;
        return true;
    }

    list.addEventListener("keydown", (event) => {
        const handle = event.target.closest?.(".drag-handle");
        if (!handle || !["ArrowUp", "ArrowDown"].includes(event.key)) return;
        event.preventDefault();
        const from = indexOf(handle);
        moveAndFocus(from, from + (event.key === "ArrowUp" ? -1 : 1));
    });

    // Where the dragged item would land: the slot before the first item whose
    // middle is below the pointer, turned into the index after removal.
    function target(y) {
        const all = items();
        let slot = all.findIndex((item) => {
            const rect = item.getBoundingClientRect();
            return y < rect.top + rect.height / 2;
        });
        if (slot === -1) slot = all.length;
        return { slot, to: slot > drag.from ? slot - 1 : slot, all };
    }

    function showLine(y) {
        const { slot, to, all } = target(y);
        drag.to = to;
        if (to === drag.from || !canMove(drag.from, to)) {
            line.hidden = true;
            return;
        }
        const listRect = list.getBoundingClientRect();
        const edge = slot < all.length
            ? all[slot].getBoundingClientRect().top - 4
            : all[all.length - 1].getBoundingClientRect().bottom + 4;
        line.style.top = `${edge - listRect.top - 2}px`;
        line.hidden = false;
    }

    list.addEventListener("pointerdown", (event) => {
        const handle = event.target.closest?.(".drag-handle");
        if (!handle || event.button !== 0 || drag) return;
        event.preventDefault();
        // Keeps the moves and the release coming to the handle; it throws for
        // a pointer that is already gone, and the drag still works without it.
        try {
            handle.setPointerCapture(event.pointerId);
        } catch {}
        const item = handle.closest("li[data-index]");
        drag = { handle, item, from: Number(item.dataset.index), to: null, pointerId: event.pointerId };
        item.classList.add("is-dragging");
        document.body.classList.add("is-reordering");
        list.append(line);
    });

    list.addEventListener("pointermove", (event) => {
        if (!drag || event.pointerId !== drag.pointerId) return;
        if (event.clientY < 60) window.scrollBy(0, -10);
        else if (event.clientY > window.innerHeight - 60) window.scrollBy(0, 10);
        showLine(event.clientY);
    });

    function endDrag(event, drop) {
        if (!drag || event.pointerId !== drag.pointerId) return;
        const { from, to, item } = drag;
        drag = null;
        item.classList.remove("is-dragging");
        document.body.classList.remove("is-reordering");
        line.hidden = true;
        line.remove();
        if (drop && to !== null) moveAndFocus(from, to);
    }
    list.addEventListener("pointerup", (event) => endDrag(event, true));
    list.addEventListener("pointercancel", (event) => endDrag(event, false));
}

// A bar shown just above the page's status line while a file is uploading,
// then as a moving stripe while the server works on it, with a "Cancelar"
// button that postForm wires to the request in flight.
function uploadProgress(statusElement) {
    const container = document.createElement("div");
    container.className = "upload-progress";
    container.hidden = true;
    container.innerHTML = `
        <div class="upload-progress-track" role="progressbar" aria-label="Progreso de la subida" aria-valuemin="0" aria-valuemax="100">
            <span class="upload-progress-bar"></span>
        </div>
        <span class="upload-progress-label"></span>
        <button type="button" class="tool-button upload-progress-cancel">Cancelar</button>
    `;
    statusElement.before(container);
    const track = container.querySelector(".upload-progress-track");
    const bar = container.querySelector(".upload-progress-bar");
    const label = container.querySelector(".upload-progress-label");
    const cancelButton = container.querySelector(".upload-progress-cancel");
    let onCancel = null;
    cancelButton.addEventListener("click", () => {
        if (!onCancel) return;
        cancelButton.disabled = true;
        label.textContent = "Cancelando…";
        onCancel();
    });

    return {
        // Set by postForm while a request is in flight; null when it ends.
        setCancel(handler) {
            onCancel = handler;
            cancelButton.disabled = false;
        },
        uploading(fraction) {
            const percent = Math.round(fraction * 100);
            container.hidden = false;
            container.classList.remove("is-processing");
            bar.style.width = `${percent}%`;
            track.setAttribute("aria-valuenow", percent);
            label.textContent = `Subiendo ${percent} %`;
        },
        processing() {
            container.hidden = false;
            container.classList.add("is-processing");
            bar.style.width = "";
            track.removeAttribute("aria-valuenow");
            label.textContent = "Procesando…";
        },
        hide() {
            container.hidden = true;
        },
    };
}

// A random id the server can cancel the job by. crypto.randomUUID needs a
// secure context and the service is reached over plain http on the LAN.
function newJobId() {
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    return [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

// Sends a form like fetch does, but with XMLHttpRequest because fetch cannot
// report upload progress. It resolves to a regular Response, so callers read
// it exactly as they would a fetch result. The progress bar's "Cancelar"
// button aborts the request and tells the server to stop the job (closing
// the connection alone also stops it); the promise then rejects.
function postForm(url, formData, progress) {
    return new Promise((resolve, reject) => {
        const request = new XMLHttpRequest();
        const jobId = newJobId();
        let cancelled = false;
        request.open("POST", url);
        request.setRequestHeader("X-Job-Id", jobId);
        request.responseType = "blob";
        progress?.setCancel(() => {
            cancelled = true;
            fetch(`/api/jobs/${jobId}/cancel`, { method: "POST", keepalive: true }).catch(() => {});
            request.abort();
        });
        request.upload.addEventListener("progress", (event) => {
            if (event.lengthComputable) progress?.uploading(event.loaded / event.total);
        });
        request.upload.addEventListener("load", () => progress?.processing());
        request.addEventListener("load", () => {
            progress?.setCancel(null);
            progress?.hide();
            const headers = new Headers();
            request.getAllResponseHeaders().trim().split(/\r?\n/).forEach((line) => {
                const separator = line.indexOf(":");
                if (separator > 0) headers.append(line.slice(0, separator).trim(), line.slice(separator + 1).trim());
            });
            const body = [204, 205, 304].includes(request.status) ? null : request.response;
            resolve(new Response(body, { status: request.status, headers }));
        });
        const fail = () => {
            progress?.setCancel(null);
            progress?.hide();
            reject(new Error(cancelled ? "Trabajo cancelado." : "No se pudo conectar con el servidor."));
        };
        request.addEventListener("error", fail);
        request.addEventListener("abort", fail);
        request.send(formData);
    });
}

async function errorMessage(response, fallback) {
    // A crash or a proxy may answer with HTML, and FastAPI's validation errors
    // carry a list instead of a message; both fall back to the given text.
    try {
        const { detail } = await response.json();
        return typeof detail === "string" && detail ? detail : fallback;
    } catch {
        return fallback;
    }
}

function downloadName(response) {
    // Names with spaces or accents arrive encoded as filename*=utf-8''...
    const header = response.headers.get("Content-Disposition") || "";
    // filename* (RFC 6266) is preferred; filename="..." is only the ASCII fallback.
    const encoded = header.match(/filename\*=utf-8''([^;]+)/i);
    if (encoded) {
        try {
            return decodeURIComponent(encoded[1].trim());
        } catch {
            // A malformed encoding falls back to the plain name below.
        }
    }
    const plain = header.match(/filename="([^"]+)"/);
    return plain ? plain[1] : null;
}

function downloadBlob(blob, filename) {
    const downloadUrl = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = downloadUrl;
    link.download = filename;
    link.click();
    URL.revokeObjectURL(downloadUrl);
}

function withPdfExtension(name) {
    return name.toLowerCase().endsWith(".pdf") ? name : `${name}.pdf`;
}

function isPdf(file) {
    return file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf");
}

function isImage(file) {
    return file.type.startsWith("image/");
}

function escapeHtml(value) {
    return value.replace(/[&<>\"']/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#039;" })[character]);
}
