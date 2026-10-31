from io import BytesIO
from pathlib import Path
import subprocess

from openpyxl import Workbook
from pptx import Presentation
import pytest

import app.main as main
from conftest import make_pdf, page_layout


needs_libreoffice = pytest.mark.skipif(main.find_soffice() is None, reason="LibreOffice is not installed")


def make_xlsx(sheet_count: int) -> bytes:
    workbook = Workbook()
    workbook.active.title = "Hoja 1"
    for number in range(2, sheet_count + 1):
        workbook.create_sheet(f"Hoja {number}")
    for number, sheet in enumerate(workbook.worksheets, start=1):
        sheet["A1"] = f"Hoja {number}"
        sheet["B1"] = number * 10
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def make_pptx(slide_count: int) -> bytes:
    presentation = Presentation()
    for number in range(1, slide_count + 1):
        slide = presentation.slides.add_slide(presentation.slide_layouts[1])
        slide.shapes.title.text = f"Diapositiva {number}"
    output = BytesIO()
    presentation.save(output)
    return output.getvalue()


@pytest.mark.parametrize(
    ("endpoint", "filename"),
    [
        ("/api/excel-to-pdf", "notes.docx"),
        ("/api/excel-to-pdf", "data.csv"),
        ("/api/powerpoint-to-pdf", "book.xlsx"),
        ("/api/powerpoint-to-pdf", "slides.key"),
    ],
)
def test_office_rejects_other_extensions(client, endpoint, filename):
    response = client.post(endpoint, files={"file": (filename, b"hello")})

    assert response.status_code == 400


@pytest.mark.parametrize(
    ("endpoint", "filename"),
    [("/api/excel-to-pdf", "a.xlsx"), ("/api/powerpoint-to-pdf", "a.pptx")],
)
def test_office_reports_missing_libreoffice(client, monkeypatch, endpoint, filename):
    monkeypatch.setattr(main, "find_soffice", lambda: None)

    response = client.post(endpoint, files={"file": (filename, b"PK\x03\x04content")})

    assert response.status_code == 503


@pytest.mark.parametrize(
    ("endpoint", "filename"),
    [
        ("/api/word-to-pdf", "a.docx"),
        ("/api/word-to-pdf", "a.doc"),
        ("/api/excel-to-pdf", "a.xls"),
        ("/api/powerpoint-to-pdf", "a.pptx"),
    ],
)
def test_office_rejects_empty_files(client, monkeypatch, endpoint, filename):
    monkeypatch.setattr(main, "find_soffice", lambda: "/usr/bin/soffice")

    response = client.post(endpoint, files={"file": (filename, b"")})

    assert response.status_code == 400
    assert response.json()["detail"] == "El archivo está vacío."


@pytest.mark.parametrize(
    ("endpoint", "filename", "kind"),
    [
        ("/api/word-to-pdf", "a.docx", "Word"),
        ("/api/excel-to-pdf", "a.xlsx", "Excel"),
        ("/api/powerpoint-to-pdf", "a.pptx", "PowerPoint"),
    ],
)
def test_office_rejects_packages_that_are_not_zip_or_ole(client, monkeypatch, endpoint, filename, kind):
    def no_libreoffice(*args, **kwargs):
        raise AssertionError("LibreOffice must not run")

    monkeypatch.setattr(main, "find_soffice", lambda: "/usr/bin/soffice")
    monkeypatch.setattr(main, "run_tool", no_libreoffice)

    response = client.post(endpoint, files={"file": (filename, b"hello, this is plain text")})

    assert response.status_code == 400
    assert response.json()["detail"] == f"El archivo no parece un documento de {kind} válido."


@pytest.mark.parametrize(
    ("filename", "content"),
    [
        ("a.docx", b"PK\x03\x04rest of a zip"),
        ("a.docx", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1old binary"),  # OLE saved as .docx
        ("a.doc", b"{\\rtf1 an RTF export}"),  # legacy extensions stay permissive
    ],
)
def test_office_accepts_zip_ole_and_legacy_content(client, monkeypatch, documents_dir, filename, content):
    seen = {}

    def fake_soffice(command, timeout, env=None):
        seen["ran"] = True
        outdir = Path(command[command.index("--outdir") + 1])
        (outdir / "source.pdf").write_bytes(make_pdf(1))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(main, "find_soffice", lambda: "/usr/bin/soffice")
    monkeypatch.setattr(main, "run_tool", fake_soffice)

    response = client.post("/api/word-to-pdf", files={"file": (filename, content)})

    assert response.status_code == 200
    assert seen["ran"]


@pytest.mark.parametrize("output_name", [None, "", "document.pdf"])
def test_default_name_comes_from_the_uploaded_file(client, monkeypatch, documents_dir, output_name):
    def fake_soffice(command, timeout, env=None):
        outdir = Path(command[command.index("--outdir") + 1])
        (outdir / "source.pdf").write_bytes(make_pdf(1))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(main, "find_soffice", lambda: "/usr/bin/soffice")
    monkeypatch.setattr(main, "run_tool", fake_soffice)
    data = {} if output_name is None else {"output_name": output_name}

    response = client.post("/api/word-to-pdf", files={"file": ("Informe año ñ.docx", b"PK\x03\x04x")}, data=data)

    assert response.status_code == 200
    assert response.headers["content-disposition"] == (
        "attachment; filename=\"Informe ano n.pdf\"; filename*=UTF-8''Informe%20a%C3%B1o%20%C3%B1.pdf"
    )
    assert list(documents_dir.iterdir()) == []


def test_office_pages_prefill_the_name_from_the_file():
    root = Path(__file__).resolve().parent.parent
    script = (root / "static" / "office.js").read_text(encoding="utf-8")
    assert "officeOutputName.value = `${file.name" in script
    for page in ("word", "excel", "powerpoint"):
        html = (root / "templates" / f"{page}.html").read_text(encoding="utf-8")
        assert not any(old in html for old in main.OFFICE_OLD_DEFAULT_NAMES)


@needs_libreoffice
def test_excel_converts_every_sheet(client):
    response = client.post(
        "/api/excel-to-pdf",
        files={"file": ("book.xlsx", make_xlsx(2))},
        data={"output_name": "informe"},
    )

    assert response.status_code == 200
    assert response.headers["content-disposition"] == 'attachment; filename="informe.pdf"'
    assert len(page_layout(response.content)) == 2


@needs_libreoffice
def test_powerpoint_converts_one_page_per_slide(client):
    response = client.post("/api/powerpoint-to-pdf", files={"file": ("deck.pptx", make_pptx(3))})

    assert response.status_code == 200
    assert response.content.startswith(b"%PDF")
    assert len(page_layout(response.content)) == 3

