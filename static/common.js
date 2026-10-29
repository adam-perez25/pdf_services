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
        onDrop([...event.dataTransfer.files]);
    });
}

function statusSetter(element) {
    return (message, isError) => {
        element.textContent = message;
        element.classList.toggle("is-error", isError);
    };
}

// A bar shown just above the page's status line while a file is uploading,
// then as a moving stripe while the server works on it.
function uploadProgress(statusElement) {
    const container = document.createElement("div");
    container.className = "upload-progress";
    container.hidden = true;
    container.innerHTML = `
        <div class="upload-progress-track" role="progressbar" aria-label="Progreso de la subida" aria-valuemin="0" aria-valuemax="100">
            <span class="upload-progress-bar"></span>
        </div>
        <span class="upload-progress-label"></span>
    `;
    statusElement.before(container);
    const track = container.querySelector(".upload-progress-track");
    const bar = container.querySelector(".upload-progress-bar");
    const label = container.querySelector(".upload-progress-label");

    return {
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

// Sends a form like fetch does, but with XMLHttpRequest because fetch cannot
// report upload progress. It resolves to a regular Response, so callers read
// it exactly as they would a fetch result.
function postForm(url, formData, progress) {
    return new Promise((resolve, reject) => {
        const request = new XMLHttpRequest();
        request.open("POST", url);
        request.responseType = "blob";
        request.upload.addEventListener("progress", (event) => {
            if (event.lengthComputable) progress?.uploading(event.loaded / event.total);
        });
        request.upload.addEventListener("load", () => progress?.processing());
        request.addEventListener("load", () => {
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
            progress?.hide();
            reject(new Error("No se pudo conectar con el servidor."));
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
    const encoded = header.match(/filename\*=utf-8''([^;]+)/i);
    if (encoded) return decodeURIComponent(encoded[1]);
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
