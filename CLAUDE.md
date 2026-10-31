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
