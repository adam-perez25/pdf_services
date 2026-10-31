from io import BytesIO
from pathlib import Path
import re

from pypdf import PdfReader, PdfWriter
import pytest

import app.main as main
from conftest import make_pdf

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_JS = ROOT / "static" / "workflow.js"
WORKFLOW_HTML = ROOT / "templates" / "workflow.html"

needs_ghostscript = pytest.mark.skipif(main.find_ghostscript() is None, reason="Ghostscript is not installed")


def make_pdf_with_metadata(page_count: int) -> bytes:
    writer = PdfWriter(clone_from=PdfReader(BytesIO(make_pdf(page_count))))
    writer.add_metadata({"/Title": "Informe", "/Author": "Ana García", "/Producer": "LibreOffice"})
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def run_step(client, endpoint: str, content: bytes, **data) -> bytes:
    # Mirrors workflow.js: the previous result is posted as "file" with a .pdf
    # name, plus the step's own fields and the final output_name.
    response = client.post(endpoint, files={"file": ("informe.pdf", content, "application/pdf")}, data=data)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/pdf"
    return response.content


def test_page_route(client):
    response = client.get("/workflow")

    assert response.status_code == 200
    assert "/static/common.js" in response.text
    assert "/static/workflow.js" in response.text
    assert "SERVICIO 16 / FLUJO DE TRABAJO" in response.text


def test_template_declares_color_scheme():
    assert '<meta name="color-scheme" content="light dark">' in WORKFLOW_HTML.read_text(encoding="utf-8")


def test_index_links_to_workflow(client):
    assert 'href="/workflow"' in client.get("/").text


def test_script_is_served(client):
    response = client.get("/static/workflow.js")

    assert response.status_code == 200
    assert "STEP_DEFINITIONS" in response.text


def test_script_only_calls_existing_endpoints():
    # Commented lines (the OCR example) do not count.
    code = "\n".join(
        line for line in WORKFLOW_JS.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("//")
    )
    endpoints = set(re.findall(r'"(/api/[^"]+)"', code))
    registered = {
        route.path for route in main.app.routes if "POST" in (getattr(route, "methods", None) or set())
    }

    assert {"/api/merge", "/api/stamp", "/api/metadata", "/api/compress", "/api/protect"} <= endpoints
    assert endpoints - registered == set()


def test_script_has_no_inline_handlers_or_password_in_urls():
    code = WORKFLOW_JS.read_text(encoding="utf-8")
    assert "console." not in code
    assert "?password" not in code
    html = WORKFLOW_HTML.read_text(encoding="utf-8")
    assert not re.search(r"\son[a-z]+=", html)
    assert "<script>" not in html


def test_merge_stamp_metadata_protect_chain(client):
    name = "informe-procesado.pdf"
    merge = client.post(
        "/api/merge",
        files=[
            ("files", ("informe.pdf", make_pdf_with_metadata(2), "application/pdf")),
            ("files", ("anexo.pdf", make_pdf(3), "application/pdf")),
        ],
        data={"output_name": name},
    )
    assert merge.status_code == 200
    result = merge.content

    result = run_step(
        client, "/api/stamp", result,
        mode="numbers", position="bottom-center", number_format="n", start_number="1", font_size="12",
        output_name=name,
    )
    result = run_step(client, "/api/metadata", result, remove_all="true", output_name=name)

    response = client.post(
        "/api/protect",
        files={"file": ("informe.pdf", result, "application/pdf")},
        data={"password": "s3creta", "output_name": name},
    )
    assert response.status_code == 200
    assert response.headers["content-disposition"] == f'attachment; filename="{name}"'

    reader = PdfReader(BytesIO(response.content))
    assert reader.is_encrypted
    assert reader.decrypt("s3creta")
    assert len(reader.pages) == 5
    assert [page.extract_text().strip() for page in reader.pages] == ["1", "2", "3", "4", "5"]
    info = reader.metadata or {}
    assert not any(key in info for key in ("/Title", "/Author", "/Producer"))
    assert "/Metadata" not in reader.trailer["/Root"]


def test_failed_step_keeps_previous_result(client):
    # A later step failing (here protect on an already protected PDF) leaves
    # the previous step's output usable, which the page offers to download.
    protected = run_step(client, "/api/protect", make_pdf(2), password="uno", output_name="x.pdf")

    response = client.post(
        "/api/protect",
        files={"file": ("informe.pdf", protected, "application/pdf")},
        data={"password": "dos", "output_name": "x.pdf"},
    )

    assert response.status_code == 400
    assert isinstance(response.json()["detail"], str)
    assert PdfReader(BytesIO(protected)).is_encrypted


@needs_ghostscript
def test_chain_with_compress(client):
    result = run_step(
        client, "/api/stamp", make_pdf(3),
        mode="watermark", text="BORRADOR", font_size="60", opacity="0.3", rotation="45", output_name="final.pdf",
    )
    response = client.post(
        "/api/compress",
        files={"file": ("informe.pdf", result, "application/pdf")},
        data={"level": "recommended", "output_name": "final.pdf"},
    )
    assert response.status_code == 200
    assert response.headers["x-compression-applied"] in {"true", "false"}
    result = run_step(client, "/api/protect", response.content, password="clave", output_name="final.pdf")

    reader = PdfReader(BytesIO(result))
    assert reader.is_encrypted
    assert reader.decrypt("clave")
    assert len(reader.pages) == 3
