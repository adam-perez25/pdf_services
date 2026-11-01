"""Cancelling long jobs: the client leaving or pressing Cancelar stops the tool."""
import socket
import subprocess
import sys
import threading
import time

import httpx
import pytest
import uvicorn

from app import cancellation, external_tools, heavy_jobs
from app.cancellation import JobCancelled
import app.config as config
import app.main as main
from tests.conftest import make_pdf


JOB_ID = "a" * 32
SLEEPER = [sys.executable, "-c", "import time; time.sleep(60)"]


class FakeRequest:
    def __init__(self, job_id=None):
        self.headers = {cancellation.JOB_ID_HEADER: job_id} if job_id else {}
        self.scope = {}


def test_run_tool_stops_the_process_when_cancelled():
    flag = threading.Event()
    token = cancellation._current_check.set(flag.is_set)
    try:
        threading.Timer(0.5, flag.set).start()
        started = time.monotonic()
        with pytest.raises(JobCancelled):
            external_tools.run_tool(SLEEPER, timeout=30)
        assert time.monotonic() - started < 10
    finally:
        cancellation._current_check.reset(token)


def test_run_tool_without_a_check_still_times_out():
    with pytest.raises(subprocess.TimeoutExpired):
        external_tools.run_tool(SLEEPER, timeout=1)


def test_cancel_job_sets_the_registered_event():
    with cancellation.cancellable_job(FakeRequest(JOB_ID)):
        assert cancellation.current_check()() is False
        assert cancellation.cancel_job(JOB_ID) is True
        assert cancellation.current_check()() is True
        with pytest.raises(JobCancelled):
            cancellation.raise_if_cancelled()
    # Once the job ends the id is forgotten and the check is gone.
    assert cancellation.cancel_job(JOB_ID) is False
    assert cancellation.current_check() is None


def test_a_malformed_job_id_is_not_registered():
    with cancellation.cancellable_job(FakeRequest("../etc")):
        assert cancellation.cancel_job("../etc") is False


def test_cancel_endpoint(client):
    assert client.post(f"/api/jobs/{JOB_ID}/cancel").json() == {"cancelled": False}
    assert client.post("/api/jobs/not-an-id/cancel").status_code == 404


def test_cancel_endpoint_is_covered_by_the_cross_site_guard(client):
    response = client.post(f"/api/jobs/{JOB_ID}/cancel", headers={"Sec-Fetch-Site": "cross-site"})
    assert response.status_code == 403


def test_a_cancelled_job_leaves_the_heavy_job_queue():
    limiter = heavy_jobs.HeavyJobLimiter()
    assert limiter.acquire(1000, 1000, 5)  # fills the budget
    with pytest.raises(JobCancelled):
        limiter.acquire(500, 1000, 30, cancel_check=lambda: True)
    assert limiter.status() == {"running": 1, "reserved_mb": 1000, "waiting": 0}


def test_heavy_job_closes_the_upload_when_cancelled(monkeypatch):
    closed = []

    class Upload:
        class file:
            close = staticmethod(lambda: closed.append(True))

    limiter = heavy_jobs.HeavyJobLimiter()
    assert limiter.acquire(1000, 1000, 5)
    monkeypatch.setattr(heavy_jobs, "HEAVY_JOBS", limiter)
    monkeypatch.setattr(config, "HEAVY_JOB_BUDGET_MB", 1000)
    token = cancellation._current_check.set(lambda: True)
    try:
        with pytest.raises(JobCancelled):
            with heavy_jobs.heavy_job(500, [Upload]):
                pytest.fail("must not run")
    finally:
        cancellation._current_check.reset(token)
    assert closed == [True]


def test_cancelled_compression_removes_its_files(client, documents_dir, monkeypatch):
    def cancelled_tool(command, timeout, env=None):
        raise JobCancelled()

    monkeypatch.setattr(external_tools, "find_ghostscript", lambda: "gs")
    monkeypatch.setattr(external_tools, "run_tool", cancelled_tool)

    response = client.post("/api/compress", files={"file": ("a.pdf", make_pdf(2))})

    assert response.status_code == 499
    assert response.json()["detail"] == cancellation.CANCELLED_MESSAGE
    assert list(documents_dir.iterdir()) == []


@pytest.fixture
def live_server(documents_dir):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(main.app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    yield port
    server.should_exit = True
    thread.join(timeout=10)


def wait_for(outcome: dict, key: str, seconds: float = 15):
    deadline = time.monotonic() + seconds
    while key not in outcome and time.monotonic() < deadline:
        time.sleep(0.05)


@pytest.fixture
def sleeping_compress(monkeypatch):
    """Compress whose Ghostscript is a real process that sleeps; the dict
    records when it started and how the wait ended."""
    real_run_tool = external_tools.run_tool
    outcome = {}

    def slow_tool(command, timeout, env=None):
        outcome["started"] = time.monotonic()
        try:
            real_run_tool(SLEEPER, timeout=60, env=env)
        except BaseException as error:
            outcome["error"] = type(error).__name__
            outcome["ended"] = time.monotonic()
            raise

    monkeypatch.setattr(external_tools, "find_ghostscript", lambda: "gs")
    monkeypatch.setattr(external_tools, "run_tool", slow_tool)
    return outcome


def test_closing_the_connection_stops_the_tool(live_server, sleeping_compress):
    boundary = "xx"
    payload = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="a.pdf"\r\n'
        "Content-Type: application/pdf\r\n\r\n"
    ).encode() + make_pdf(2) + f"\r\n--{boundary}--\r\n".encode()
    request = (
        "POST /api/compress HTTP/1.1\r\nHost: 127.0.0.1\r\n"
        f"Content-Type: multipart/form-data; boundary={boundary}\r\n"
        f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n"
    ).encode() + payload

    with socket.create_connection(("127.0.0.1", live_server)) as sock:
        sock.sendall(request)
        wait_for(sleeping_compress, "started")
        assert "started" in sleeping_compress
        sock.shutdown(socket.SHUT_RDWR)
    wait_for(sleeping_compress, "ended")

    assert sleeping_compress.get("error") == "JobCancelled"
    assert sleeping_compress["ended"] - sleeping_compress["started"] < 10


def test_the_cancel_button_stops_the_tool(live_server, sleeping_compress):
    base = f"http://127.0.0.1:{live_server}"
    result = {}

    def post():
        result["response"] = httpx.post(
            f"{base}/api/compress",
            files={"file": ("a.pdf", make_pdf(2))},
            headers={cancellation.JOB_ID_HEADER: JOB_ID},
            timeout=30,
        )

    thread = threading.Thread(target=post)
    thread.start()
    wait_for(sleeping_compress, "started")
    assert httpx.post(f"{base}/api/jobs/{JOB_ID}/cancel").json() == {"cancelled": True}
    thread.join(timeout=20)

    assert sleeping_compress["error"] == "JobCancelled"
    assert result["response"].status_code == 499


def test_the_client_gone_flag_cancels_the_job():
    request = FakeRequest()
    with cancellation.cancellable_job(request):
        assert cancellation.current_check()() is False
        request.scope[cancellation.CLIENT_GONE_KEY] = threading.Event()
        assert cancellation.current_check()() is False
        request.scope[cancellation.CLIENT_GONE_KEY].set()
        assert cancellation.current_check()() is True


def test_the_page_sends_the_header_and_route_the_server_expects():
    script = (config.BASE_DIR / "static" / "common.js").read_text(encoding="utf-8")
    assert f'"{cancellation.JOB_ID_HEADER}"' in script
    assert "/api/jobs/${jobId}/cancel" in script


@pytest.mark.parametrize("path", ["/api/pdfa", "/api/repair"])
def test_cancelled_pdfa_and_repair_remove_their_files(client, documents_dir, tmp_path, monkeypatch, path):
    profile = tmp_path / "default_rgb.icc"
    profile.write_bytes(b"icc")

    def cancelled_tool(command, timeout, env=None):
        raise JobCancelled()

    monkeypatch.setattr(external_tools, "find_ghostscript", lambda: "gs")
    monkeypatch.setattr(external_tools, "find_srgb_profile", lambda: profile)
    monkeypatch.setattr(external_tools, "run_tool", cancelled_tool)

    response = client.post(path, files={"file": ("a.pdf", make_pdf(2))})

    assert response.status_code == 499
    assert list(documents_dir.iterdir()) == []
