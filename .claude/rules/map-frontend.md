---
paths:
  - "static/**"
  - "templates/**"
---
# Frontend map

- Static HTML, no framework, no build step. Every page links `/static/styles.css` and has a back link (`a.back-button` to `/`). `index.html` has one `a.service-card` per tool.
- Scripts load at the bottom of each template: `/static/common.js` first, then the page's own JS. They are plain globals (no modules).

  | Template | Script |
  |---|---|
  | merge | merge.js |
  | split | split.js |
  | images | images.js |
  | word, excel, powerpoint | office.js (shared) |
  | pages | pages.js (largest, ~450 lines) |
  | compress | compress.js |
  | pdf-to-images | pdf-to-images.js |
  | protect | protect.js (Proteger/Desbloquear mode select; password only in the multipart body) |
  | stamp | stamp.js (mode select toggles `#stamp-numbers-options` / `#stamp-watermark-options`) |
  | metadata | metadata.js (reads `/api/metadata/read` on file choice to prefill) |
  | workflow | workflow.js (chains tools client-side: posts each result Blob to the next `/api/*` endpoint; steps defined in `STEP_DEFINITIONS`, protect forced last, merge forced first with several PDFs) |
  | extract-text | extract-text.js (downloads the .txt, then fills the read-only `#extract-text-preview` textarea via `.value` with the first 2000 chars; counts from `X-Page-Count` / `X-Character-Count`) |
  | sign | sign.js (document store like pages.js; drawing pad canvas + PNG/JPEG upload; draggable/resizable `.sign-box` overlays on the page preview, arrow keys nudge) |

- `office.js` is configured by data attributes on `#office-form`: `data-endpoint`, `data-extensions` (comma-separated, no dots), `data-default-name`, `data-missing-file`. Element ids are the same on all three pages (`office-input`, `office-dropzone`, `office-file-name`, `office-output-name`, `office-status`). A new Office-like tool is just a template with different data attributes.
- `common.js` helpers:
  - Page setup: `setupDropzone`, `statusSetter` (toggles `.is-error`), `uploadProgress`.
  - Requests and downloads: `postForm(url, formData, progress)`, `errorMessage(response, fallback)`, `downloadName(response)` (reads Content-Disposition), `downloadBlob`.
  - Checks and formatting: `withPdfExtension`, `isPdf`, `isImage`, `escapeHtml`.
- Results download as a blob through a temporary `<a download>`. split.js and pdf-to-images.js check `blob.type === "application/zip"` because a single result comes back as a plain PDF or image.
- pages.js uses the document store:
  - upload once to `/api/documents`,
  - thumbnails from `/api/documents/{id}/pages/{n}?width=`,
  - save via `/api/documents/{id}/edit` (sign.js: `/api/documents/{id}/sign`),
  - `DELETE` with `keepalive` on unload,
  - re-upload on 410.
- CSP (set by the server): no inline `<script>` or handlers, no external hosts, images only from self or `blob:`. Fonts are local.
- sign.js sends placements as fractions of the page preview (pdfium renders it already rotated and cropped, so it matches the displayed page). Box height is derived from width, the page aspect and the signature aspect. The pad and the "Firma actual" preview use `color-scheme: light; background: Canvas` so dark ink stays visible in dark mode without hardcoded colours; `touch-action: none` on the pad and boxes stops page scroll while drawing/dragging.
- `/static` is served with `Cache-Control: no-cache`, so no cache-busting query strings are needed.
- File input `accept` attributes must match the backend (`ALLOWED_IMAGE_FORMATS`, Office extensions).
