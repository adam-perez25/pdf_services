"""Cancelling long jobs: a client that leaves, or presses "Cancelar", stops the
external tool and frees the job's heavy-job reservation.

Handlers are sync functions running in a worker thread, which Starlette never
interrupts when the client goes away. So a handler wraps its work in
`cancellable_job(request)`, and the code that waits (`external_tools.run_tool`,
the heavy-job queue) polls `current_check()` and raises JobCancelled.
Cancelling is an HTTPException (status 499), so the handlers' existing
`except HTTPException` cleanup removes the job files.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import re
import threading
from typing import Callable

from fastapi import HTTPException, Request


JOB_ID_HEADER = "X-Job-Id"
JOB_ID_PATTERN = re.compile(r"[0-9a-f]{32}")
CANCELLED_STATUS = 499  # "client closed request"
CANCELLED_MESSAGE = "Trabajo cancelado."
POLL_SECONDS = 0.5
# scope key holding the threading.Event that middleware.ClientDisconnectWatcher sets.
CLIENT_GONE_KEY = "pdfservice.client_gone"


class JobCancelled(HTTPException):
    def __init__(self):
        super().__init__(status_code=CANCELLED_STATUS, detail=CANCELLED_MESSAGE)


_current_check: ContextVar[Callable[[], bool] | None] = ContextVar("current_check", default=None)
_cancel_requests: dict[str, threading.Event] = {}
_cancel_lock = threading.Lock()


def current_check() -> Callable[[], bool] | None:
    """The cancel check of the job running in this thread, if any."""
    return _current_check.get()


def raise_if_cancelled() -> None:
    check = _current_check.get()
    if check is not None and check():
        raise JobCancelled()


def cancel_job(job_id: str) -> bool:
    """Ask the job with this id to stop; False if no such job is running."""
    with _cancel_lock:
        event = _cancel_requests.get(job_id)
    if event is None:
        return False
    event.set()
    return True


def _client_left(request: Request) -> bool:
    # Set by middleware.ClientDisconnectWatcher; absent outside the real app.
    flag = request.scope.get(CLIENT_GONE_KEY)
    return flag is not None and flag.is_set()


@contextmanager
def cancellable_job(request: Request):
    """Mark the work in this block as cancellable (call it from the handler thread).

    The check turns true when the client disconnects or POST /api/jobs/{id}/cancel
    names the id the page sent in the X-Job-Id header.
    """
    job_id = request.headers.get(JOB_ID_HEADER, "")
    event = threading.Event()
    registered = JOB_ID_PATTERN.fullmatch(job_id) is not None
    if registered:
        with _cancel_lock:
            _cancel_requests[job_id] = event

    def check() -> bool:
        return event.is_set() or _client_left(request)

    token = _current_check.set(check)
    try:
        yield
    finally:
        _current_check.reset(token)
        if registered:
            with _cancel_lock:
                if _cancel_requests.get(job_id) is event:
                    del _cancel_requests[job_id]
