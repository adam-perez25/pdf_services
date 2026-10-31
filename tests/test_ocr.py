from io import BytesIO
import subprocess

from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader, PdfWriter
import pytest

import app.main as main
from conftest import make_pdf

needs_tesseract = pytest.mark.skipif(not main.ocr_available(), reason="ocrmypdf or Tesseract is not installed")


def make_image_only_pdf(words: list[str]) -> bytes:
    # One "scanned" page per word: big black text on white, no text layer.
    # A real sans font reads much more reliably than Pillow's built-in one;
    # the Docker image has Liberation (installed for LibreOffice).
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", 100)
    except OSError:
        font = ImageFont.load_default(size=100)
    pages = []
    for word in words:
        page = Image.new("RGB", (1654, 2339), "white")  # A4 at 200 dpi
        ImageDraw.Draw(page).text((150, 300), word, fill="black", font=font)
        pages.append(page)
    output = BytesIO()
    pages[0].save(output, format="PDF", save_all=True, append_images=pages[1:], resolution=200)
    return output.getvalue()


def extracted_text(content: bytes) -> str:
    # The invisible OCR text can come out with stray spaces inside words.
    text = " ".join(page.extract_text() or "" for page in PdfReader(BytesIO(content)).pages)
    return "".join(text.split()).upper()


def ocr(client, content: bytes, filename: str = "escaneo.pdf", **data):
    return client.post("/api/ocr", files={"file": (filename, content)}, data=data)


def encrypted_pdf_bytes() -> bytes:
    writer = PdfWriter(clone_from=PdfReader(BytesIO(make_pdf(1))))
    writer.encrypt("secreto")
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


@pytest.fixture
def ocr_ready(monkeypatch):
    # Lets the validation tests run without ocrmypdf or Tesseract installed.
    monkeypatch.setattr(main, "ocr_available", lambda: True)


@needs_tesseract
def test_scanned_pdf_becomes_searchable(client, documents_dir):
    original = make_image_only_pdf(["PRESUPUESTO"])
    assert "PRESUPUESTO" not in extracted_text(original)

    response = ocr(client, original)

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == 'attachment; filename="escaneo-ocr.pdf"'
    assert "PRESUPUESTO" in extracted_text(response.content)
    assert list(documents_dir.iterdir()) == []


@needs_tesseract
def test_english_and_custom_name(client):
    response = ocr(client, make_image_only_pdf(["INVOICE", "TOTAL"]), language="eng", output_name="buscable")

    assert response.status_code == 200
    assert response.headers["content-disposition"] == 'attachment; filename="buscable.pdf"'
    text = extracted_text(response.content)
    assert "INVOICE" in text and "TOTAL" in text
    assert len(PdfReader(BytesIO(response.content)).pages) == 2


@needs_tesseract
def test_pages_with_text_are_kept(client):
    # A PDF that already has a text layer is not an error with --skip-text.
    writer = PdfWriter()
    writer.add_blank_page(300, 300)
    output = BytesIO()
    writer.write(output)

    response = ocr(client, output.getvalue(), language="spa+eng")

    assert response.status_code == 200
    assert len(PdfReader(BytesIO(response.content)).pages) == 1


def test_page_route(client):
    response = client.get("/ocr")

    assert response.status_code == 200
    assert 'name="color-scheme"' in response.text
    assert "/static/ocr.js" in response.text


def test_home_links_to_ocr(client):
    assert 'href="/ocr"' in client.get("/").text


def test_invalid_language_is_rejected(client, ocr_ready, documents_dir):
    response = ocr(client, make_pdf(1), language="fra")

    assert response.status_code == 400
    assert list(documents_dir.iterdir()) == []


def test_non_pdf_is_rejected(client, ocr_ready):
    assert ocr(client, b"x", filename="foto.jpg").status_code == 400


def test_corrupt_pdf_is_rejected(client, ocr_ready, documents_dir):
    response = ocr(client, b"%PDF-1.4 not really a pdf")

    assert response.status_code == 400
    assert "not really" not in response.text
    assert list(documents_dir.iterdir()) == []


def test_encrypted_pdf_is_rejected(client, ocr_ready, documents_dir):
    response = ocr(client, encrypted_pdf_bytes())

    assert response.status_code == 400
    assert response.json()["detail"] == main.ENCRYPTED_PDF_MESSAGE
    assert list(documents_dir.iterdir()) == []


def test_too_many_pages_is_rejected(client, ocr_ready, monkeypatch, documents_dir):
    monkeypatch.setattr(main, "OCR_MAX_PAGES", 2)

    response = ocr(client, make_pdf(3))

    assert response.status_code == 400
    assert list(documents_dir.iterdir()) == []


def test_missing_tesseract_is_reported(client, monkeypatch, documents_dir):
    monkeypatch.setattr(main, "find_tesseract", lambda: None)

    response = ocr(client, make_pdf(1))

    assert response.status_code == 503
    assert list(documents_dir.iterdir()) == []


def test_missing_ocrmypdf_is_reported(client, monkeypatch):
    monkeypatch.setattr(main, "find_tesseract", lambda: "/usr/bin/tesseract")
    monkeypatch.setattr(main, "ocrmypdf_installed", lambda: False)

    assert ocr(client, make_pdf(1)).status_code == 503


@pytest.mark.parametrize(
    ("exit_code", "status", "detail"),
    [
        (main.OCR_EXIT_ALREADY_DONE, 400, None),
        (main.OCR_EXIT_ENCRYPTED, 400, main.ENCRYPTED_PDF_MESSAGE),
        (main.OCR_EXIT_INVALID_CONFIG, 503, main.OCR_MISSING_MESSAGE),
        (15, 400, "No se pudo aplicar el OCR al PDF."),
    ],
)
def test_ocrmypdf_exit_codes(client, ocr_ready, monkeypatch, documents_dir, exit_code, status, detail):
    def fake_run_tool(command, timeout):
        return subprocess.CompletedProcess(command, exit_code, "", "/secret/path traceback")

    monkeypatch.setattr(main, "run_tool", fake_run_tool)

    response = ocr(client, make_pdf(1))

    assert response.status_code == status
    if detail:
        assert response.json()["detail"] == detail
    assert "/secret/path" not in response.text
    assert list(documents_dir.iterdir()) == []


def test_timeout_is_reported(client, ocr_ready, monkeypatch, documents_dir):
    seen = {}

    def slow_run_tool(command, timeout):
        seen["timeout"] = timeout
        raise subprocess.TimeoutExpired(command, timeout)

    monkeypatch.setattr(main, "run_tool", slow_run_tool)

    response = ocr(client, make_pdf(2))

    assert response.status_code == 504
    assert seen["timeout"] == main.ocr_timeout(2)
    assert list(documents_dir.iterdir()) == []


def test_timeout_grows_with_pages_and_is_capped():
    assert main.ocr_timeout(1) < main.ocr_timeout(10)
    assert main.ocr_timeout(10_000) == main.OCR_TIMEOUT_MAX_SECONDS
