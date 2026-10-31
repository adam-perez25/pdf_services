"""Large documents: 1500-page PDFs through every pypdf tool, the sweeper
leaving in-flight job files alone, scaled timeouts and configurable limits."""
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import zipfile

from pypdf import PdfReader, PdfWriter
import pytest

import app.main as main


PAGE_COUNT = 1500
ROOT = Path(__file__).resolve().parent.parent


def page_widths(content: bytes) -> list[int]:
    return [round(float(page.mediabox.width)) for page in PdfReader(BytesIO(content)).pages]


@pytest.fixture(scope="module")
def large_pdf() -> bytes:
    # Page n is 200 + n points wide, so reordered pages can be told apart.
    writer = PdfWriter()
    for n in range(1, PAGE_COUNT + 1):
        writer.add_blank_page(width=200 + n, height=300)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def upload(large_pdf: bytes, name: str = "grande.pdf") -> dict:
    return {"file": (name, large_pdf, "application/pdf")}


def test_pdf_info(client, large_pdf):
    response = client.post("/api/pdf-info", files=upload(large_pdf))

    assert response.status_code == 200
    assert response.json() == {"pages": PAGE_COUNT}


def test_merge(client, large_pdf, documents_dir):
    response = client.post(
        "/api/merge",
        files=[("files", (f"{n}.pdf", large_pdf, "application/pdf")) for n in range(2)],
        data={"output_name": "todo.pdf"},
    )

    assert response.status_code == 200
    assert response.headers["content-disposition"] == 'attachment; filename="todo.pdf"'
    assert len(PdfReader(BytesIO(response.content)).pages) == 2 * PAGE_COUNT
    assert list(documents_dir.iterdir()) == []


def test_split_zip_ranges(client, large_pdf, documents_dir):
    response = client.post(
        "/api/split-zip",
        files=upload(large_pdf),
        data={"mode": "ranges", "ranges": "1-700, 701-1500", "names": '["a", "b"]'},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(BytesIO(response.content)) as archive:
        assert archive.namelist() == ["a.pdf", "b.pdf"]
        first = page_widths(archive.read("a.pdf"))
        second = page_widths(archive.read("b.pdf"))
    assert (len(first), len(second)) == (700, 800)
    assert (first[0], second[0], second[-1]) == (201, 901, 200 + PAGE_COUNT)
    assert list(documents_dir.iterdir()) == []


def test_split_single_range(client, large_pdf, documents_dir):
    response = client.post(
        "/api/split-zip",
        files=upload(large_pdf),
        data={"mode": "ranges", "ranges": "2-1499", "names": '["medio"]'},
    )

    assert response.status_code == 200
    assert response.headers["content-disposition"] == 'attachment; filename="medio.pdf"'
    assert len(page_widths(response.content)) == PAGE_COUNT - 2
    assert list(documents_dir.iterdir()) == []


def test_organizer_upload_and_edit(client, large_pdf, documents_dir):
    upload_response = client.post("/api/documents", files=upload(large_pdf))
    assert upload_response.status_code == 200
    body = upload_response.json()
    assert body["pages"] == PAGE_COUNT

    thumbnail = client.get(f"/api/documents/{body['id']}/pages/{PAGE_COUNT}")
    assert thumbnail.status_code == 200

    # Reverse the order and drop every other page, rotating the first one.
    layout = [{"page": n, "rotation": 90 if n == PAGE_COUNT else 0} for n in range(PAGE_COUNT, 0, -2)]
    response = client.post(
        f"/api/documents/{body['id']}/edit",
        data={"pages": json.dumps(layout), "output_name": "ordenado.pdf"},
    )

    assert response.status_code == 200
    assert response.headers["content-disposition"] == 'attachment; filename="ordenado.pdf"'
    reader = PdfReader(BytesIO(response.content))
    assert len(reader.pages) == PAGE_COUNT // 2
    assert round(float(reader.pages[0].mediabox.width)) == 200 + PAGE_COUNT
    assert reader.pages[0].rotation == 90
    assert round(float(reader.pages[-1].mediabox.width)) == 202
    assert list(documents_dir.iterdir()) == []


def test_stamp_page_numbers(client, large_pdf, documents_dir):
    response = client.post(
        "/api/stamp",
        files=upload(large_pdf),
        data={"mode": "numbers", "number_format": "page-n-of-total"},
    )

    assert response.status_code == 200
    reader = PdfReader(BytesIO(response.content))
    assert len(reader.pages) == PAGE_COUNT
    assert f"Página {PAGE_COUNT} de {PAGE_COUNT}" in reader.pages[-1].extract_text()
    assert list(documents_dir.iterdir()) == []


def test_metadata(client, large_pdf, documents_dir):
    response = client.post("/api/metadata", files=upload(large_pdf), data={"title": "Informe anual"})

    assert response.status_code == 200
    reader = PdfReader(BytesIO(response.content))
    assert reader.metadata.title == "Informe anual"
    assert len(reader.pages) == PAGE_COUNT
    assert list(documents_dir.iterdir()) == []


def test_protect_and_unlock(client, large_pdf, documents_dir):
    protected = client.post("/api/protect", files=upload(large_pdf), data={"password": "secreto"})
    assert protected.status_code == 200
    assert PdfReader(BytesIO(protected.content)).is_encrypted

    unlocked = client.post(
        "/api/unlock",
        files={"file": ("p.pdf", protected.content, "application/pdf")},
        data={"password": "secreto"},
    )
    assert unlocked.status_code == 200
    reader = PdfReader(BytesIO(unlocked.content))
    assert not reader.is_encrypted
    assert len(reader.pages) == PAGE_COUNT
    assert list(documents_dir.iterdir()) == []


# --- Sweeper and in-flight jobs ---------------------------------------------

def make_old(path: Path) -> None:
    old = time.time() - main.DOCUMENT_TTL_SECONDS - 60
    os.utime(path, (old, old))


def test_sweeper_skips_files_of_running_jobs(documents_dir):
    running = documents_dir / f"{'a' * 32}.in.pdf"
    abandoned = documents_dir / f"{'b' * 32}.in.pdf"
    for path in (running, abandoned):
        path.write_bytes(b"%PDF")
        make_old(path)

    with main.protect_from_sweeper(running):
        main.remove_expired_documents()
        assert running.exists()
        assert not abandoned.exists()

    # Once the job ends the file gets a fresh mtime for its download, and is
    # swept normally if it is then left behind.
    assert running.stat().st_mtime > time.time() - 60
    main.remove_expired_documents()
    assert running.exists()
    make_old(running)
    main.remove_expired_documents()
    assert not running.exists()
    assert main.ACTIVE_JOB_FILES == set()


def test_slow_compression_survives_a_sweep(client, large_pdf, documents_dir, monkeypatch):
    # A fake Ghostscript that runs "for hours": while it works, every file in
    # the documents folder is already older than the TTL and a sweep runs.
    calls = {}

    def slow_tool(command, timeout):
        calls["timeout"] = timeout
        output = Path(next(arg for arg in command if arg.startswith("-sOutputFile=")).split("=", 1)[1])
        source = Path(command[-1])
        output.write_bytes(source.read_bytes()[: len(large_pdf) // 2])
        for path in documents_dir.iterdir():
            make_old(path)
        main.remove_expired_documents()
        calls["left"] = sorted(path.name for path in documents_dir.iterdir())
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(main, "find_ghostscript", lambda: "gs")
    monkeypatch.setattr(main, "run_tool", slow_tool)

    response = client.post("/api/compress", files=upload(large_pdf))

    assert response.status_code == 200
    assert response.headers["x-compression-applied"] == "true"
    assert len(calls["left"]) == 2  # <job>.in.pdf and <job>.out.pdf survived
    assert calls["timeout"] == main.compression_timeout(PAGE_COUNT)
    assert list(documents_dir.iterdir()) == []


def test_slow_split_zip_survives_a_sweep(client, large_pdf, documents_dir, monkeypatch):
    real_write = main.write_pdf_to_file

    def write_then_sweep(writer):
        # The ZIP being filled ages past the TTL while parts are still added;
        # the part just written is fresh, as it would be in reality.
        path = real_write(writer)
        for leftover in documents_dir.iterdir():
            if leftover != path:
                make_old(leftover)
        main.remove_expired_documents()
        # (On Windows an open file cannot be deleted anyway, so also check
        # that the ZIP is registered as in use.)
        zips = [p for p in documents_dir.iterdir() if p.suffix == ".zip"]
        assert len(zips) == 1 and zips[0] in main.ACTIVE_JOB_FILES
        return path

    monkeypatch.setattr(main, "write_pdf_to_file", write_then_sweep)
    response = client.post(
        "/api/split-zip",
        files=upload(large_pdf),
        data={"mode": "ranges", "ranges": "1-10, 11-20, 21-30"},
    )

    assert response.status_code == 200
    with zipfile.ZipFile(BytesIO(response.content)) as archive:
        assert len(archive.namelist()) == 3


# --- Timeouts ----------------------------------------------------------------

def test_compression_timeout_scales_with_pages(monkeypatch):
    monkeypatch.setattr(main, "COMPRESSION_TIMEOUT_SECONDS", 600)
    monkeypatch.setattr(main, "COMPRESSION_SECONDS_PER_PAGE", 2)
    monkeypatch.setattr(main, "COMPRESSION_MAX_TIMEOUT_SECONDS", 3600)

    assert main.compression_timeout(20) == 640
    assert main.compression_timeout(1000) == 2600
    assert main.compression_timeout(100_000) == 3600


def test_compression_timeout_cap_never_cuts_below_the_base(monkeypatch):
    monkeypatch.setattr(main, "COMPRESSION_TIMEOUT_SECONDS", 900)
    monkeypatch.setattr(main, "COMPRESSION_MAX_TIMEOUT_SECONDS", 300)

    assert main.compression_timeout(1000) == 900


# --- Configuration from environment variables -------------------------------

def test_env_int_uses_the_default_when_unset_or_empty(monkeypatch):
    monkeypatch.delenv("PDF_TEST_SETTING", raising=False)
    assert main.env_int("PDF_TEST_SETTING", 42, 1, 100) == 42
    monkeypatch.setenv("PDF_TEST_SETTING", "  ")
    assert main.env_int("PDF_TEST_SETTING", 42, 1, 100) == 42


def test_env_int_reads_a_valid_value(monkeypatch):
    monkeypatch.setenv("PDF_TEST_SETTING", " 75 ")
    assert main.env_int("PDF_TEST_SETTING", 42, 1, 100) == 75


@pytest.mark.parametrize("value, message", [("500MB", "no es un número entero"), ("1.5", "no es un número entero"), ("0", "entre 1 y 100"), ("101", "entre 1 y 100")])
def test_env_int_rejects_bad_values_clearly(monkeypatch, value, message):
    monkeypatch.setenv("PDF_TEST_SETTING", value)
    with pytest.raises(ValueError) as error:
        main.env_int("PDF_TEST_SETTING", 42, 1, 100)
    assert "PDF_TEST_SETTING" in str(error.value)
    assert message in str(error.value)


SETTINGS = [
    "MAX_UPLOAD_MB", "MAX_TOTAL_UPLOAD_MB", "OFFICE_TIMEOUT_SECONDS",
    "COMPRESSION_TIMEOUT_SECONDS", "COMPRESSION_SECONDS_PER_PAGE",
    "COMPRESSION_MAX_TIMEOUT_SECONDS", "DOCUMENT_TTL_SECONDS", "DOCUMENT_SWEEP_SECONDS",
]


def import_settings(**env: str) -> subprocess.CompletedProcess:
    """Import app.main in a fresh interpreter with the given variables set."""
    clean = {key: value for key, value in os.environ.items() if key not in SETTINGS}
    code = f"import json, app.main as m; print(json.dumps({{name: getattr(m, name) for name in {SETTINGS!r}}}))"
    return subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, env={**clean, **env},
        capture_output=True, text=True, timeout=120,
    )


def test_defaults_and_environment_overrides():
    defaults = import_settings()
    assert defaults.returncode == 0, defaults.stderr
    assert json.loads(defaults.stdout) == {
        "MAX_UPLOAD_MB": 500,
        "MAX_TOTAL_UPLOAD_MB": 1000,
        "OFFICE_TIMEOUT_SECONDS": 300,
        "COMPRESSION_TIMEOUT_SECONDS": 600,
        "COMPRESSION_SECONDS_PER_PAGE": 2,
        "COMPRESSION_MAX_TIMEOUT_SECONDS": 3600,
        "DOCUMENT_TTL_SECONDS": 1800,
        "DOCUMENT_SWEEP_SECONDS": 300,
    }

    # Without MAX_TOTAL_UPLOAD_MB, the total follows a raised per-file limit.
    raised = import_settings(MAX_UPLOAD_MB="1500")
    assert raised.returncode == 0, raised.stderr
    raised_values = json.loads(raised.stdout)
    assert (raised_values["MAX_UPLOAD_MB"], raised_values["MAX_TOTAL_UPLOAD_MB"]) == (1500, 1500)

    custom = import_settings(MAX_UPLOAD_MB="800", MAX_TOTAL_UPLOAD_MB="2000", COMPRESSION_SECONDS_PER_PAGE="5")
    assert custom.returncode == 0, custom.stderr
    values = json.loads(custom.stdout)
    assert (values["MAX_UPLOAD_MB"], values["MAX_TOTAL_UPLOAD_MB"]) == (800, 2000)
    assert values["COMPRESSION_SECONDS_PER_PAGE"] == 5


@pytest.mark.parametrize(
    "env, message",
    [
        ({"MAX_UPLOAD_MB": "mucho"}, "MAX_UPLOAD_MB"),
        ({"DOCUMENT_TTL_SECONDS": "5"}, "DOCUMENT_TTL_SECONDS"),
        ({"MAX_UPLOAD_MB": "600", "MAX_TOTAL_UPLOAD_MB": "500"}, "no puede ser menor"),
    ],
)
def test_invalid_settings_stop_the_service_with_a_clear_message(env, message):
    result = import_settings(**env)

    assert result.returncode != 0
    assert message in result.stderr


# --- Limits are read at request time ----------------------------------------

@pytest.fixture(scope="module")
def pdf_over_old_limit() -> bytes:
    # About 60 MB, over the old fixed 50 MB limit: one page plus an
    # incompressible attachment.
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=300)
    writer.add_attachment("relleno.bin", os.urandom(60 * 1024 * 1024))
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def test_configured_limits_are_respected(client, monkeypatch, pdf_over_old_limit):
    files = {"file": ("grande.pdf", pdf_over_old_limit, "application/pdf")}

    monkeypatch.setattr(main, "MAX_UPLOAD_MB", 100)
    monkeypatch.setattr(main, "MAX_TOTAL_UPLOAD_MB", 100)
    accepted = client.post("/api/pdf-info", files=files)
    assert accepted.status_code == 200
    assert accepted.json() == {"pages": 1}

    monkeypatch.setattr(main, "MAX_UPLOAD_MB", 50)
    rejected = client.post("/api/pdf-info", files=files)
    assert rejected.status_code == 413
    assert "50 MB" in rejected.json()["detail"]

    # The request-size middleware follows MAX_TOTAL_UPLOAD_MB too.
    monkeypatch.setattr(main, "MAX_TOTAL_UPLOAD_MB", 40)
    cut_off = client.post("/api/pdf-info", files=files)
    assert cut_off.status_code == 413
    assert "40 MB" in cut_off.json()["detail"]
