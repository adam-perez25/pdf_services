# Roadmap

Planned work for PDFService, ordered by priority. Each item is one branch.

## Phase 1: Hardening (do first, protects everything after it)

| Item | Branch | Effort | Summary |
|---|---|---|---|
| Upload limits, clear errors, encrypted PDFs | `feature/hardening` | S | `MAX_UPLOAD_MB` with a 413 response and a memory limit in `docker-compose.yml`; map known errors to Spanish messages instead of `f"...: {error}"` (11 handlers in `app/main.py`) and log the raw error; detect password-protected PDFs and say so. |
| Dark mode | `feature/dark-mode` | S | `prefers-color-scheme: dark` in `static/styles.css` (today it only has reduced-motion and mobile queries). |

## Phase 2: New tools (one branch each)

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

## Ideas for later

- Limit concurrent heavy jobs (two 500 MB jobs at once can exceed the 3 GB memory limit).
- Guard PDF to images at 300 dpi on 1000+ pages (time and temp disk).

## Not planned

- **PDF to Word:** LibreOffice's PDF import gives poor `.docx` output.
- **Accounts or permanent storage:** the app deliberately keeps nothing.
- **Search on the home page:** not needed with the current number of tools.
