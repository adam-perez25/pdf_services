"""The documents folder: organizer/sign uploads referred to by id, the
sweeper that removes expired files, and protect_from_sweeper for job files
that are still in use. The folder itself is config.DOCUMENTS_DIR.
"""
from contextlib import contextmanager
import os
from pathlib import Path
import re
import threading
import time

from fastapi import HTTPException
import pypdfium2

from app import config
from app.errors import logger


DOCUMENT_ID_PATTERN = re.compile(r"[0-9a-f]{32}")


# Work files of requests that are still being processed (a long compression,
# a big ZIP). The sweeper skips them, so a job that runs longer than
# DOCUMENT_TTL_SECONDS does not lose its files halfway.
ACTIVE_JOB_FILES: set[Path] = set()
ACTIVE_JOB_LOCK = threading.Lock()


@contextmanager
def protect_from_sweeper(*paths: Path):
    with ACTIVE_JOB_LOCK:
        ACTIVE_JOB_FILES.update(paths)
    try:
        yield
    finally:
        with ACTIVE_JOB_LOCK:
            ACTIVE_JOB_FILES.difference_update(paths)
        # The download starts right after; a fresh mtime gives it a full TTL
        # before the sweeper may remove a file whose cleanup never ran.
        for path in paths:
            try:
                os.utime(path)
            except FileNotFoundError:
                pass


# The page organizer uploads a PDF once and then refers to it by id, so
# thumbnails, previews and the final save never upload the file again.
# Documents live in a temporary folder and are removed after the download, after
# DOCUMENT_TTL_SECONDS without use, or when the page asks for it.
def get_document_path(document_id: str) -> Path:
    if not DOCUMENT_ID_PATTERN.fullmatch(document_id):
        raise HTTPException(status_code=404, detail="Documento no encontrado.")
    path = config.DOCUMENTS_DIR / f"{document_id}.pdf"
    try:
        # Refresh the last-use time; unlike Path.touch this never recreates a
        # file that another request has just deleted.
        os.utime(path)
    except FileNotFoundError:
        raise document_gone() from None
    return path


def document_gone() -> HTTPException:
    # 410 tells the page the upload expired, so it can send the file again.
    return HTTPException(
        status_code=410,
        detail="El documento ha caducado. Vuelve a seleccionar el PDF.",
    )


def open_pdfium_document(path: Path) -> pypdfium2.PdfDocument:
    try:
        return pypdfium2.PdfDocument(path)
    except Exception:
        if not path.exists():
            raise document_gone() from None
        raise


def remove_expired_documents(max_age: float | None = None) -> None:
    # Read at call time, so the configured (or monkeypatched) TTL applies.
    limit = time.time() - (config.DOCUMENT_TTL_SECONDS if max_age is None else max_age)
    for path in config.DOCUMENTS_DIR.iterdir():
        # Holding the lock keeps a job from registering a file between the
        # check and the unlink.
        with ACTIVE_JOB_LOCK:
            if path in ACTIVE_JOB_FILES:
                continue
            try:
                if path.stat().st_mtime < limit:
                    path.unlink()
            except OSError:
                # Already gone, or (on Windows) still open by a request.
                pass


def sweep_documents_forever() -> None:
    while True:
        time.sleep(config.DOCUMENT_SWEEP_SECONDS)
        try:
            remove_expired_documents()
        except Exception:
            logger.exception("No se pudieron limpiar los documentos temporales.")


def start_document_sweeper() -> None:
    # Anything left from a previous run (crash, `docker restart`) is stale.
    remove_expired_documents(max_age=0)
    threading.Thread(target=sweep_documents_forever, daemon=True).start()
