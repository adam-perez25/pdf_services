---
paths:
  - "static/**"
  - "templates/**"
---
# Frontend map

- Static HTML, no framework, no build step, no Jinja. Every page links `/static/styles.css` and has a back link (`a.back-button` to `/`, text "Menú"). `index.html` has one `a.service-card` per tool (16, numbered 01-16).
- Scripts load at the bottom of each template: `/static/common.js` first, then the page's own JS. They are plain globals (no modules).

  | Template | Script |
  |---|---|
  | merge | merge.js |
  | split | split.js |
  | images | images.js |
  | word, excel, powerpoint | office.js (shared) |
  | pages | pages.js (document store) |
  | compress | compress.js |
  | pdf-to-images | pdf-to-images.js (optional `#pdf-images-pages` sent as `pages`, cleared after a conversion; 400/504/507 shown via `errorMessage`) |
  | protect | protect.js (Proteger/Desbloquear select; password only in the multipart body) |
  | stamp | stamp.js (mode select toggles `#stamp-numbers-options` / `#stamp-watermark-options`) |
  | metadata | metadata.js (reads `/api/metadata/read` on file choice to prefill; truncates prefilled values to `maxLength`) |
  | workflow | workflow.js (see below) |
  | extract-text | extract-text.js (downloads the .txt, fills the read-only `#extract-text-preview` textarea via `.value` with the first 2000 chars; counts from `X-Page-Count` / `X-Character-Count`) |
  | sign | sign.js (largest, ~660 lines; document store like pages.js, drawing pad canvas + PNG/JPEG upload, draggable/resizable `.sign-box` overlays, arrow keys nudge) |
  | ocr | ocr.js (language select `#ocr-language`: spa / eng / spa+eng; ignores resubmits while a request runs) |

- `office.js` is configured by data attributes on `#office-form`: `data-endpoint`, `data-extensions` (comma-separated, no dots), `data-default-name` (Spanish: `documento.pdf`, `hoja-de-calculo.pdf`, `presentacion.pdf`), `data-missing-file`. Choosing a file prefills the name with its stem + `.pdf`. Element ids are the same on all three pages (`office-input`, `office-dropzone`, `office-file-name`, `office-output-name`, `office-status`). A new Office-like tool is just a template with different data attributes.
- `common.js` helpers:
  - Page setup: `setupDropzone`, `statusSetter` (toggles `.is-error`), `uploadProgress` (also holds a "Cancelar" button wired by `postForm` through `progress.setCancel`).
  - Requests and downloads: `postForm(url, formData, progress)` (sends a random `X-Job-Id`; Cancelar calls `/api/jobs/{id}/cancel` and aborts the XHR, rejecting with "Trabajo cancelado."), `errorMessage(response, fallback)`, `downloadName(response)` (prefers `filename*=UTF-8''...` over the ASCII `filename="..."` fallback, survives a malformed encoding), `downloadBlob`.
  - Checks and formatting: `withPdfExtension`, `isPdf`, `isImage`, `escapeHtml`.
- Results download as a blob through a temporary `<a download>`. split.js and pdf-to-images.js check `blob.type === "application/zip"` because a single result comes back as a plain PDF or image.
- Document store (pages.js and sign.js): upload once to `/api/documents`, thumbnails from `/api/documents/{id}/pages/{n}?width=`, save via `/api/documents/{id}/edit` (sign.js: `/api/documents/{id}/sign`), `DELETE` with `keepalive` on unload, re-upload on 410.
- workflow.js chains tools client-side: it posts each step's result Blob as `file` to the existing `/api/*` endpoints with `postForm()`, downloads only the final result and offers the last good intermediate if a step fails (cleared when files or steps change).
  - `MERGE_STEP` runs first when there are several PDFs.
  - `STEP_DEFINITIONS` entries are data-driven: `id`, `title`, `endpoint`, `working`, `failed`, optional `position`, `build(container)` -> `fill(formData)` (returns an error string or `""`), optional `afterResponse`, optional `clear`.
  - Positions: ocr `first` (before anything that adds text, which OCR would skip), numbers / watermark / compress normal, metadata `late` (after compress/OCR rewrite producer and dates), protect `last`. `POSITION_RANK` sorts them; `canMove` / `moveStep` only swap steps within the same group, and `addStep` puts a new step at the end of its group.
  - Helpers: `field`, `selectControl`, `numberControl`, `textControl`, `checkControl`.
- CSP (set by the server): no inline `<script>` or handlers, no external hosts, images only from self or `blob:`. Fonts are local (`static/fonts`).
- sign.js sends placements as fractions of the page preview (pdfium renders it rotated and cropped, so it matches). The pad and the "Firma actual" preview use `color-scheme: light; background: Canvas` so dark ink stays visible in dark mode; `touch-action: none` on the pad and boxes stops page scroll while drawing or dragging.
- Dark mode: all colors are CSS custom properties on `:root`, redefined in a `prefers-color-scheme: dark` block; every template has `<meta name="color-scheme" content="light dark">`. New CSS must use the variables (test_dark_mode rejects hardcoded colors).
- `/static` is served with `Cache-Control: no-cache`, so no cache-busting query strings are needed.
- File input `accept` attributes must match the backend (`ALLOWED_IMAGE_FORMATS` in `templates/images.html`, Office extensions in word/excel/powerpoint.html).
