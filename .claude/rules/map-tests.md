---
paths:
  - "tests/**"
---
# Tests map

- Run `python -m pytest` (pytest.ini: `testpaths=tests`, `pythonpath=.`). Tests import `app.main as main` only for `main.app`; everything else comes from the module that owns it (`import app.config as config`, `import app.routers.split as split_router`, ...). Patch a name where it is looked up: settings on `config`, `heavy_job` / `HEAVY_JOBS` on `heavy_jobs`, `run_tool` / `find_*` / `ocr_available` on `external_tools`, other helpers on the router that imports them. Dev deps in `requirements-dev.txt` (httpx, pytest, openpyxl, python-pptx).
- One file per tool:
  - test_compress, test_metadata (incl. the fresh XMP after an edit and `pdf_date_to_xmp`), test_images_to_pdf (transparency, EXIF, TIFF frames incl. a huge later frame), test_office_to_pdf, test_page_organizer, test_protect, test_split_zip, test_extract_text (real-text PDFs via `text_pdf`), test_stamp (renders with pypdfium2 to check where the text lands on rotated pages), test_sign (uploads via `/api/documents`, renders under `PDFIUM_LOCK` to check ink position, rotations, cropbox, transparent PNG, validation, 410).
  - test_pdf_to_images also covers page ranges, the pixel cap, the disk check (monkeypatched `shutil.disk_usage`), the timeout (monkeypatched `pdf_to_images_timeout`) and ENOSPC (fake `ZipFile.writestr`).
  - test_ocr: real OCR runs behind `needs_tesseract`; validation, exit codes, timeout, `--redo-ocr` page selection, the fallback to `--skip-text`, `--rotate-pages`, `page_list` and the sweeper during a long run use monkeypatched `ocr_available` / `run_tool` and always run.
  - test_pdf_tools covers merge, split, pdf-info and edit-pages.
- Cross-cutting:
  - test_workflow: the `/workflow` page; every `"/api/..."` string in `static/workflow.js` (ignoring `//` lines) must be a registered POST route (`all_routes` looks inside the included routers); step positions (OCR first, metadata late, protect last); the merge -> stamp -> metadata -> protect chain through the TestClient; behind `needs_ghostscript`, a chain with compress before metadata leaving no Producer/XMP after unlock.
  - test_health, test_run_tool (one test needs Linux `/proc`), test_security_headers, test_dark_mode (CSS variables, color-scheme meta on every template).
  - test_heavy_jobs: the limiter (FIFO, fits / runs alone, queue cap, 503 + `Retry-After`, release on errors), estimates incl. the small-file rule, endpoint-level waits with a fake slow Ghostscript, organizer save protected from the sweeper while waiting, `malloc_trim` scheduling (one test is glibc only).
  - test_large_pdfs: a 1500-page PDF built once per module through merge, split-zip, organizer, stamp, metadata, protect/unlock and pdf-to-images; the sweeper skipping in-flight files; `compression_timeout`; `env_int`; env vars read in a subprocess that imports `app.main` and reads `app.config` (`SETTINGS` list - add new env settings there); limits read at request time (~13 s).
  - test_hardening: upload limits (413, monkeypatched `MAX_UPLOAD_MB`), non-leaking error details, password-protected PDFs (`PdfWriter.encrypt`), `content_disposition` / `clean_base_name` (Unicode, header injection) and the Spanish 400 for validation errors.
  - test_security_hardening: the seeded LibreOffice profile (fake `run_tool` parses the xcu; a real linked-image ODT behind `needs_libreoffice`), PostScript-prefixed PDFs for compress/OCR (fake `run_tool` checks the bytes; real Ghostscript behind `needs_ghostscript`), page-list caps, the 500x200000 pt thumbnail, header-only PNG pixel bombs (`png_header_only`, recorded `heavy_job` estimates), the cross-site guard (Sec-Fetch-Site / Origin), disabled /docs, the output-name cap and the compose hardening lines (skipped inside Docker, where docker-compose.yml is not present).
- `tests/conftest.py`:
  - `documents_dir` monkeypatches `config.DOCUMENTS_DIR` to `tmp_path/"documents"`, so tests can assert it is left empty.
  - `client` is `TestClient(main.app)`, NOT used as a context manager, so the lifespan and sweeper do not run. Startup is tested with `with TestClient(main.app):` (`test_startup_purges_leftovers` in test_page_organizer.py).
  - `sample_pdf` is a 4-page PDF where page n is n*100 pt wide, so tests can tell pages apart.
  - Helpers: `make_pdf`, `make_png`, `page_layout(content)` (width and rotation per page).
- Skip markers are defined per file: `needs_ghostscript` (test_compress, test_security_hardening, test_workflow; test_images_to_pdf has one Ghostscript test), `needs_tesseract` (`not external_tools.ocr_available()`, test_ocr), `needs_libreoffice` (test_office_to_pdf, test_pdf_tools, test_security_hardening). On Windows without Tesseract the OCR runs, the glibc-only trim test and the `/proc` test skip.
- Office inputs are generated with openpyxl and python-pptx (or hand-built zips); no binary fixtures are checked in.
- Tests are not in the Docker image. `/docker-verify` runs them on the PC and inside the container (mount `tests/`, `pytest.ini`, `requirements-dev.txt`; run as root to pip install the dev deps).
