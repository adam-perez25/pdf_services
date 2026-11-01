"""Heavy job limiter: a memory budget shared by the running jobs.

Two jobs on 500 MB files at once could exceed the container's memory
(mem_limit 3g), so each job reserves an estimate of the memory it needs from
a shared budget (config.HEAVY_JOB_BUDGET_MB) and waits in line while the
budget is used up. Small jobs (the usual few-MB PDFs) skip the line entirely,
so they never wait.

Endpoints wrap their processing in heavy_jobs.heavy_job(...) (through the
module, so tests can replace it), after the cheap validations.
"""
from collections import deque
from contextlib import contextmanager
import gc
import math
import sys
import threading
import time

from fastapi import HTTPException, UploadFile

from app import cancellation, config
from app.errors import logger


# Jobs estimated below this many MB never wait and reserve nothing.
HEAVY_JOB_MIN_MB = 100
# Jobs on files smaller than this (in total) never wait either, whatever the
# tool: the fixed part of an estimate (Ghostscript, LibreOffice...) is a
# worst case for big documents, and the usual few-MB PDFs must not queue.
HEAVY_JOB_SMALL_FILES_MB = 50
HEAVY_JOB_RETRY_AFTER_SECONDS = 60
HEAVY_JOB_BUSY_MESSAGE = (
    "El servidor está ocupado con otros documentos grandes. "
    "Inténtalo de nuevo en unos minutos."
)
# Estimated memory per tool: (factor x upload size, fixed MB). From the
# measurements with a 200 MB / 1000-page PDF: merging 2 copies used ~500 MB
# (1.25x), split/organize/stamp/metadata ~300 MB (1.5x), protect ~650 MB
# (3.25x). Ghostscript, Tesseract and LibreOffice run as separate processes
# whose memory depends little on the file size, hence the fixed part.
# pdf-to-images renders one page at a time, bounded by MAX_IMAGE_PIXELS
# (40 Mpx x 4 bytes = 160 MB per bitmap, plus the RGB copy and the encoder).
HEAVY_JOB_ESTIMATES = {
    "merge": (1.3, 0),
    "split": (1.5, 0),
    "edit": (1.5, 0),  # organizer save, edit-pages
    "stamp": (1.5, 0),
    "metadata": (1.5, 0),
    "protect": (3.5, 0),
    "unlock": (3.5, 0),
    "sign": (1.5, 100),
    "images-to-pdf": (1.5, 300),  # one decoded image at a time
    "pdf-to-images": (0.5, 400),
    "extract-text": (0.5, 0),  # pdfium, page by page, written to disk
    "compress": (0.5, 300),  # Ghostscript
    "pdfa": (0.5, 300),  # Ghostscript, with color conversion
    "repair": (0.5, 300),  # Ghostscript
    "ocr": (0.5, 600),  # ocrmypdf + OCR_JOBS Tesseract workers
    "office": (3.0, 500),  # LibreOffice; documents grow a lot once loaded
}


def job_estimate_mb(tool: str, *sizes: int, minimum_mb: int = 0) -> int:
    """Estimated memory in MB of running `tool` on files of these byte sizes.

    Small files give 0, so heavy_job lets them through without waiting.
    `minimum_mb` is a floor that applies whatever the file sizes: a tiny
    compressed image can decode to hundreds of MB (see decoded_image_mb).
    """
    if sum(sizes) < HEAVY_JOB_SMALL_FILES_MB * 1024 * 1024:
        return minimum_mb
    factor, fixed = HEAVY_JOB_ESTIMATES[tool]
    return max(math.ceil(factor * sum(sizes) / (1024 * 1024) + fixed), minimum_mb)


# Memory per pixel while an image is decoded and converted: the decoded image
# (up to 4 bytes per pixel, RGBA) plus its RGB/RGBA copy.
DECODED_BYTES_PER_PIXEL = 8


def decoded_image_mb(pixels: int) -> int:
    return math.ceil(pixels * DECODED_BYTES_PER_PIXEL / (1024 * 1024))


class HeavyJobLimiter:
    """A memory budget shared by the running jobs, handed out in arrival order.

    A job runs when it is first in line and its estimate fits in what is left
    of the budget, or when nothing else is running (so a job bigger than the
    whole budget still runs, alone). Strict first-come order means a waiting
    big job is never overtaken by later ones, so it cannot starve.
    """

    def __init__(self):
        self.condition = threading.Condition()
        self.queue: deque = deque()
        self.reserved_mb = 0
        self.running = 0

    def acquire(
        self, megabytes: int, budget: int, wait_seconds: float, max_waiting: int | None = None, cancel_check=None
    ) -> bool:
        """Wait for a turn; False if it did not come in time. With cancel_check,
        the wait is cut into short slices and raises JobCancelled once it is true."""
        deadline = time.monotonic() + wait_seconds
        ticket = object()
        with self.condition:
            if max_waiting is not None and len(self.queue) >= max_waiting:
                return False
            self.queue.append(ticket)
            try:
                while not (
                    self.queue[0] is ticket
                    and (self.running == 0 or self.reserved_mb + megabytes <= budget)
                ):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return False
                    if cancel_check is not None:
                        if cancel_check():
                            raise cancellation.JobCancelled()
                        remaining = min(remaining, cancellation.POLL_SECONDS)
                    self.condition.wait(remaining)
                self.reserved_mb += megabytes
                self.running += 1
                return True
            finally:
                # Whether it got in or gave up, the next one in line may now go.
                self.queue.remove(ticket)
                self.condition.notify_all()

    def release(self, megabytes: int) -> None:
        with self.condition:
            self.reserved_mb -= megabytes
            self.running -= 1
            self.condition.notify_all()

    def status(self) -> dict:
        with self.condition:
            return {"running": self.running, "reserved_mb": self.reserved_mb, "waiting": len(self.queue)}


HEAVY_JOBS = HeavyJobLimiter()


@contextmanager
def heavy_job(megabytes: int, uploads: list[UploadFile] = ()):
    """Hold a share of the memory budget while a job runs.

    Use it around the processing only, after the cheap validations, so bad
    requests fail at once. If the turn does not come within
    HEAVY_JOB_WAIT_SECONDS it answers 503 with Retry-After (closing `uploads`,
    like check_upload_sizes), or 499 if the job is cancelled while it waits. The share is released even if the job fails.
    """
    if megabytes < HEAVY_JOB_MIN_MB:
        yield
        return
    try:
        acquired = HEAVY_JOBS.acquire(
            megabytes, config.HEAVY_JOB_BUDGET_MB, config.HEAVY_JOB_WAIT_SECONDS, config.HEAVY_JOB_MAX_WAITING,
            cancellation.current_check(),
        )
    except cancellation.JobCancelled:
        for upload in uploads:
            upload.file.close()
        raise
    if not acquired:
        for upload in uploads:
            upload.file.close()
        raise HTTPException(
            status_code=503,
            detail=HEAVY_JOB_BUSY_MESSAGE,
            headers={"Retry-After": str(HEAVY_JOB_RETRY_AFTER_SECONDS)},
        )
    try:
        yield
    finally:
        HEAVY_JOBS.release(megabytes)
        release_free_memory()


def _load_malloc_trim():
    # glibc only (the Docker image): musl and Windows have no malloc_trim.
    if not sys.platform.startswith("linux"):
        return None
    try:
        import ctypes

        return ctypes.CDLL("libc.so.6").malloc_trim
    except (OSError, AttributeError):
        return None


MALLOC_TRIM = _load_malloc_trim()
# The job's objects (readers, writers) are only freed once the endpoint has
# returned, a moment after heavy_job ends, so the trim runs a little later.
MALLOC_TRIM_DELAY_SECONDS = 2.0
_trim_lock = threading.Lock()
_trim_timer: threading.Timer | None = None


def _trim_now() -> None:
    try:
        gc.collect()
        MALLOC_TRIM(0)
    except Exception:
        logger.debug("malloc_trim failed", exc_info=True)


def release_free_memory() -> None:
    """Give the memory a big job freed back to the system.

    glibc keeps freed heap memory for reuse, so after a few big jobs the idle
    container stays near its peak size; malloc_trim(0) returns the free pages.
    Called only after big jobs (heavy_job reservations); several jobs ending
    close together share one trim. A no-op where malloc_trim does not exist.
    """
    global _trim_timer
    if MALLOC_TRIM is None:
        return
    with _trim_lock:
        if _trim_timer is not None and _trim_timer.is_alive():
            return  # a trim is already due; it will cover this job too
        _trim_timer = threading.Timer(MALLOC_TRIM_DELAY_SECONDS, _trim_now)
        _trim_timer.daemon = True
        _trim_timer.start()
