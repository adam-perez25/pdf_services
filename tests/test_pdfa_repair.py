from io import BytesIO
from pathlib import Path
import subprocess

from pypdf import PdfReader, PdfWriter
import pytest

import app.config as config
import app.external_tools as external_tools
import app.heavy_jobs as heavy_jobs
import app.routers.pdfa as pdfa_router
from conftest import make_pdf

needs_ghostscript = pytest.mark.skipif(
    external_tools.find_ghostscript() is None or external_tools.find_srgb_profile() is None,
    reason="Ghostscript (with its sRGB profile) is not installed",
)


def pdfa(client, content: bytes, **data):
    return client.post("/api/pdfa", files={"file": ("informe.pdf", content)}, data=data)


def repair(client, content: bytes, **data):
    return client.post("/api/repair", files={"file": ("roto.pdf", content)}, data=data)


def xmp_text(pdf: bytes) -> str:
    root = PdfReader(BytesIO(pdf)).trailer["/Root"]
    return root["/Metadata"].get_object().get_data().decode("utf-8", "replace")


@needs_ghostscript
@pytest.mark.parametrize("part", ["1", "2", "3"])
def test_pdfa_adds_output_intent_and_identification(client, part):
    response = pdfa(client, make_pdf(3), part=part)

    assert response.status_code == 200
    assert response.headers["content-disposition"] == 'attachment; filename="informe-pdfa.pdf"'
    assert response.headers["x-pdfa-part"] == part
    root = PdfReader(BytesIO(response.content)).trailer["/Root"]
    assert len(PdfReader(BytesIO(response.content)).pages) == 3
    assert root["/OutputIntents"][0].get_object()["/S"] == "/GTS_PDFA1"
    assert f"pdfaid:part='{part}'" in xmp_text(response.content)


@needs_ghostscript
def test_pdfa_defaults_to_part_2_and_uses_the_given_name(client, documents_dir):
    response = pdfa(client, make_pdf(1), output_name="archivo oficial")

    assert response.status_code == 200
    assert response.headers["x-pdfa-part"] == "2"
    assert response.headers["content-disposition"] == 'attachment; filename="archivo oficial.pdf"'
    assert list(documents_dir.iterdir()) == []


@needs_ghostscript
def test_repair_recovers_pages_of_a_truncated_pdf(client, documents_dir):
    broken = make_pdf(3)[:-300]

    response = repair(client, broken)

    assert response.status_code == 200
    assert response.headers["content-disposition"] == 'attachment; filename="roto-reparado.pdf"'
    assert len(PdfReader(BytesIO(response.content)).pages) == 3
    assert list(documents_dir.iterdir()) == []


@needs_ghostscript
def test_repair_rejects_a_pdf_with_nothing_to_save(client, documents_dir):
    response = repair(client, b"%PDF-1.4\ngarbage")

    assert response.status_code == 400
    assert response.json()["detail"] == pdfa_router.REPAIR_FAILED_MESSAGE
    assert list(documents_dir.iterdir()) == []


def test_validation(client):
    assert pdfa(client, make_pdf(1), part="4").status_code == 400
    assert client.post("/api/pdfa", files={"file": ("a.txt", b"x")}).status_code == 400
    assert client.post("/api/repair", files={"file": ("a.txt", b"x")}).status_code == 400
    assert client.post("/api/pdfa").status_code == 400  # missing file


def fake_ghostscript(monkeypatch, tmp_path, tool=None):
    profile = tmp_path / "default_rgb.icc"
    profile.write_bytes(b"icc")
    monkeypatch.setattr(external_tools, "find_ghostscript", lambda: "gs")
    monkeypatch.setattr(external_tools, "find_srgb_profile", lambda: profile)
    if tool:
        monkeypatch.setattr(external_tools, "run_tool", tool)
    return profile


def test_missing_ghostscript_or_profile_is_503(client, tmp_path, monkeypatch):
    monkeypatch.setattr(external_tools, "find_ghostscript", lambda: None)
    assert pdfa(client, make_pdf(1)).status_code == 503
    assert repair(client, make_pdf(1)).status_code == 503

    monkeypatch.setattr(external_tools, "find_ghostscript", lambda: "gs")
    monkeypatch.setattr(external_tools, "find_srgb_profile", lambda: None)
    response = pdfa(client, make_pdf(1))
    assert response.status_code == 503
    assert response.json()["detail"] == pdfa_router.PDFA_MISSING_PROFILE_MESSAGE


def test_pdfa_command_is_sandboxed_and_reads_only_the_profile(client, tmp_path, monkeypatch):
    seen = {}

    def tool(command, timeout, env=None):
        seen["command"] = command
        seen["timeout"] = timeout
        definition = Path(command[-2])
        seen["definition"] = definition.read_text(encoding="utf-8")
        Path(next(arg for arg in command if arg.startswith("-sOutputFile=")).split("=", 1)[1]).write_bytes(make_pdf(1))
        return subprocess.CompletedProcess(command, 0, "", "")

    profile = fake_ghostscript(monkeypatch, tmp_path, tool)

    response = pdfa(client, make_pdf(2), part="3")

    assert response.status_code == 200
    command = seen["command"]
    assert "-dSAFER" in command
    assert "-dPDFA=3" in command
    assert f"--permit-file-read={profile.as_posix()}" in command
    assert f"({profile.as_posix()}) def" in seen["definition"]
    assert seen["timeout"] == pdfa_router.rewrite_timeout(2)


def test_ps_prefix_is_removed_before_ghostscript(client, tmp_path, monkeypatch):
    inputs = []

    def tool(command, timeout, env=None):
        inputs.append(Path(command[-1]).read_bytes()[:5])
        Path(next(arg for arg in command if arg.startswith("-sOutputFile=")).split("=", 1)[1]).write_bytes(make_pdf(1))
        return subprocess.CompletedProcess(command, 0, "", "")

    fake_ghostscript(monkeypatch, tmp_path, tool)
    hostile = b"%!PS\n(x) pop\n" + make_pdf(1)

    assert pdfa(client, hostile).status_code == 200
    assert repair(client, hostile).status_code == 200
    assert inputs == [b"%PDF-", b"%PDF-"]
    # No header at all is refused before Ghostscript runs.
    monkeypatch.setattr(external_tools, "run_tool", lambda *args, **kwargs: pytest.fail("Ghostscript must not run"))
    assert repair(client, b"%!PS\nquit\n").status_code == 400
    assert pdfa(client, b"%!PS\nquit\n").status_code == 400


def test_password_protected_pdf_is_refused(client, tmp_path, monkeypatch):
    writer = PdfWriter()
    writer.add_blank_page(100, 100)
    writer.encrypt("secreto")
    output = BytesIO()
    writer.write(output)
    fake_ghostscript(monkeypatch, tmp_path, lambda *args, **kwargs: pytest.fail("Ghostscript must not run"))

    for call in (pdfa, repair):
        response = call(client, output.getvalue())
        assert response.status_code == 400
        assert "contraseña" in response.json()["detail"].lower()


@pytest.mark.parametrize("call, expected", [(pdfa, "PDF/A"), (repair, "reparación")])
def test_timeout_is_504_and_leaves_no_files(client, documents_dir, tmp_path, monkeypatch, call, expected):
    def tool(command, timeout, env=None):
        raise subprocess.TimeoutExpired(command, timeout)

    fake_ghostscript(monkeypatch, tmp_path, tool)

    response = call(client, make_pdf(1))

    assert response.status_code == 504
    assert expected in response.json()["detail"]
    assert list(documents_dir.iterdir()) == []


def test_ghostscript_failure_does_not_leak_details(client, documents_dir, tmp_path, monkeypatch):
    def tool(command, timeout, env=None):
        return subprocess.CompletedProcess(command, 1, "", "secret internal path /srv/x")

    fake_ghostscript(monkeypatch, tmp_path, tool)

    for call in (pdfa, repair):
        response = call(client, make_pdf(1))
        assert response.status_code == 400
        assert "secret" not in response.text
    assert list(documents_dir.iterdir()) == []


def test_a_broken_pdf_is_a_400_for_pdfa_but_is_tried_by_repair(client, tmp_path, monkeypatch):
    ran = []

    def tool(command, timeout, env=None):
        ran.append(timeout)
        Path(next(arg for arg in command if arg.startswith("-sOutputFile=")).split("=", 1)[1]).write_bytes(make_pdf(1))
        return subprocess.CompletedProcess(command, 0, "", "")

    fake_ghostscript(monkeypatch, tmp_path, tool)
    broken = b"%PDF-1.4\nnot really a pdf"

    assert pdfa(client, broken).status_code == 400
    assert ran == []
    assert repair(client, broken).status_code == 200
    # Pages could not be counted, so repair gets the longest timeout.
    assert ran == [config.COMPRESSION_MAX_TIMEOUT_SECONDS]


def test_both_tools_queue_as_heavy_jobs():
    assert "pdfa" in heavy_jobs.HEAVY_JOB_ESTIMATES
    assert "repair" in heavy_jobs.HEAVY_JOB_ESTIMATES


def test_pages_are_served_and_linked(client):
    page = client.get("/pdfa")
    assert page.status_code == 200
    assert "<title>PDF/A y reparar PDF | PDF Toolkit</title>" in page.text
    assert 'name="color-scheme"' in page.text
    assert 'href="/pdfa"' in client.get("/").text
    assert client.get("/static/pdfa.js").status_code == 200


def test_cross_site_requests_are_refused(client):
    for path in ("/api/pdfa", "/api/repair"):
        response = client.post(path, files={"file": ("a.pdf", make_pdf(1))}, headers={"Sec-Fetch-Site": "cross-site"})
        assert response.status_code == 403
