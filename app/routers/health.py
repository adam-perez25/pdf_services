"""Health check used by the Docker HEALTHCHECK."""
import tempfile

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app import config, external_tools, heavy_jobs


router = APIRouter()


@router.get("/health")
def health():
    # Used by the Docker HEALTHCHECK. The service is unhealthy only if it cannot
    # store uploads; a missing LibreOffice or Ghostscript is reported but only
    # disables the Office conversions or Compress PDF.
    try:
        config.DOCUMENTS_DIR.mkdir(exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=config.DOCUMENTS_DIR, prefix=".health-"):
            pass
        storage_ok = True
    except OSError:
        storage_ok = False

    return JSONResponse(
        status_code=200 if storage_ok else 503,
        content={
            "status": "ok" if storage_ok else "error",
            "storage": storage_ok,
            "libreoffice": external_tools.find_soffice() is not None,
            "ghostscript": external_tools.find_ghostscript() is not None,
            "ocr": external_tools.ocr_available(),
            "heavy_jobs": heavy_jobs.HEAVY_JOBS.status(),
        },
    )
