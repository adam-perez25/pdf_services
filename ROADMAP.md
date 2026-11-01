# Roadmap

Planned work for PDFService, ordered by priority. Each item is one branch.

## Phase 1: Hardening (done)

| Item | Branch | Effort | Summary |
|---|---|---|---|
| Upload limits, clear errors, encrypted PDFs | `feature/hardening` | S | `MAX_UPLOAD_MB` with a 413 response and a memory limit in `docker-compose.yml`; map known errors to Spanish messages instead of `f"...: {error}"` (11 handlers in `app/main.py`) and log the raw error; detect password-protected PDFs and say so. |
| Dark mode | `feature/dark-mode` | S | `prefers-color-scheme: dark` in `static/styles.css` (today it only has reduced-motion and mobile queries). |

## Phase 2: New tools (done)

| Item | Branch | Effort | Summary |
|---|---|---|---|
| Protect / unlock PDF | `feature/protect-pdf` | M | Add or remove a password with pypdf (`encrypt` / `decrypt`). No new dependency. Passwords are never logged. |
| Page numbers and watermark | `feature/stamp-pdf` | M | Stamp page numbers or a text watermark (position, size, opacity) on every page. |
| Edit metadata | `feature/pdf-metadata` | S-M | View and edit title, author, subject, keywords; option to remove all metadata. |

## Large PDFs (done, `feature/large-pdfs`)

Limits and timeouts configurable via env vars (500 MB per file / 1 GB per request by default, compression timeout scaled per page), results streamed from disk instead of memory, sweeper skips in-flight job files, `mem_limit` 3g. Tested with 1500-page PDFs.

## Phase 3: Big features (done)

| Item | Branch | Notes |
|---|---|---|
| OCR for scanned PDFs | `feature/ocr-pdf` | `/ocr`: ocrmypdf + Tesseract (spa, eng) in the Docker image (+~170 MB); private scratch dir per job, timeout per page, page cap and timeouts configurable. |
| Chain tools | `feature/chain-tools` | `/workflow`, client-side: `static/workflow.js` calls the existing `/api` endpoints in sequence (OCR, numbers, watermark, metadata, compress, protect). New tools are one entry in `STEP_DEFINITIONS`. |
| Sign a PDF | `feature/sign-pdf` | `/sign`: draw or upload a signature, place it on any pages (visual signature, not a digital one). |
| Extract text (PDF to TXT) | `feature/extract-text` | `/extract-text`: page ranges, separators, preview; points to OCR when the PDF has no text. |

## Next (in this order)

The service will become a microservice in a home lab, reachable from the local network. Splitting `app/main.py` comes before the new tools, so they land in small modules instead of growing a 3200-line file.

| # | Item | Branch | Effort | Summary |
|---|---|---|---|---|
| 1 | CI and dependency updates | `chore/ci` | S | GitHub Actions running `python -m pytest` on push (Ubuntu with LibreOffice, Ghostscript, Tesseract via apt); Dependabot for pip, Docker and Actions (every dependency is pinned and several parse untrusted files). Done first so the split below is checked on every push. |
| 2 | ~~Split `app/main.py` into modules~~ (done) | `refactor/split-main` | M | `app/main.py` only wires the app (middlewares in the same order, one `include_router` per tool). Shared code: `config.py` (env limits), `uploads.py`, `documents.py`, `pdf_helpers.py`, `image_helpers.py`, `heavy_jobs.py`, `external_tools.py`, `middleware.py`, `errors.py`; one router per tool in `app/routers/`. Tests patch the module that owns each name. No behaviour change. |
| 3 | Cancel long jobs | `feature/cancel-jobs` | M | `postForm` (`static/common.js`) keeps its request private, so the UI cannot cancel, and the server never notices a client that left: a closed tab keeps a 2-hour OCR running and its heavy-job reservation. Add a cancel button, and stop the LibreOffice / Ghostscript / OCR process (via `run_tool`) when the client disconnects. |
| 4 | PDF/A and repair PDF | `feature/pdfa-repair` | M | Ghostscript is already in the image. PDF/A for official paperwork; repair re-saves damaged PDFs. Same pattern as compress (`strip_before_pdf_header`, `heavy_job`), plus a workflow step. |
| 5 | Extract embedded images | `feature/extract-images` | S-M | Pull the original images out of a PDF (not a page render) into a ZIP with pypdf. |

Bigger, later:

- **Fill and flatten PDF forms:** list the AcroForm fields with their types, fill them, optionally lock them. L.
- **Redaction:** only if it really removes the text underneath (e.g. render the page and re-OCR); a black box over live text is worse than no tool. L.
- **Optional shared access key for the home lab:** a single key set by env var, if the network is ever shared with people who should not use it. No accounts, nothing stored. M.

## Ideas for later

- ~~Limit concurrent heavy jobs (two 500 MB jobs at once can exceed the 3 GB memory limit).~~ Done in `feature/heavy-job-limits`: weighted memory budget (`HEAVY_JOB_BUDGET_MB`), FIFO wait, 503 + `Retry-After` after `HEAVY_JOB_WAIT_SECONDS`.
- ~~Guard PDF to images at 300 dpi on 1000+ pages (time and temp disk).~~ Done on `feature/pdf-to-images-guard`: `pages` selection, up-front pixel cap (400) and disk estimate (507), per-page time limit (504), clean stop on a full disk; all configurable (`PDF_TO_IMAGES_*`).

## Not planned

- **PDF to Word:** LibreOffice's PDF import gives poor `.docx` output.
- **Accounts or permanent storage:** the app deliberately keeps nothing.
- **Search on the home page:** not needed with the current number of tools.
- **Bind the port to localhost only:** the service is meant to be reachable from the home network (`8000:8000` stays as it is).
