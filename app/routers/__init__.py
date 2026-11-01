"""One APIRouter per tool; app/main.py includes them all.

The file-processing endpoints are plain functions, not async: FastAPI runs
them in a worker thread, so a long merge or conversion does not freeze the
server for everyone else. The HTML page routes are async (html_pages.py).
"""
