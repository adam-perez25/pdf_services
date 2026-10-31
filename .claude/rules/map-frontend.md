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
  | extract-text | extract-text.js (downloads the .txt, then fills the read-only `#extract-text-preview` textarea via `.value` with the first 2000 chars; counts from `X-Page-Count` / `X-Character-Count`) |

- `office.js` is configured by data attributes on `#office-form`: `data-endpoint`, `data-extensions` (comma-separated, no dots), `data-default-name`, `data-missing-file`. Element ids are the same on all three pages (`office-input`, `office-dropzone`, `office-file-name`, `office-output-name`, `office-status`). A new Office-like tool is just a template with different data attributes.
- `common.js` helpers:
  - Page setup: `setupDropzone`, `statusSetter` (toggles `.is-error`), `uploadProgress`.
  - Requests and downloads: `postForm(url, formData, progress)`, `errorMessage(response, fallback)`, `downloadName(response)` (reads Content-Disposition), `downloadBlob`.
  - Checks and formatting: `withPdfExtension`, `isPdf`, `isImage`, `escapeHtml`.
- Results download as a blob through a temporary `<a download>`. split.js and pdf-to-images.js check `blob.type === "application/zip"` because a single result comes back as a plain PDF or image.
- pages.js uses the document store:
  - upload once to `/api/documents`,
  - thumbnails from `/api/documents/{id}/pages/{n}?width=`,
  - save via `/api/documents/{id}/edit`,
  - `DELETE` with `keepalive` on unload,
  - re-upload on 410.
- CSP (set by the server): no inline `<script>` or handlers, no external hosts, images only from self or `blob:`. Fonts are local.
- `/static` is served with `Cache-Control: no-cache`, so no cache-busting query strings are needed.
- File input `accept` attributes must match the backend (`ALLOWED_IMAGE_FORMATS`, Office extensions).
