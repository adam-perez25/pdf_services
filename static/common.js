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
