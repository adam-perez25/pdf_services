---
paths:
  - "tests/**"
---
# Tests map

- Run `python -m pytest` (pytest.ini: testpaths=tests, pythonpath=.). Tests import `app.main as main`.
- Files:
  - one per tool: test_compress, test_images_to_pdf, test_office_to_pdf, test_page_organizer, test_pdf_to_images, test_protect, test_split_zip;
  - test_pdf_tools covers merge, split, pdf-info and edit-pages;
  - test_health, test_run_tool and test_security_headers cover cross-cutting behaviour.
- `tests/conftest.py`:
  - `documents_dir` monkeypatches `main.DOCUMENTS_DIR` to `tmp_path/"documents"`, so tests can assert that it is left empty.
  - `client` is `TestClient(main.app)`. It is NOT used as a context manager, so the lifespan and sweeper do not run.
  - `sample_pdf` is a 4-page PDF where page n is n*100 pt wide, so tests can tell pages apart.
  - Helpers: `make_pdf`, `make_png`, `page_layout(content)` (width and rotation per page).
- Startup behaviour is tested with `with TestClient(main.app):` (`test_startup_purges_leftovers` in test_page_organizer.py).
- Skip markers:
  - `needs_ghostscript` (test_compress.py:10) and test_images_to_pdf.py:72 skip when Ghostscript is missing.
  - `needs_libreoffice` (test_office_to_pdf.py:11, test_pdf_tools.py:120) skips when LibreOffice is missing.
  - test_run_tool.py:19 needs Linux `/proc`.
- Office inputs are generated with openpyxl and python-pptx (dev deps); no binary fixtures are checked in.
- Tests are not in the Docker image. `/docker-verify` runs them on the PC and inside the container.
