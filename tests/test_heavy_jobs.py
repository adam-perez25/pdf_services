"""Heavy job limiter: a shared memory budget so big jobs do not run all at once."""
import contextlib
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

import app.config as config
import app.documents as documents
import app.external_tools as external_tools
import app.heavy_jobs as heavy_jobs
import app.main as main
from conftest import make_pdf

ROOT = Path(__file__).resolve().parent.parent


def wait_until(condition, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("La condición no se cumplió a tiempo.")
        time.sleep(0.005)


class Waiter:
    """Runs limiter.acquire in a thread and records whether it got in."""

    def __init__(self, limiter, megabytes, budget=100, wait_seconds=5.0):
        self.result = None
        self.done = threading.Event()

        def run():
            self.result = limiter.acquire(megabytes, budget, wait_seconds)
            self.done.set()

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()


# --- The limiter itself ------------------------------------------------------

def test_two_big_jobs_run_one_after_the_other():
    limiter = heavy_jobs.HeavyJobLimiter()
    assert limiter.acquire(80, 100, 0)

    second = Waiter(limiter, 80)
    wait_until(lambda: limiter.status()["waiting"] == 1)
    assert not second.done.is_set()

    limiter.release(80)
    assert second.done.wait(3) and second.result is True
    assert limiter.status() == {"running": 1, "reserved_mb": 80, "waiting": 0}
    limiter.release(80)
    assert limiter.status() == {"running": 0, "reserved_mb": 0, "waiting": 0}


def test_jobs_that_fit_run_together():
    limiter = heavy_jobs.HeavyJobLimiter()
    # wait_seconds=0: each one must get in at once, without waiting.
    assert all(limiter.acquire(30, 100, 0) for _ in range(3))
    assert limiter.status()["running"] == 3
    assert not limiter.acquire(30, 100, 0)  # 120 > 100


def test_a_job_bigger_than_the_budget_runs_alone():
    limiter = heavy_jobs.HeavyJobLimiter()
    assert limiter.acquire(500, 100, 0)
    assert not limiter.acquire(10, 100, 0)
    limiter.release(500)
    assert limiter.acquire(10, 100, 0)
    # ...and waits for the others to finish before it starts.
    assert not limiter.acquire(500, 100, 0)


def test_a_waiting_big_job_is_not_overtaken():
    limiter = heavy_jobs.HeavyJobLimiter()
    assert limiter.acquire(60, 100, 0)
    big = Waiter(limiter, 80)
    wait_until(lambda: limiter.status()["waiting"] == 1)
    # 60 + 30 fits, but the big job came first.
    small = Waiter(limiter, 30)
    wait_until(lambda: limiter.status()["waiting"] == 2)
    assert not small.done.is_set()

    limiter.release(60)
    assert big.done.wait(3) and big.result is True
    assert not small.done.is_set()  # 80 + 30 does not fit
    limiter.release(80)
    assert small.done.wait(3) and small.result is True


def test_a_job_that_gives_up_lets_the_next_one_go():
    limiter = heavy_jobs.HeavyJobLimiter()
    assert limiter.acquire(60, 100, 0)
    big = Waiter(limiter, 80, wait_seconds=0.1)
    wait_until(lambda: limiter.status()["waiting"] == 1)
    small = Waiter(limiter, 30)

    assert big.done.wait(3) and big.result is False
    assert small.done.wait(3) and small.result is True
    assert limiter.status() == {"running": 2, "reserved_mb": 90, "waiting": 0}


@pytest.fixture
def limiter(monkeypatch):
    fresh = heavy_jobs.HeavyJobLimiter()
    monkeypatch.setattr(heavy_jobs, "HEAVY_JOBS", fresh)
    monkeypatch.setattr(config, "HEAVY_JOB_BUDGET_MB", 1000)
    return fresh


def test_heavy_job_answers_503_after_waiting_too_long(limiter, monkeypatch):
    monkeypatch.setattr(config, "HEAVY_JOB_WAIT_SECONDS", 0)

    class Upload:
        file = BytesIO(b"x")

    with heavy_jobs.heavy_job(1000):
        with pytest.raises(HTTPException) as caught:
            with heavy_jobs.heavy_job(500, [Upload]):
                pass

    assert caught.value.status_code == 503
    assert caught.value.detail == heavy_jobs.HEAVY_JOB_BUSY_MESSAGE
    assert caught.value.headers["Retry-After"] == str(heavy_jobs.HEAVY_JOB_RETRY_AFTER_SECONDS)
    assert Upload.file.closed
    assert limiter.status() == {"running": 0, "reserved_mb": 0, "waiting": 0}


def test_heavy_job_releases_its_share_when_the_job_fails(limiter):
    with pytest.raises(ValueError):
        with heavy_jobs.heavy_job(400):
            assert limiter.status()["reserved_mb"] == 400
            raise ValueError("falla")

    assert limiter.status() == {"running": 0, "reserved_mb": 0, "waiting": 0}


def test_small_jobs_skip_the_line(limiter, monkeypatch):
    monkeypatch.setattr(config, "HEAVY_JOB_WAIT_SECONDS", 0)
    with heavy_jobs.heavy_job(5000):
        with heavy_jobs.heavy_job(heavy_jobs.HEAVY_JOB_MIN_MB - 1), heavy_jobs.heavy_job(1):
            assert limiter.status() == {"running": 1, "reserved_mb": 5000, "waiting": 0}


def test_estimates():
    megabyte = 1024 * 1024
    assert heavy_jobs.job_estimate_mb("protect", 200 * megabyte) == 700
    assert heavy_jobs.job_estimate_mb("merge", 100 * megabyte, 100 * megabyte) == 260
    assert heavy_jobs.job_estimate_mb("compress", 60 * megabyte) == 330
    # Small files never wait, even on tools with a big fixed part.
    for tool in heavy_jobs.HEAVY_JOB_ESTIMATES:
        assert heavy_jobs.job_estimate_mb(tool, 2 * megabyte) == 0, tool
    assert heavy_jobs.job_estimate_mb("merge", 20 * megabyte, 20 * megabyte) == 0
    assert heavy_jobs.job_estimate_mb("merge", 30 * megabyte, 30 * megabyte) == 78
    # The usual few-MB PDF never waits in pypdf tools.
    assert heavy_jobs.job_estimate_mb("stamp", 5 * megabyte) < heavy_jobs.HEAVY_JOB_MIN_MB


# --- Settings ----------------------------------------------------------------

def import_heavy_settings(**env: str) -> subprocess.CompletedProcess:
    names = ["HEAVY_JOB_BUDGET_MB", "HEAVY_JOB_WAIT_SECONDS"]
    clean = {key: value for key, value in os.environ.items() if key not in names}
    code = f"import json, app.main, app.config as m; print(json.dumps([getattr(m, n) for n in {names!r}]))"
    return subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, env={**clean, **env},
        capture_output=True, text=True, timeout=120,
    )


def test_settings_defaults_and_overrides():
    if "HEAVY_JOB_BUDGET_MB" not in os.environ and "HEAVY_JOB_WAIT_SECONDS" not in os.environ:
        assert (config.HEAVY_JOB_BUDGET_MB, config.HEAVY_JOB_WAIT_SECONDS) == (2000, 300)

    custom = import_heavy_settings(HEAVY_JOB_BUDGET_MB="2500", HEAVY_JOB_WAIT_SECONDS="60")
    assert custom.returncode == 0, custom.stderr
    assert json.loads(custom.stdout) == [2500, 60]


def test_invalid_setting_stops_the_service():
    result = import_heavy_settings(HEAVY_JOB_BUDGET_MB="10")
    assert result.returncode != 0
    assert "HEAVY_JOB_BUDGET_MB" in result.stderr


# --- Through the endpoints ---------------------------------------------------

@pytest.fixture
def slow_compress(limiter, monkeypatch):
    """Compress holds its share until `release` is set; Ghostscript is faked."""
    started, release = threading.Event(), threading.Event()

    def fake_run_tool(command, timeout, env=None):
        started.set()
        assert release.wait(5)
        output = next(arg for arg in command if arg.startswith("-sOutputFile="))
        shutil.copyfile(command[-1], output.split("=", 1)[1])
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(external_tools, "find_ghostscript", lambda: "gs")
    monkeypatch.setattr(external_tools, "run_tool", fake_run_tool)
    # Every compression counts as bigger than the whole budget.
    monkeypatch.setattr(heavy_jobs, "HEAVY_JOB_ESTIMATES", {**heavy_jobs.HEAVY_JOB_ESTIMATES, "compress": (0, 1500)})
    # The sample PDF is tiny; let it count as a big job for these tests.
    monkeypatch.setattr(heavy_jobs, "HEAVY_JOB_SMALL_FILES_MB", 0)
    return started, release


def post_compress(pdf: bytes, **data):
    return TestClient(main.app).post(
        "/api/compress",
        files={"file": ("doc.pdf", pdf, "application/pdf")},
        data={"level": "recommended", **data},
    )


def start_in_thread(function):
    result = {}
    thread = threading.Thread(target=lambda: result.setdefault("response", function()), daemon=True)
    thread.start()
    return thread, result


def test_second_big_request_gets_503_while_the_first_runs(client, sample_pdf, slow_compress, monkeypatch, limiter):
    started, release = slow_compress
    first, first_result = start_in_thread(lambda: post_compress(sample_pdf))
    assert started.wait(5)

    health = client.get("/health").json()["heavy_jobs"]
    assert health == {"running": 1, "reserved_mb": 1500, "waiting": 0}

    # Bad requests are rejected at once, without waiting in line.
    assert post_compress(sample_pdf, level="ultra").status_code == 400
    # Small jobs do not wait either.
    files = [("files", ("a.pdf", sample_pdf, "application/pdf"))]
    assert client.post("/api/merge", files=files).status_code == 200

    monkeypatch.setattr(config, "HEAVY_JOB_WAIT_SECONDS", 0)
    busy = post_compress(sample_pdf)
    assert busy.status_code == 503
    assert busy.json()["detail"] == heavy_jobs.HEAVY_JOB_BUSY_MESSAGE
    assert busy.headers["Retry-After"] == str(heavy_jobs.HEAVY_JOB_RETRY_AFTER_SECONDS)

    release.set()
    first.join(5)
    assert first_result["response"].status_code == 200
    assert limiter.status() == {"running": 0, "reserved_mb": 0, "waiting": 0}


def test_second_big_request_waits_for_the_first(client, sample_pdf, slow_compress, limiter, documents_dir):
    started, release = slow_compress
    first, first_result = start_in_thread(lambda: post_compress(sample_pdf))
    assert started.wait(5)
    started.clear()

    second, second_result = start_in_thread(lambda: post_compress(sample_pdf))
    wait_until(lambda: limiter.status()["waiting"] == 1)
    assert not started.is_set()  # the second one has not reached Ghostscript

    release.set()
    first.join(5)
    second.join(5)
    assert first_result["response"].status_code == 200
    assert second_result["response"].status_code == 200
    assert limiter.status() == {"running": 0, "reserved_mb": 0, "waiting": 0}
    assert list(documents_dir.iterdir()) == []


def test_full_queue_answers_at_once():
    limiter = heavy_jobs.HeavyJobLimiter()
    assert limiter.acquire(80, 100, 0)
    first = Waiter(limiter, 80)
    wait_until(lambda: limiter.status()["waiting"] == 1)

    started = time.monotonic()
    # One already waits and the cap is 1: no waiting at all, even with time left.
    assert limiter.acquire(80, 100, 5.0, max_waiting=1) is False
    assert time.monotonic() - started < 1

    limiter.release(80)
    assert first.done.wait(3) and first.result is True
    assert limiter.status() == {"running": 1, "reserved_mb": 80, "waiting": 0}


def test_waiting_organizer_save_keeps_its_document(client, documents_dir, monkeypatch):
    # While an organizer save waits for its turn, the stored document must be
    # protected from the sweeper (it is registered before the wait starts).
    upload = client.post("/api/documents", files={"file": ("a.pdf", make_pdf(2))})
    path = documents_dir / f"{upload.json()['id']}.pdf"
    seen = {}

    @contextlib.contextmanager
    def fake_heavy_job(megabytes, uploads=()):
        seen["protected"] = path in documents.ACTIVE_JOB_FILES
        yield

    monkeypatch.setattr(heavy_jobs, "heavy_job", fake_heavy_job)

    response = client.post(f"/api/documents/{upload.json()['id']}/edit", data={"pages": '[{"page": 1}]'})

    assert response.status_code == 200
    assert seen["protected"] is True


def test_big_jobs_return_freed_memory_with_malloc_trim(monkeypatch):
    trims = []
    monkeypatch.setattr(heavy_jobs, "MALLOC_TRIM", lambda pad: trims.append(pad) or 1)
    monkeypatch.setattr(heavy_jobs, "MALLOC_TRIM_DELAY_SECONDS", 0.01)

    with heavy_jobs.heavy_job(heavy_jobs.HEAVY_JOB_MIN_MB - 1):
        pass
    time.sleep(0.1)
    assert trims == []  # small jobs never trim

    with heavy_jobs.heavy_job(heavy_jobs.HEAVY_JOB_MIN_MB):
        pass
    with heavy_jobs.heavy_job(heavy_jobs.HEAVY_JOB_MIN_MB):
        pass
    wait_until(lambda: trims)
    time.sleep(0.1)
    assert trims == [0]  # two jobs ending together share one trim


def test_steady_big_jobs_do_not_postpone_the_trim_forever(monkeypatch):
    trims = []
    monkeypatch.setattr(heavy_jobs, "MALLOC_TRIM", lambda pad: trims.append(pad) or 1)
    monkeypatch.setattr(heavy_jobs, "MALLOC_TRIM_DELAY_SECONDS", 0.2)

    # A job ends every 0.05 s for longer than the delay: one trim still runs.
    deadline = time.monotonic() + 0.5
    while time.monotonic() < deadline:
        with heavy_jobs.heavy_job(heavy_jobs.HEAVY_JOB_MIN_MB):
            pass
        time.sleep(0.05)
    assert trims, "the trim kept being postponed"


def test_malloc_trim_is_optional(monkeypatch):
    # Windows and musl have no malloc_trim: releasing does nothing.
    monkeypatch.setattr(heavy_jobs, "MALLOC_TRIM", None)
    with heavy_jobs.heavy_job(heavy_jobs.HEAVY_JOB_MIN_MB):
        pass
    assert heavy_jobs.release_free_memory() is None


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="glibc only")
def test_malloc_trim_is_found_on_glibc():
    assert heavy_jobs.MALLOC_TRIM is not None
