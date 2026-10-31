# PDFService

Web PDF toolkit (FastAPI + Uvicorn) run with Docker Compose. Merge, split, compress, protect/unlock with a password, convert images/Office files to PDF, PDF to images, page editor, page numbers / watermark. The UI and README are in Spanish.

- Code: `app/main.py`. Templates in `templates/`, frontend assets in `static/`.
- Tests: `python -m pytest` (config in `pytest.ini`, tests in `tests/`, one file per tool). Dev deps in `requirements-dev.txt`.
- Docker: `docker compose up --build -d`, service `unir-pdf` on http://localhost:8000, health at `/health`. The container runs as a non-root user with `mem_limit: 1g`; tests are not part of the image.
- Office conversions need LibreOffice; Compress PDF needs Ghostscript. Tests that need them are skipped if missing.

## Commands available in this project

Remind the user of these when they fit what they are asking for:

- `/docker-verify [branch]` — **project-only.** Rebuild the container from `main` (or a branch), run the tests on the PC and inside Docker, check `/health`, and report which branch is live at localhost:8000. Suggest it after merging into `main`, or when the user asks whether the service works or what version is running.
- Global commands that are useful here: `/feature`, `/fix-bug`, `/test`, `/docs`, `/explain`, `/status`, `/security-audit` (uploads and LibreOffice/Ghostscript parse untrusted files), `/ideas`, `/release`, `/showcase`, `/ship`.

<!-- project-map: commit=abe2e39 updated=2026-10-31 -->
## Project map

### Overview
Spanish-language web PDF toolkit, `FastAPI(title="Unir PDF")` (`app/main.py:46`) served by uvicorn in Docker (`unir-pdf`, port 8000). No DB, no auth; optional env vars only for limits/timeouts (see Config below); only temp files. Deps: fastapi, pypdf, cryptography (pypdf AES-256), Pillow, pypdfium2, python-multipart, uvicorn; LibreOffice and Ghostscript as external tools.

### Layout
- `app/main.py`: the whole backend (pages, API, middlewares, helpers). `app/__init__.py` is empty.
- `templates/*.html`: `index.html` plus one plain HTML page per tool (no Jinja; `render_page` at main.py:203 reads the file on each request).
- `static/`: `common.js`, one JS per tool (`office.js` shared by word/excel/powerpoint), `styles.css`, `fonts/` (bundled woff2 + OFL).
- `tests/`: `conftest.py` and one file per tool. `.claude/skills/docker-verify/` holds the project command.

### Entry points and flow
- `uvicorn app.main:app`. `lifespan` (main.py:40) runs `start_document_sweeper` (507): purge DOCUMENTS_DIR, then a daemon thread `sweep_documents_forever` (498).
- Page routes `GET /`, `/merge`, `/split`, ... (main.py:83-135, `/protect` last) -> `render_page`.
- Tool request: `postForm()` in common.js -> multipart POST `/api/*`. Handler is a sync `def`, so it runs in a worker thread (main.py:224). It validates the extension and options, processes the file, and returns an attachment named by `clean_output_name` (216). `file.file.close()` runs in `finally`.
- Error codes: 400 bad input or password-protected PDF, 413 upload too big, 503 LibreOffice/Ghostscript missing, 504 timeout, 410 expired organizer document.
- Hardening helpers (after `clean_output_name`): every `/api` upload handler calls `check_upload_sizes` (413 over `MAX_UPLOAD_MB` per file / `MAX_TOTAL_UPLOAD_MB` per request; measured by seeking, not reading). Generic failures use `processing_error(msg, error)`: Spanish `detail` without the exception text, raw error logged. PDFs are opened with `open_pdf_reader` (pypdf) or `open_pdfium_checked` (pdfium), which raise 400 `ENCRYPTED_PDF_MESSAGE` for password-protected files (owner-only passwords still open).
- Outputs go to disk, not memory (big PDFs): pypdf tools (merge, split, split-zip parts, edit-pages/organizer edit, images-to-pdf, protect/unlock, stamp, metadata) call `write_pdf_to_file(writer)` -> `<job>.out.pdf` in DOCUMENTS_DIR and return `file_download(path, filename)` (FileResponse + explicit `Content-Disposition: attachment; filename="..."` + `BackgroundTask` unlink). Use these two helpers for new tools. Only pdf-info, thumbnails and single images stay in memory. `build_edited_pdf` (bytes) is kept as a small in-memory wrapper over `build_edited_writer`.
- Other job files in DOCUMENTS_DIR (from `new_job_paths(*suffixes)`), removed with a `BackgroundTask`: split-zip ZIP, pdf-to-images, compress, extract-text.
- Office: `convert_office_to_pdf` uses a `TemporaryDirectory` and a throwaway LibreOffice profile; the result is moved to a job file and streamed.
- Pass PdfReader an open file, never a path: pypdf reads a path fully into memory.

### Key modules (app/main.py)
- Middlewares: `UploadSizeLimit` (pure ASGI, added first so it runs innermost) answers 413 for `POST /api/*` whose Content-Length exceeds `max_request_bytes()` (`MAX_TOTAL_UPLOAD_MB` + `MULTIPART_OVERHEAD_MB`) and cuts off chunked bodies past it, before anything is spooled to disk. `revalidate_static_files` (53) sets no-cache on `/static`. `add_security_headers` (76) applies `SECURITY_HEADERS` (65): strict CSP, nosniff, DENY, no-referrer.
- External tools:
  - `find_soffice` (157) and `find_ghostscript` (161) locate the programs.
  - `run_tool` (165) wraps Popen. `kill_process_tree` (188) uses killpg on POSIX and `taskkill /T` on Windows. It is needed because `soffice` is only a launcher for `soffice.bin`.
- Names and ranges: `clean_base_name` (208), `clean_output_name` (216), `parse_page_ranges` (335), `unique_pdf_names` (354).
- Page organizer:
  - Helpers: `get_document_path` (458), `document_gone` (471), `open_pdfium_document` (479), `remove_expired_documents` (488), `build_edited_pdf` (513).
  - Endpoints at main.py:548-673.
- Images: `ALLOWED_IMAGE_FORMATS` (681), `a4_resolution`, `IMAGE_DPI_CHOICES` / `MAX_IMAGE_PIXELS` (748-751).
- Compress: `COMPRESSION_LEVELS` (951) maps strong/recommended/high to Ghostscript `/screen` / `/ebook` / `/printer`. pdfium validates the file first, and Ghostscript runs with `-dSAFER`.
- Protect: `/api/protect` and `/api/unlock` (end of main.py, after compress) use pypdf `PdfWriter(clone_from=reader)`; protect encrypts with `algorithm="AES-256"` (user = owner password) and rejects already-encrypted input, unlock rejects unencrypted input and answers 400 `Contraseña incorrecta`. `check_password` rejects empty or >`MAX_PASSWORD_BYTES` (127) passwords; unlock accepts an empty password so owner-only (restrictions) PDFs can be unlocked. Both endpoints also call `check_upload_sizes` and `processing_error`; stamp and metadata open PDFs with `open_pdf_reader`. Passwords are never logged nor echoed in errors; UI is `templates/protect.html` + `static/protect.js` (Proteger/Desbloquear select), tests in `tests/test_protect.py`.
- Stamp (page numbers / watermark): contiguous block at the end of main.py. `GET /stamp` and `POST /api/stamp` (`mode=numbers|watermark`). `STAMP_*` constants validate options (numeric fields arrive as strings so bad values give 400, not 422). `visible_page_box` returns a cm matrix that maps upright displayed coordinates to page space for /Rotate 0/90/180/270 (uses the cropbox). `stamp_overlay` builds an in-memory overlay page (standard Helvetica, WinAnsi, hex strings; ExtGState for opacity) merged with `page.merge_page`. `helvetica_width` uses an embedded AFM width table for centering. Watermark text must encode to cp1252; it shrinks to fit the width/diagonal. No new dependencies.
- Metadata: every edit drops /Info keys outside `METADATA_FIELDS` + `METADATA_KEPT_KEYS` (creator, producer, dates, /Trapped). `METADATA_FIELDS` / `MAX_METADATA_LENGTH` (500), `open_metadata_reader`, `POST /api/metadata/read` (JSON, prefills the form) and `POST /api/metadata` (pypdf clone; the 4 fields replace /Info values, empty = removed; XMP `/Metadata` always dropped; `remove_all` drops /Info; orphans removed with `compress_identical_objects` so old values are not written). Page `/metadata`, `metadata.js`.
- Config (top of main.py): `env_int(name, default, min, max)` reads `MAX_UPLOAD_MB` (500), `MAX_TOTAL_UPLOAD_MB` (1000, must be >= per-file), `DOCUMENT_TTL_SECONDS` (1800), `DOCUMENT_SWEEP_SECONDS` (300), and next to their code `OFFICE_TIMEOUT_SECONDS` (300), `COMPRESSION_TIMEOUT_SECONDS` (600) + `COMPRESSION_SECONDS_PER_PAGE` (2) capped by `COMPRESSION_MAX_TIMEOUT_SECONDS` (3600) via `compression_timeout(pages)`. Parsed once at import; invalid -> ValueError naming the variable (service does not start). Values live in module constants read at call time, so tests monkeypatch them. Defaults are written out in docker-compose.yml `environment:`; `mem_limit: 3g`.
- Workflow ("Flujo de trabajo"): only `GET /workflow` (last block of main.py) on the backend. The chaining is client-side in `static/workflow.js`: it posts each step's result Blob as `file` to the existing endpoints (`/api/merge` first when there are several PDFs, then `/api/stamp`, `/api/metadata`, `/api/compress`, `/api/protect`) with `postForm()`, downloads only the final result and offers the last good intermediate if a step fails. Steps are data-driven in `STEP_DEFINITIONS` (endpoint, `build(container)` -> `fill(formData)`, `last: true` for protect); a commented OCR entry shows how to add `/api/ocr`. Tests in `tests/test_workflow.py` check the JS only references registered `/api/*` routes and run the chain through the TestClient.
- Extract text (PDF to TXT): contiguous block at the end of main.py. `GET /extract-text` and `POST /api/extract-text` (`pages` in `parse_page_ranges` syntax, empty = all; `page_separator` in `TEXT_SEPARATORS`: `header` default `--- Página N ---`, `formfeed`, `none`; optional `output_name`). Copies the upload to DOCUMENTS_DIR, then under `PDFIUM_LOCK` streams `pdfium_page_text` (textpage `get_text_range`, `normalize_text` -> `\n`) page by page into `<job>.txt`, returned as `FileResponse` `text/plain; charset=utf-8` with `BackgroundTask` cleanup and `X-Page-Count` / `X-Character-Count` headers. All-whitespace result -> 400 `EMPTY_TEXT_MESSAGE` (scanned, use OCR at `/ocr`). `clean_text_output_name` = `clean_base_name` rules + `.txt`. Page `extract-text.html` + `extract-text.js` (read-only textarea preview of the first 2000 chars, set via `.value`).
- Sign (visual signature, not cryptographic): contiguous block at the very end of main.py (imports `ImageOps`/`NumberObject` locally). `GET /sign`, `POST /api/documents/{id}/sign` reuses the organizer store (404 bad id, 410 expired, deletes the upload after success like `/edit`). `load_signature_image` (PNG/JPEG only via `formats=SIGNATURE_IMAGE_FORMATS`, verify, `SIGNATURE_MAX_MB` 413, pixel cap, RGBA, thumbnail to `SIGNATURE_MAX_SIDE`), `parse_signature_placements` (JSON list of `{page,x,y,width,height}` as 0-1 fractions of the displayed page, top-left origin, `SIGNATURE_TOLERANCE`, max `SIGNATURE_MAX_PLACEMENTS`), `signature_xobject` (one Flate image XObject + `/SMask` for alpha, registered with `writer._add_object` because streams must be indirect), `signature_overlay` (uses `visible_page_box`, merged with `page.merge_page`).
- Health: `/health` (133) returns 503 only if it cannot write to DOCUMENTS_DIR. It reports `libreoffice` and `ghostscript` as booleans.

### Data and state
- `DOCUMENTS_DIR = <tmp>/pdf-toolkit-documents` (main.py:28) holds organizer uploads (`<uuid4 hex>.pdf`), job files and ZIPs.
- Ids are checked with `DOCUMENT_ID_PATTERN.fullmatch` (32 hex), which blocks path traversal.
- TTL is based on mtime: 30 min, swept every 5 min (configurable). `get_document_path` refreshes it with `os.utime`.
- `protect_from_sweeper(*paths)` registers in-flight job files in `ACTIVE_JOB_FILES` (under `ACTIVE_JOB_LOCK`); `remove_expired_documents` skips them, and on exit their mtime is refreshed so the download gets a full TTL. Wrap any long job's files in it.
- `POST /api/documents/{id}/edit` and `/sign` delete the upload after use. pages.js and sign.js also send `DELETE` on unload.
- `PDFIUM_LOCK` (33) wraps every pypdfium2 use, because pdfium is not thread-safe. pdf-to-images takes it per page (encoding runs outside), so long conversions do not block organizer thumbnails.
- Frontend state lives only in page JS variables.

### Conventions
- API endpoints are sync `def`; page routes are `async def`. Error `detail` strings, UI text and README are in Spanish.
- Multi-file output is a `ZIP_STORED` archive with parts named `<base>-page-NNN`. A single result is returned as the plain file instead.
- Work files go in DOCUMENTS_DIR or a `TemporaryDirectory`, never in the code dir. The container runs as uid 10001.
- Tests: one file per tool. Fixtures `client` and `documents_dir` point to a tmp dir. Tests needing LibreOffice or Ghostscript are `skipif` markers.

### Gotchas
- Keep in sync:
  - README API table and constants table <-> endpoints and constants in main.py.
  - A new tool needs a page route, `templates/x.html`, a card in `templates/index.html`, a test file and a README row.
  - A new single-file PDF tool can also join the workflow page with one entry in `STEP_DEFINITIONS` (static/workflow.js); if its endpoint is renamed, update workflow.js too (test_workflow checks the routes exist).
  - `ALLOWED_IMAGE_FORMATS` <-> `accept` in `templates/images.html:31`. The Office extensions <-> `accept`/`data-extensions` in word/excel/powerpoint.html.
  - Dockerfile copies only `app`, `static`, `templates`, so a new top-level dir needs a COPY.
- CSP forbids inline scripts and external hosts, so all JS goes in `static/`. `.woff2` mime is registered at main.py:49 because the slim image lacks it.
- Pillow images are opened with `formats=ALLOWED_IMAGE_FORMATS`, because EPS would invoke Ghostscript.
- The sweeper deletes anything in DOCUMENTS_DIR older than the TTL unless it is registered with `protect_from_sweeper`; a new long-running job must register its files.
- A new limit or timeout: use `env_int`, add it to docker-compose.yml `environment:` and the README config table, and test it in tests/test_large_pdfs.py.
- A container restart wipes organizer sessions; the UI recovers via 410 and re-upload.
- Compress returns the original file if the output is not smaller (`X-Compression-Applied: false`).
- FastAPI turns an empty optional form field into `None`, so `/api/metadata` uses `Form("")` and treats missing and empty the same (field removed).

Area detail: `.claude/rules/map-frontend.md` (static/templates), `.claude/rules/map-tests.md` (tests).
<!-- /project-map -->
