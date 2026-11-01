"""Settings: paths, and every limit or timeout that can be set by env var.

Values are parsed once at import (a bad value stops the service with a clear
message) and stored as module constants. Other modules read them as
`config.NAME` at call time, so tests can monkeypatch them in one place.
"""
import os
from pathlib import Path
import tempfile


def env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    """Read a whole-number setting from the environment, or use the default.

    A value that is not a number or is out of range stops the service at
    startup with a clear message, instead of quietly running with a limit
    nobody chose. The result is stored in a module constant and read at call
    time, so tests can still monkeypatch the constant.
    """
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"La variable de entorno {name}={raw!r} no es un número entero.") from None
    if not minimum <= value <= maximum:
        raise ValueError(
            f"La variable de entorno {name}={raw!r} debe estar entre {minimum} y {maximum}."
        )
    return value


BASE_DIR = Path(__file__).resolve().parent.parent
DOCUMENTS_DIR = Path(tempfile.gettempdir()) / "pdf-toolkit-documents"
DOCUMENTS_DIR.mkdir(exist_ok=True)
DOCUMENT_TTL_SECONDS = env_int("DOCUMENT_TTL_SECONDS", 30 * 60, 60, 7 * 24 * 3600)
DOCUMENT_SWEEP_SECONDS = env_int("DOCUMENT_SWEEP_SECONDS", 5 * 60, 10, 24 * 3600)
# Upload limits: per file, and for all the files of one request (merge, images).
# They stop accidents and abuse, not big documents: a 1000-page scan is
# usually a few hundred MB.
MAX_UPLOAD_MB = env_int("MAX_UPLOAD_MB", 500, 1, 100_000)
# Unset, the total follows a raised per-file limit instead of stopping startup.
MAX_TOTAL_UPLOAD_MB = env_int("MAX_TOTAL_UPLOAD_MB", max(1000, MAX_UPLOAD_MB), 1, 100_000)
if MAX_TOTAL_UPLOAD_MB < MAX_UPLOAD_MB:
    raise ValueError(
        f"MAX_TOTAL_UPLOAD_MB ({MAX_TOTAL_UPLOAD_MB}) no puede ser menor que MAX_UPLOAD_MB ({MAX_UPLOAD_MB})."
    )
# Extra room in a request for the multipart boundaries and the text fields.
MULTIPART_OVERHEAD_MB = 1

# Heavy job limiter (see heavy_jobs.py). The budget leaves about 1 GB of the
# container's 3 GB for Python itself, the small jobs that skip the line and
# estimates that fall short.
HEAVY_JOB_BUDGET_MB = env_int("HEAVY_JOB_BUDGET_MB", 2000, 100, 1_000_000)
# How long a request waits for its turn before giving up with a 503.
HEAVY_JOB_WAIT_SECONDS = env_int("HEAVY_JOB_WAIT_SECONDS", 300, 1, 24 * 3600)
# At most this many big jobs wait in line; more get a 503 at once. Each waiting
# request holds one of the server's worker threads (about 40), and /health and
# the small jobs need some of them too.
HEAVY_JOB_MAX_WAITING = env_int("HEAVY_JOB_MAX_WAITING", 10, 1, 1000)

# Guards for converting very long documents to images, checked before
# rendering anything. An A4 page is about 2.2 million pixels at 150 ppp and
# 8.7 million at 300 ppp, so the default of 10 000 megapixels allows 1000 A4
# pages at 300 ppp (8.7 Gpx) or about 4500 at 150 ppp, but rejects 2000 A4
# pages at 300 ppp (17.4 Gpx), which would take most of an hour and many GB of
# temporary disk.
PDF_TO_IMAGES_MAX_MEGAPIXELS = env_int("PDF_TO_IMAGES_MAX_MEGAPIXELS", 10_000, 1, 1_000_000)
# Space that must stay free in the temporary folder after the estimated output.
PDF_TO_IMAGES_MIN_FREE_MB = env_int("PDF_TO_IMAGES_MIN_FREE_MB", 512, 0, 1_000_000)
PDF_TO_IMAGES_TIMEOUT_SECONDS = env_int("PDF_TO_IMAGES_TIMEOUT_SECONDS", 300, 10, 24 * 3600)
PDF_TO_IMAGES_SECONDS_PER_PAGE = env_int("PDF_TO_IMAGES_SECONDS_PER_PAGE", 3, 0, 600)
PDF_TO_IMAGES_MAX_TIMEOUT_SECONDS = env_int("PDF_TO_IMAGES_MAX_TIMEOUT_SECONDS", 7200, 10, 7 * 24 * 3600)

OFFICE_TIMEOUT_SECONDS = env_int("OFFICE_TIMEOUT_SECONDS", 300, 10, 24 * 3600)

# Ghostscript's time grows with the page count, so the timeout does too:
# base + per page, capped. 20 pages get 640 s, 1000 pages 2600 s.
COMPRESSION_TIMEOUT_SECONDS = env_int("COMPRESSION_TIMEOUT_SECONDS", 600, 10, 24 * 3600)
COMPRESSION_SECONDS_PER_PAGE = env_int("COMPRESSION_SECONDS_PER_PAGE", 2, 0, 600)
COMPRESSION_MAX_TIMEOUT_SECONDS = env_int("COMPRESSION_MAX_TIMEOUT_SECONDS", 3600, 10, 7 * 24 * 3600)

# Big scans are allowed: the limits are settings like the other ones, and the
# cap (4 h by default) covers about 700 pages at the slow 20 s per page.
OCR_MAX_PAGES = env_int("OCR_MAX_PAGES", 2000, 1, 100_000)
OCR_TIMEOUT_BASE_SECONDS = env_int("OCR_TIMEOUT_SECONDS", 60, 10, 24 * 3600)
OCR_TIMEOUT_PER_PAGE_SECONDS = env_int("OCR_SECONDS_PER_PAGE", 20, 0, 3600)
OCR_TIMEOUT_MAX_SECONDS = env_int("OCR_MAX_TIMEOUT_SECONDS", 4 * 3600, 10, 7 * 24 * 3600)
