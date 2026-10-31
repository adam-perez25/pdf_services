from io import BytesIO
import os
from pathlib import Path
import subprocess
import time

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
        (main.OCR_EXIT_ENCRYPTED, 400, main.ENCRYPTED_PDF_MESSAGE),
        (main.OCR_EXIT_INVALID_CONFIG, 503, main.OCR_MISSING_MESSAGE),
        (15, 400, "No se pudo aplicar el OCR al PDF."),
    ],
)
def test_ocrmypdf_exit_codes(client, ocr_ready, monkeypatch, documents_dir, exit_code, status, detail):
    def fake_run_tool(command, timeout, env=None):
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

    def slow_run_tool(command, timeout, env=None):
        seen["timeout"] = timeout
        seen["tmpdir"] = env["TMPDIR"]
        raise subprocess.TimeoutExpired(command, timeout)

    monkeypatch.setattr(main, "run_tool", slow_run_tool)

    response = ocr(client, make_pdf(2))

    assert response.status_code == 504
    assert seen["timeout"] == main.ocr_timeout(2)
    assert list(documents_dir.iterdir()) == []
    # ocrmypdf's private scratch folder is removed even though it was killed.
    assert not os.path.exists(seen["tmpdir"])


def test_timeout_grows_with_pages_and_is_capped():
    assert main.ocr_timeout(1) < main.ocr_timeout(10)
    assert main.ocr_timeout(10_000) == main.OCR_TIMEOUT_MAX_SECONDS


def test_long_ocr_survives_a_sweep(client, ocr_ready, monkeypatch, documents_dir):
    # A fake ocrmypdf that runs "for hours": while it works, its files are
    # older than the TTL and a sweep runs, but they must not be deleted.
    seen = {}

    def slow_run_tool(command, timeout, env=None):
        source, output = Path(command[-2]), Path(command[-1])
        output.write_bytes(source.read_bytes())
        old = time.time() - main.DOCUMENT_TTL_SECONDS - 60
        for path in documents_dir.iterdir():
            os.utime(path, (old, old))
        main.remove_expired_documents()
        seen["left"] = sorted(path.name for path in documents_dir.iterdir())
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(main, "run_tool", slow_run_tool)

    response = ocr(client, make_pdf(2))

    assert response.status_code == 200
    assert len(seen["left"]) == 2  # <job>.in.pdf and <job>.out.pdf survived
    assert list(documents_dir.iterdir()) == []


def test_ocr_limits_allow_big_scans():
    assert main.OCR_MAX_PAGES >= 1000
    assert main.ocr_timeout(500) > 60 * 60


def make_mixed_pdf() -> bytes:
    # Page 1 digital (no image), pages 2 and 3 scans: only they have images.
    writer = PdfWriter()
    writer.add_blank_page(300, 300)
    for page in PdfReader(BytesIO(make_image_only_pdf(["NUMERADO", "ESCANEADO"]))).pages:
        writer.add_page(page)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def stamped(client, content: bytes) -> bytes:
    # As if it went through Numerar páginas first: the page has a little text.
    response = client.post("/api/stamp", files={"file": ("a.pdf", content)}, data={"mode": "numbers"})
    assert response.status_code == 200
    return response.content


def capture_command(monkeypatch) -> dict:
    seen = {}

    def fake_run_tool(command, timeout, env=None):
        seen["command"] = command
        Path(command[-1]).write_bytes(Path(command[-2]).read_bytes())
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(main, "run_tool", fake_run_tool)
    return seen


def test_page_list_compresses_ranges():
    assert main.page_list([1, 2, 3, 5, 7, 8]) == "1-3,5,7-8"
    assert main.page_list([4]) == "4"
    assert main.page_list([]) == ""


def test_only_pages_with_images_are_redone(client, ocr_ready, monkeypatch, documents_dir):
    seen = capture_command(monkeypatch)

    response = ocr(client, make_mixed_pdf())

    assert response.status_code == 200
    command = seen["command"]
    assert "--redo-ocr" in command and "--skip-text" not in command
    assert command[command.index("--pages") + 1] == "2-3"
    assert "--rotate-pages" in command
    assert list(documents_dir.iterdir()) == []


def test_digital_pdf_is_skipped_not_redone(client, ocr_ready, monkeypatch):
    seen = capture_command(monkeypatch)
    # conftest's make_pdf pages are Pillow images, so build image-free pages.
    writer = PdfWriter()
    for _ in range(3):
        writer.add_blank_page(300, 300)
    digital = BytesIO()
    writer.write(digital)

    response = ocr(client, digital.getvalue())

    assert response.status_code == 200
    command = seen["command"]
    assert "--skip-text" in command
    assert "--redo-ocr" not in command and "--pages" not in command
    assert "--rotate-pages" in command


def test_pdf_with_form_fields_falls_back_to_skip_text(client, ocr_ready, monkeypatch):
    # ocrmypdf refuses --redo-ocr on fillable forms.
    from pypdf.generic import ArrayObject, DictionaryObject, NameObject

    writer = PdfWriter(clone_from=PdfReader(BytesIO(make_image_only_pdf(["FORMULARIO"]))))
    field = writer._add_object(DictionaryObject({NameObject("/FT"): NameObject("/Tx")}))
    writer._root_object[NameObject("/AcroForm")] = DictionaryObject({NameObject("/Fields"): ArrayObject([field])})
    output = BytesIO()
    writer.write(output)
    seen = capture_command(monkeypatch)

    response = ocr(client, output.getvalue())

    assert response.status_code == 200
    assert "--skip-text" in seen["command"] and "--redo-ocr" not in seen["command"]


def test_unicode_download_name(client, ocr_ready, monkeypatch):
    capture_command(monkeypatch)

    response = ocr(client, make_pdf(1), filename="Escáner año.pdf")

    assert response.headers["content-disposition"] == (
        "attachment; filename=\"Escaner ano-ocr.pdf\"; filename*=UTF-8''Esc%C3%A1ner%20a%C3%B1o-ocr.pdf"
    )


@needs_tesseract
def test_scanned_page_with_a_stamped_number_is_ocred(client):
    # --skip-text used to leave such a page alone because it already had text.
    original = stamped(client, make_image_only_pdf(["PRESUPUESTO"]))
    assert "PRESUPUESTO" not in extracted_text(original)

    response = ocr(client, original)

    assert response.status_code == 200
    assert extracted_text(response.content).count("PRESUPUESTO") == 1


@needs_tesseract
@pytest.mark.parametrize("angle", [90, 180])
def test_sideways_and_upside_down_scans_are_read(client, angle):
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", 48)
    except OSError:
        pytest.skip("Liberation Sans is needed for a reliable orientation test")
    page = Image.new("RGB", (1654, 2339), "white")
    draw = ImageDraw.Draw(page)
    # Orientation detection needs a page with a fair amount of text.
    for line in range(20):
        draw.text((150, 200 + line * 90), f"El presupuesto anual de la biblioteca {line}", fill="black", font=font)
    output = BytesIO()
    page.rotate(angle, expand=True).save(output, format="PDF", resolution=200)

    response = ocr(client, output.getvalue())

    assert response.status_code == 200
    assert "PRESUPUESTO" in extracted_text(response.content)


@needs_tesseract
def test_digital_text_is_kept_and_not_duplicated(client):
    writer = PdfWriter()
    for _ in range(2):
        writer.add_blank_page(300, 300)
    digital = BytesIO()
    writer.write(digital)
    text_page = stamped(client, digital.getvalue())
    before = extracted_text(text_page)
    assert before

    response = ocr(client, text_page)

    assert response.status_code == 200
    assert extracted_text(response.content) == before


def test_redo_ocr_refusal_falls_back_to_skip_text(client, ocr_ready, monkeypatch, documents_dir):
    # Some PDFs make ocrmypdf refuse --redo-ocr: the request retries plainly.
    commands = []

    def fake_run_tool(command, timeout, env=None):
        commands.append(command)
        if "--redo-ocr" in command:
            return subprocess.CompletedProcess(command, 2, "", "--redo-ocr is not supported for this PDF")
        Path(command[-1]).write_bytes(Path(command[-2]).read_bytes())
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(main, "run_tool", fake_run_tool)

    response = ocr(client, make_mixed_pdf())

    assert response.status_code == 200
    assert len(commands) == 2
    assert "--redo-ocr" in commands[0] and "--skip-text" in commands[1]
    assert list(documents_dir.iterdir()) == []
