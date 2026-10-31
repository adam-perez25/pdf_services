# PDFService

Web PDF toolkit (FastAPI + Uvicorn) run with Docker Compose. 16 tools: merge, split, images/Word/Excel/PowerPoint to PDF, page organizer, compress, PDF to images, protect/unlock, page numbers / watermark, metadata, OCR, extract text, sign, and a workflow page that chains them. The UI and README are in Spanish.

- Code: `app/main.py`. Templates in `templates/`, frontend assets in `static/`.
- Tests: `python -m pytest` (config in `pytest.ini`, tests in `tests/`, one file per tool). Dev deps in `requirements-dev.txt`.
- Docker: `docker compose up --build -d`, service `unir-pdf` on http://localhost:8000, health at `/health`. The container runs as non-root uid 10001 with `mem_limit: 3g`, `init`, `cap_drop: ALL`, `no-new-privileges` and `pids_limit: 512`; tests are not part of the image.
- Office conversions need LibreOffice; Compress PDF needs Ghostscript; OCR needs the `ocrmypdf` package plus Tesseract (spa, eng). Tests that need them are skipped if missing.

## Commands available in this project

Remind the user of these when they fit what they are asking for:

- `/docker-verify [branch]` — **project-only.** Rebuild the container from `main` (or a branch), run the tests on the PC and inside Docker, check `/health`, and report which branch is live at localhost:8000. Suggest it after merging into `main`, or when the user asks whether the service works or what version is running.
- Global commands that are useful here: `/feature`, `/fix-bug`, `/test`, `/docs`, `/explain`, `/status`, `/security-audit` (uploads and LibreOffice/Ghostscript/Tesseract parse untrusted files), `/ideas`, `/release`, `/showcase`, `/ship`.

<!-- project-map: commit=14ff1ad updated=2026-11-01 -->
## Project map

### Overview
Spanish-language web PDF toolkit: `FastAPI(title="Unir PDF")` in `app/main.py` (`docs_url`/`redoc_url`/`openapi_url` disabled), served by uvicorn in Docker (`unir-pdf`, port 8000). No DB, no auth, only temp files; optional env vars only for limits/timeouts (see Config). Python deps (`requirements.txt`, pinned): fastapi, starlette, uvicorn, pypdf, cryptography (AES-256), Pillow, pypdfium2, python-multipart, ocrmypdf. The Dockerfile apt-installs LibreOffice (writer/calc/impress), Ghostscript and Tesseract (spa, eng, osd). `requirements-dev.txt` adds httpx, pytest, openpyxl, python-pptx.

### Layout
- `app/main.py` (~3200 lines): the whole backend (pages, API, middlewares, helpers). `app/__init__.py` is empty.
- `templates/*.html`: `index.html` plus one plain HTML page per tool (no Jinja; `render_page` reads the file on each request).
- `static/`: `common.js`, one JS per tool (`office.js` shared by word/excel/powerpoint), `styles.css`, `fonts/`. Detail: `.claude/rules/map-frontend.md`.
- `tests/`: `conftest.py` plus one file per tool and per cross-cutting concern. Detail: `.claude/rules/map-tests.md`.
- `.claude/skills/docker-verify/` is the project command. `README.md` (Spanish) has the API and config tables; `ROADMAP.md` lists done work and ideas.
- Docker: the Dockerfile copies only `app`, `static`, `templates`, runs as uid 10001 and has a Python `HEALTHCHECK` on `/health`. `docker-compose.yml` sets `mem_limit: 3g`, `init: true`, `cap_drop: [ALL]`, `no-new-privileges`, `pids_limit: 512` and an `environment:` block with every default.

### Entry points and flow
- `uvicorn app.main:app`. `lifespan` runs `start_document_sweeper`: purge DOCUMENTS_DIR, then a daemon thread `sweep_documents_forever`.
- Page routes (`/`, `/merge`, `/split`, `/images`, `/word`, `/excel`, `/powerpoint`, `/pages`, `/compress`, `/pdf-to-images`, `/protect`, `/metadata`, `/stamp`, `/workflow`, `/extract-text`, `/sign`, `/ocr`) are `async def` and call `render_page`.
- Tool request: `postForm()` in common.js sends a multipart POST to `/api/*`; the handler is a sync `def` (worker thread). Order inside a handler: validate extension/options -> `check_upload_sizes` -> open the PDF with `open_pdf_reader` / `open_pdfium_checked` -> process inside `with heavy_job(...)` -> return `file_download(path, name)`; `file.file.close()` in `finally`.
- Error codes: 400 bad input, password-protected PDF (`ENCRYPTED_PDF_MESSAGE`) and every `RequestValidationError` (the `validation_error` handler turns FastAPI's English 422 into a Spanish 400 "Falta el campo «x» o su valor no es válido."); 403 cross-site request; 404 bad document id or page; 410 expired organizer/sign document; 413 upload too big; 503 LibreOffice/Ghostscript/OCR missing or `heavy_job` busy (with `Retry-After`); 504 timeout; 507 pdf-to-images output does not fit in the temp disk.
- Outputs go to disk, not memory: pypdf tools (merge, split, split-zip parts, edit-pages/organizer edit, images-to-pdf, protect/unlock, stamp, metadata, sign) call `write_pdf_to_file(writer)` (`<job>.out.pdf` in DOCUMENTS_DIR) and return `file_download(path, filename, ...)` (FileResponse + `content_disposition(filename)` + `BackgroundTask` unlink). Use these two helpers for new tools. Only pdf-info, thumbnails and single images stay in memory. `pdf_attachment` is an unused leftover; `build_edited_pdf` (bytes) wraps `build_edited_writer`.
- Other job files come from `new_job_paths(*suffixes)`: split-zip ZIP, pdf-to-images, compress and OCR `.in/.out.pdf`, extract-text `.txt`.
- Pass PdfReader an open file, never a path: pypdf reads a path fully into memory.

### Key modules (app/main.py)
- Middlewares: `UploadSizeLimit` is added first, then `CrossSiteRequestGuard`, then the `@app.middleware` decorators (`revalidate_static_files`, `add_security_headers`). So the guard runs outside `UploadSizeLimit` (a cross-site upload is refused before its body is read) and inside the security-headers middleware (its 403 still gets the headers).
  - `UploadSizeLimit` (pure ASGI) answers 413 for `POST /api/*` over `max_request_bytes()` (`MAX_TOTAL_UPLOAD_MB` + `MULTIPART_OVERHEAD_MB`) and cuts off chunked bodies past it.
  - `CrossSiteRequestGuard` answers 403 `CROSS_SITE_MESSAGE` for POST/PUT/PATCH/DELETE on `/api/*` when `Sec-Fetch-Site` is cross-site/same-site, or, without that header, when `Origin` is present, not `null` and does not match `Host`. No headers (curl, scripts, tests) and `Origin: null` (browsers send it because of `Referrer-Policy: no-referrer`) are allowed.
  - `revalidate_static_files` sets no-cache on `/static`; `add_security_headers` applies `SECURITY_HEADERS` (strict CSP, nosniff, DENY, no-referrer).
- Upload and PDF helpers: `check_upload_sizes` (measures by seeking, `upload_size`; `MAX_UPLOAD_MB` per file, `MAX_TOTAL_UPLOAD_MB` per request); `processing_error(msg, error)` (Spanish 400, raw error only logged); `open_pdf_reader` / `open_pdfium_checked` (400 for password-protected PDFs; owner-only passwords still open); `strip_before_pdf_header(path)` (removes bytes before `%PDF-` within the first 1024, 400 `NOT_A_PDF_MESSAGE` without a header) runs before Ghostscript (compress) and ocrmypdf (OCR), because pdfium accepts a `%!PS` prefix that Ghostscript would execute.
- Download names: `clean_base_name` (NFC, keeps Unicode letters/digits and `. _ -` space, everything else `_`, stem capped at `MAX_OUTPUT_NAME_LENGTH` 150), `clean_output_name` (adds `.pdf`), `unique_pdf_names` (ZIP entries, empty names fall back to their defaults), `content_disposition(filename)` (ASCII `filename="..."` fallback plus `filename*=UTF-8''...`). Never hand-build `Content-Disposition`.
- Page lists and JSON: `parse_page_ranges`; `page_list_limit(pages)` = max(`PAGE_LIST_FACTOR` 10 x pages, `PAGE_LIST_MIN_LIMIT` 100) with `check_page_list_total` (split-zip ranges, also capped at `SPLIT_MAX_RANGES` 500; organizer layout; extract-text); `PAGE_SELECTION_MAX_LENGTH` (1000 chars) for page fields; `selected_page_numbers` (pdf-to-images, duplicates dropped); `parse_user_json(value, message)` (json.loads where bad JSON or `RecursionError` gives 400; used for split-zip names, organizer pages, sign placements).
- External tools: `find_soffice`, `find_ghostscript`, `find_tesseract`; `run_tool(cmd, timeout, env)` wraps Popen and on timeout calls `kill_process_tree` (killpg on POSIX, `taskkill /T` on Windows; `soffice` is only a launcher for `soffice.bin`).
- Heavy job limiter (block after `file_download`): `HEAVY_JOB_BUDGET_MB` is a memory budget shared by running jobs; `HeavyJobLimiter` (Condition, strict FIFO tickets) lets a job run if it fits or nothing else runs. `with heavy_job(job_estimate_mb(tool, *sizes, minimum_mb=0), uploads)` wraps only the processing, after the cheap validations; past `HEAVY_JOB_WAIT_SECONDS` it closes `uploads` and answers 503 + `Retry-After`, and more than `HEAVY_JOB_MAX_WAITING` waiters get 503 at once. Estimates under `HEAVY_JOB_MIN_MB` (100) skip it; files under `HEAVY_JOB_SMALL_FILES_MB` (50) in total give 0 (or `minimum_mb`). Per-tool `(factor, fixed MB)` in `HEAVY_JOB_ESTIMATES`; `decoded_image_mb(pixels)` is the floor for images-to-pdf and sign, so a tiny pixel bomb still queues. After a reservation `release_free_memory()` schedules `gc.collect()` + glibc `malloc_trim(0)` `MALLOC_TRIM_DELAY_SECONDS` (2 s) later (one pending timer covers several jobs; no-op without glibc). Not used by cheap endpoints (pdf-info, documents upload, thumbnails, metadata read).
- Page organizer: helpers `get_document_path`, `document_gone`, `open_pdfium_document`, `remove_expired_documents`, `build_edited_writer` (page and rotation must be ints, `/Rotate` written modulo 360). Endpoints `/api/documents` (upload), `GET /api/documents/{id}/pages/{n}?width=` (JPEG thumbnail; width clamped 100-2000; `thumbnail_scale` caps `THUMBNAIL_MAX_PIXELS` 8M, `THUMBNAIL_MAX_SIDE` 16000), `POST /edit`, `DELETE`, `POST /api/edit-pages`.
- Images: `ALLOWED_IMAGE_FORMATS` (PNG, JPEG, WEBP, GIF, BMP, TIFF); `IMAGE_MAX_PIXELS` (100M, also `Image.MAX_IMAGE_PIXELS`; `DecompressionBombWarning` is an error); `image_pixels(file, formats, invalid)` reads the header only, before decoding. images-to-pdf: `image_page(frame)` (EXIF transpose when needed, transparency painted white), multi-page TIFF -> one page per frame (each frame size-checked, Pillow only checks the first), other formats first frame only, `a4_resolution`. pdf-to-images: `IMAGE_DPI_CHOICES`, `MAX_IMAGE_PIXELS` (40M per page, downscale via `render_scale`).
- PDF to images guard (before `/api/pdf-to-images`): optional `pages` (`selected_page_numbers`; names keep the real page number; one page -> plain image). Before rendering, the total pixels are summed under `PDFIUM_LOCK` and `check_pdf_to_images_budget` answers 400 over `PDF_TO_IMAGES_MAX_MEGAPIXELS` or 507 when the estimated bytes (`IMAGE_BYTES_PER_PIXEL` png 0.5, jpg 0.2) + `PDF_TO_IMAGES_MIN_FREE_MB` exceed the free space of DOCUMENTS_DIR. `pdf_to_images_timeout(pages)` is checked between pages (504, job files removed); ENOSPC anywhere (`is_disk_full`) -> 507 `DISK_FULL_IMAGES_MESSAGE`.
- Office (word/excel/powerpoint): `convert_office_to_pdf` runs `check_office_content` (empty file -> 400; .docx/.xlsx/.pptx must start with a ZIP or OLE signature; legacy .doc/.xls/.ppt only checked for empty), then `heavy_job("office")`; in a `TemporaryDirectory` it calls `write_libreoffice_profile(profile)` and runs soffice with `-env:UserInstallation=<profile>`; the result is moved to a job file and streamed. `LIBREOFFICE_PROFILE_SETTINGS` (`user/registrymodifications.xcu`) is the defense against documents that link external/local images: `BlockUntrustedRefererLinks` alone blocks http and file:/// links; `DisableActiveContent`, never-update links and a dead proxy (127.0.0.1:9) are defense in depth. `office_output_name`: an empty name or an old English default (`OFFICE_OLD_DEFAULT_NAMES`) means "use the upload's stem".
- Compress: `COMPRESSION_LEVELS` maps strong/recommended/high to Ghostscript `/screen` / `/ebook` / `/printer` with `-dSAFER`. Job file -> `strip_before_pdf_header` -> pdfium check -> Ghostscript under `compression_timeout(pages)`. Returns the original if the output is not smaller (`X-Compression-Applied: false`).
- Protect/unlock: pypdf `PdfWriter(clone_from=reader)`; protect uses AES-256 (user = owner password) and rejects already-encrypted input; unlock rejects unencrypted input and answers 400 `Contraseña incorrecta`. `check_password` rejects empty or >`MAX_PASSWORD_BYTES` (127) passwords for protect; unlock accepts an empty password (owner-only PDFs). Passwords are never logged or echoed.
- Stamp (`mode=numbers|watermark`): `STAMP_*` constants validate options (numeric fields arrive as strings; rotation "45.0" accepted). `visible_page_box` returns a cm matrix for /Rotate 0/90/180/270 using the cropbox; `stamp_overlay` builds an in-memory overlay (Helvetica, WinAnsi, ExtGState opacity) merged with `merge_page`; `helvetica_width` uses an embedded AFM table. Page numbers leave room for descenders and cap height; the watermark shrinks so its rotated box fits the page; its text must encode to cp1252.
- Metadata: `METADATA_FIELDS`, `MAX_METADATA_LENGTH` (500, errors name the field in Spanish), `METADATA_KEPT_KEYS`. `POST /api/metadata/read` returns JSON to prefill the form; `POST /api/metadata` clones with pypdf, the 4 fields replace /Info values (empty = removed), drops the old XMP and writes a fresh one with `build_xmp(info)` (+ `pdf_date_to_xmp`), because Adobe and the Windows property handler show XMP rather than /Info. `remove_all` drops /Info and every XMP stream (document and page level). Orphans removed with `compress_identical_objects(remove_unreferenced=True)`.
- Extract text: `POST /api/extract-text` (`pages`, `page_separator` in `TEXT_SEPARATORS`: `header` default, `formfeed`, `none`; `output_name`) streams `pdfium_page_text` page by page into `<job>.txt` (`text/plain; charset=utf-8`, `X-Page-Count` / `X-Character-Count`). All-whitespace -> 400 `EMPTY_TEXT_MESSAGE` (use OCR).
- Sign (visual, not cryptographic): `POST /api/documents/{id}/sign` reuses the organizer store (404, 410, deletes the upload after success). `check_signature_upload` (header check, outside the queue) and `load_signature_image` (PNG/JPEG via `SIGNATURE_IMAGE_FORMATS`, `SIGNATURE_MAX_MB` 10, pixel cap, blank image rejected, thumbnail to `SIGNATURE_MAX_SIDE` 2000, decoded inside `heavy_job`); `parse_signature_placements` (fractions with top-left origin, `SIGNATURE_MAX_PLACEMENTS` 200, `SIGNATURE_TOLERANCE`); `signature_xobject` (Flate image + `/SMask`, `writer._add_object`) and `signature_overlay` (uses `visible_page_box`); `sign_to_file` writes the result.
- OCR: `POST /api/ocr`, `language` in `OCR_LANGUAGES` (spa, eng, spa+eng), `ocr_available()` = tesseract + the ocrmypdf module. Flow: `heavy_job("ocr")` + `protect_from_sweeper` -> `strip_before_pdf_header` -> pdfium page count (`OCR_MAX_PAGES` -> 400) -> `pages_with_images` -> `ocr_command`: pages that draw images get `--redo-ocr --pages <page_list>` (scanned pages with a stamped number are still read; digital pages are copied unchanged); no image pages or an AcroForm (`has_form_fields`) -> `--skip-text`; if `--redo-ocr` exits with an unknown code it retries once with `--skip-text`. `--rotate-pages` always (Tesseract osd), plus `--output-type pdf --optimize 0 --jobs OCR_JOBS --quiet`, with TMPDIR/TEMP/TMP pointed at a private `TemporaryDirectory`. Exit codes: 8 -> `ENCRYPTED_PDF_MESSAGE`; 3/9 -> 503 `OCR_MISSING_MESSAGE`; others -> `processing_error`. `ocr_timeout(pages)` = base + per page, capped.
- Workflow: only `GET /workflow` on the backend; the chaining is client-side (see the frontend rule).
- Health: `/health` returns 503 only if DOCUMENTS_DIR is not writable; it reports `storage`, `libreoffice`, `ghostscript`, `ocr`, and `heavy_jobs` (`running`, `reserved_mb`, `waiting`).
- Config: `env_int(name, default, min, max)`, parsed once at import (invalid -> ValueError naming the variable, the service does not start); values live in module constants read at call time, so tests monkeypatch them. Defaults are written out in docker-compose.yml.

  | Group | Variables (defaults) |
  |---|---|
  | Uploads | `MAX_UPLOAD_MB` 500, `MAX_TOTAL_UPLOAD_MB` 1000 (>= per-file; unset, follows a raised per-file limit) |
  | Documents | `DOCUMENT_TTL_SECONDS` 1800, `DOCUMENT_SWEEP_SECONDS` 300 |
  | Office | `OFFICE_TIMEOUT_SECONDS` 300 |
  | Compress | `COMPRESSION_TIMEOUT_SECONDS` 600, `COMPRESSION_SECONDS_PER_PAGE` 2, `COMPRESSION_MAX_TIMEOUT_SECONDS` 3600 |
  | OCR | `OCR_MAX_PAGES` 2000, `OCR_TIMEOUT_SECONDS` 60, `OCR_SECONDS_PER_PAGE` 20, `OCR_MAX_TIMEOUT_SECONDS` 14400 |
  | Heavy jobs | `HEAVY_JOB_BUDGET_MB` 2000, `HEAVY_JOB_WAIT_SECONDS` 300, `HEAVY_JOB_MAX_WAITING` 10 |
  | PDF to images | `PDF_TO_IMAGES_MAX_MEGAPIXELS` 10000, `PDF_TO_IMAGES_MIN_FREE_MB` 512, `PDF_TO_IMAGES_TIMEOUT_SECONDS` 300, `PDF_TO_IMAGES_SECONDS_PER_PAGE` 3, `PDF_TO_IMAGES_MAX_TIMEOUT_SECONDS` 7200 |

### Data and state
- `DOCUMENTS_DIR = <tmp>/pdf-toolkit-documents` holds organizer/sign uploads (`<uuid4 hex>.pdf`), job files and ZIPs. Ids are checked with `DOCUMENT_ID_PATTERN.fullmatch` (32 hex), which blocks path traversal.
- TTL is based on mtime (30 min, swept every 5 min, configurable); `get_document_path` refreshes it with `os.utime`.
- `protect_from_sweeper(*paths)` registers in-flight job files in `ACTIVE_JOB_FILES` (under `ACTIVE_JOB_LOCK`); `remove_expired_documents` skips them, and on exit the mtime is refreshed so the download gets a full TTL. Used by `write_pdf_to_file`, compress, OCR, pdf-to-images, extract-text, organizer edit and sign.
- `POST /api/documents/{id}/edit` and `/sign` delete the upload after use; pages.js and sign.js also send `DELETE` on unload. A container restart wipes organizer sessions; the UI recovers via 410 and re-upload.
- `PDFIUM_LOCK` wraps every pypdfium2 use, because pdfium is not thread-safe. pdf-to-images takes it per page (encoding runs outside), so thumbnails stay responsive.
- Frontend state lives only in page JS variables.

### Conventions
- API endpoints are sync `def`; page routes are `async def`. Error `detail` strings, UI text and README are in Spanish.
- Multi-file output is a `ZIP_STORED` archive with parts named `<base>-page-NNN` (4 digits from 1000 pages). A single result is returned as the plain file instead.
- Work files go in DOCUMENTS_DIR or a `TemporaryDirectory`, never in the code dir.
- Tests: one file per tool. Fixtures `client` and `documents_dir` point to a tmp dir. Tests needing LibreOffice, Ghostscript or Tesseract are `skipif` markers.

### Gotchas
- Keep in sync:
  - README API table and config table <-> endpoints and constants in main.py.
  - A new tool needs a page route, `templates/x.html`, a card in `templates/index.html`, a test file and a README row.
  - A new single-file PDF tool can join the workflow with one `STEP_DEFINITIONS` entry in `static/workflow.js` (choose its `position`); if its endpoint is renamed, update workflow.js too (test_workflow checks every referenced `/api/...` route exists).
  - `ALLOWED_IMAGE_FORMATS` <-> `accept` in `templates/images.html`; the Office extensions <-> `accept`/`data-extensions` in word/excel/powerpoint.html.
  - Dockerfile copies only `app`, `static`, `templates`, so a new top-level dir needs a COPY.
- CSP forbids inline scripts and external hosts, so all JS goes in `static/`. `.woff2` mime is registered with `mimetypes.add_type` because the slim image lacks it.
- New state-changing routes must live under `/api/` to be covered by the cross-site guard; tests exercise it with `Sec-Fetch-Site: cross-site` or a foreign `Origin`.
- Pillow images are opened with `formats=` (`ALLOWED_IMAGE_FORMATS` / `SIGNATURE_IMAGE_FORMATS`), because EPS would invoke Ghostscript; check sizes with `image_pixels` (and per frame) before decoding.
- Anything handed to Ghostscript or ocrmypdf must first go through `strip_before_pdf_header`. LibreOffice must always get a profile seeded by `write_libreoffice_profile`.
- Page-list inputs that can repeat pages must call `check_page_list_total`; JSON form fields must use `parse_user_json`; renders and thumbnails go through `render_scale` / `thumbnail_scale`.
- Download headers: use `file_download` / `content_disposition` with names from `clean_base_name` / `clean_output_name`.
- The sweeper deletes anything in DOCUMENTS_DIR older than the TTL unless it is registered with `protect_from_sweeper`; a new long-running job must register its files.
- A new heavy `/api` endpoint must wrap its processing in `with heavy_job(job_estimate_mb(...), uploads)` (add its row to `HEAVY_JOB_ESTIMATES`), after validations; where a broad `except Exception` turns errors into `processing_error`, keep an `except HTTPException: raise` before it so the 503 is not turned into a 400.
- A new limit or timeout: use `env_int`, add it to docker-compose.yml `environment:` and the README config table, and test it in tests/test_large_pdfs.py.
- Metadata edits must regenerate XMP with `build_xmp`, or viewers that read XMP show stale values. FastAPI turns an empty optional form field into `None`, so use `Form("")`.
- `malloc_trim` only helps on glibc (the Docker image); memory does not drop on Windows runs.

Area detail: `.claude/rules/map-frontend.md` (static/templates), `.claude/rules/map-tests.md` (tests).
