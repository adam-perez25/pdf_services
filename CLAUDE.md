# PDFService

Web PDF toolkit (FastAPI + Uvicorn) run with Docker Compose. Merge, split, compress, convert images/Office files to PDF, PDF to images, page editor. The UI and README are in Spanish.

- Code: `app/main.py`. Templates in `templates/`, frontend assets in `static/`.
- Tests: `python -m pytest` (config in `pytest.ini`, tests in `tests/`, one file per tool). Dev deps in `requirements-dev.txt`.
- Docker: `docker compose up --build -d`, service `unir-pdf` on http://localhost:8000, health at `/health`. The container runs as a non-root user; tests are not part of the image.
- Office conversions need LibreOffice; Compress PDF needs Ghostscript. Tests that need them are skipped if missing.

## Commands available in this project

Remind the user of these when they fit what they are asking for:

- `/docker-verify [branch]` — **project-only.** Rebuild the container from `main` (or a branch), run the tests on the PC and inside Docker, check `/health`, and report which branch is live at localhost:8000. Suggest it after merging into `main`, or when the user asks whether the service works or what version is running.
- Global commands that are useful here: `/feature`, `/fix-bug`, `/test`, `/docs`, `/explain`, `/status`, `/security-audit` (uploads and LibreOffice/Ghostscript parse untrusted files), `/ideas`, `/release`, `/showcase`, `/ship`.

<!-- project-map: commit=abe2e39 updated=2026-10-31 -->
## Project map

### Overview
Spanish-language web PDF toolkit, `FastAPI(title="Unir PDF")` (`app/main.py:46`) served by uvicorn in Docker (`unir-pdf`, port 8000). No DB, no auth, no env vars; only temp files and in-memory work. Deps: fastapi, pypdf, Pillow, pypdfium2, python-multipart, uvicorn; LibreOffice and Ghostscript as external tools.

### Layout
- `app/main.py`: the whole backend (pages, API, middlewares, helpers). `app/__init__.py` is empty.
- `templates/*.html`: `index.html` plus one plain HTML page per tool (no Jinja; `render_page` at main.py:203 reads the file on each request).
- `static/`: `common.js`, one JS per tool (`office.js` shared by word/excel/powerpoint), `styles.css`, `fonts/` (bundled woff2 + OFL).
- `tests/`: `conftest.py` and one file per tool. `.claude/skills/docker-verify/` holds the project command.

### Entry points and flow
- `uvicorn app.main:app`. `lifespan` (main.py:40) runs `start_document_sweeper` (507): purge DOCUMENTS_DIR, then a daemon thread `sweep_documents_forever` (498).
- Page routes `GET /`, `/merge`, `/split`, ... (main.py:83-130) -> `render_page`.
- Tool request: `postForm()` in common.js -> multipart POST `/api/*`. Handler is a sync `def`, so it runs in a worker thread (main.py:224). It validates the extension and options, processes the file, and returns an attachment named by `clean_output_name` (216). `file.file.close()` runs in `finally`.
- Error codes: 400 bad input, 503 LibreOffice/Ghostscript missing, 504 timeout, 410 expired organizer document.
- In memory: merge (226), pdf-info (271), split (288), images-to-pdf (693), edit-pages (646), metadata read/edit (end of file).
- Temp files in DOCUMENTS_DIR, removed with a `BackgroundTask`: split-zip (366), pdf-to-images (754), compress (960).
- Office: `convert_office_to_pdf` (885) uses a `TemporaryDirectory` and a throwaway LibreOffice profile.

### Key modules (app/main.py)
- Middlewares: `revalidate_static_files` (53) sets no-cache on `/static`. `add_security_headers` (76) applies `SECURITY_HEADERS` (65): strict CSP, nosniff, DENY, no-referrer.
- External tools:
  - `find_soffice` (157) and `find_ghostscript` (161) locate the programs.
  - `run_tool` (165) wraps Popen. `kill_process_tree` (188) uses killpg on POSIX and `taskkill /T` on Windows. It is needed because `soffice` is only a launcher for `soffice.bin`.
- Names and ranges: `clean_base_name` (208), `clean_output_name` (216), `parse_page_ranges` (335), `unique_pdf_names` (354).
- Page organizer:
  - Helpers: `get_document_path` (458), `document_gone` (471), `open_pdfium_document` (479), `remove_expired_documents` (488), `build_edited_pdf` (513).
  - Endpoints at main.py:548-673.
- Images: `ALLOWED_IMAGE_FORMATS` (681), `a4_resolution`, `IMAGE_DPI_CHOICES` / `MAX_IMAGE_PIXELS` (748-751).
- Compress: `COMPRESSION_LEVELS` (951) maps strong/recommended/high to Ghostscript `/screen` / `/ebook` / `/printer`. pdfium validates the file first, and Ghostscript runs with `-dSAFER`.
- Metadata: `METADATA_FIELDS` / `MAX_METADATA_LENGTH` (500), `open_metadata_reader`, `POST /api/metadata/read` (JSON, prefills the form) and `POST /api/metadata` (pypdf clone; the 4 fields replace /Info values, empty = removed; XMP `/Metadata` always dropped; `remove_all` drops /Info; orphans removed with `compress_identical_objects` so old values are not written). Page `/metadata`, `metadata.js`.
- Health: `/health` (133) returns 503 only if it cannot write to DOCUMENTS_DIR. It reports `libreoffice` and `ghostscript` as booleans.

### Data and state
- `DOCUMENTS_DIR = <tmp>/pdf-toolkit-documents` (main.py:28) holds organizer uploads (`<uuid4 hex>.pdf`), job files and ZIPs.
- Ids are checked with `DOCUMENT_ID_PATTERN.fullmatch` (32 hex), which blocks path traversal.
- TTL is based on mtime: 30 min, swept every 5 min. `get_document_path` refreshes it with `os.utime`.
- `POST /api/documents/{id}/edit` deletes the upload after use. pages.js also sends `DELETE` on unload.
- `PDFIUM_LOCK` (33) wraps every pypdfium2 use, because pdfium is not thread-safe.
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
  - `ALLOWED_IMAGE_FORMATS` <-> `accept` in `templates/images.html:31`. The Office extensions <-> `accept`/`data-extensions` in word/excel/powerpoint.html.
  - Dockerfile copies only `app`, `static`, `templates`, so a new top-level dir needs a COPY.
- CSP forbids inline scripts and external hosts, so all JS goes in `static/`. `.woff2` mime is registered at main.py:49 because the slim image lacks it.
- Pillow images are opened with `formats=ALLOWED_IMAGE_FORMATS`, because EPS would invoke Ghostscript.
- The sweeper deletes anything in DOCUMENTS_DIR older than the TTL, including very slow in-flight jobs.
- A container restart wipes organizer sessions; the UI recovers via 410 and re-upload.
- Compress returns the original file if the output is not smaller (`X-Compression-Applied: false`).
- FastAPI turns an empty optional form field into `None`, so `/api/metadata` uses `Form("")` and treats missing and empty the same (field removed).

Area detail: `.claude/rules/map-frontend.md` (static/templates), `.claude/rules/map-tests.md` (tests).
<!-- /project-map -->
