---
name: docker-verify
description: Rebuild the PDFService Docker container from main, run the tests on the PC and inside Docker, check /health, and report which branch is live at localhost:8000. Use when the user types /docker-verify.
argument-hint: [branch to verify instead of main]
disable-model-invocation: true
---

# /docker-verify

Branch to verify: $ARGUMENTS (if empty, `main`).

Facts about this project:
- Compose service: `unir-pdf`, port `8000:8000`. Health endpoint: `GET http://localhost:8000/health` (JSON with `status`, `storage`, and whether LibreOffice / Ghostscript are available).
- The image does **not** contain the tests or dev dependencies (`requirements-dev.txt`, `tests/`, `pytest.ini`).
- Local tests: `python -m pytest`. On Windows, the Office -> PDF tests can make LibreOffice show a "waiting for printer" dialog if the default network printer is off. It is harmless; the test still passes.

## Steps

1. **Check state.** `git status`, current branch, `git fetch`, compare with `origin/<branch>`.
   - Uncommitted changes: stop and ask (they would not be in the image anyway).
   - Remember the branch the user was on, so you can go back to it at the end.
   - If another Claude session is running on this repo, warn the user before switching branches.
2. **Switch to the branch** to verify (`git checkout <branch>`; pull if behind and the tree is clean).
3. **Local tests:** `python -m pytest -q`. Note passed / failed / skipped (skips usually mean LibreOffice or Ghostscript is missing on the PC).
4. **Rebuild the container:** `docker compose down`, then `docker compose up --build -d`. Record the commit: `git rev-parse --short HEAD`.
5. **Tests inside Docker** (a throwaway container from the same image, as root so pip can install the dev deps; nothing is kept):
   ```
   docker compose run --rm --no-deps --user root \
     -v "<repo>/tests:/app/tests" -v "<repo>/pytest.ini:/app/pytest.ini" \
     -v "<repo>/requirements-dev.txt:/app/requirements-dev.txt" \
     unir-pdf sh -c "pip install -q -r requirements-dev.txt && python -m pytest -q"
   ```
   Use absolute Windows paths for the mounts.
6. **Health:** wait for `docker compose ps` to show `healthy` (up to ~60s), then fetch `/health` and show the JSON.
7. **Go back** to the branch the user was on if it was different (the container keeps running the verified branch).
8. **Report** as a table:

   | Check | Result |
   |---|---|
   | Local tests | N passed, N skipped, N failed |
   | Docker tests | ... |
   | Container | healthy / unhealthy |
   | /health | storage, libreoffice, ghostscript |
   | Live at localhost:8000 | branch + commit |

   On any failure, show the relevant error lines and suggest `/fix-bug`. Do not fix code here.
