from io import BytesIO

from PIL import Image
from pypdf import PdfReader
import pytest

import app.main as main
from conftest import make_pdf

needs_ghostscript = pytest.mark.skipif(main.find_ghostscript() is None, reason="Ghostscript is not installed")


def make_scanned_pdf(page_count: int = 2) -> bytes:
    # A4 pages "scanned" at 300 dpi: big noisy images, like a real scan.
    pages = [Image.effect_noise((2480, 3508), 40).convert("RGB") for _ in range(page_count)]
    output = BytesIO()
    pages[0].save(output, format="PDF", save_all=True, append_images=pages[1:], resolution=300)
    return output.getvalue()


@pytest.fixture(scope="module")
def scanned_pdf() -> bytes:
    return make_scanned_pdf()


def compress(client, content: bytes, **data):
    return client.post("/api/compress", files={"file": ("escaneo.pdf", content)}, data=data)


@needs_ghostscript
def test_scanned_pdf_gets_much_smaller(client, scanned_pdf):
    response = compress(client, scanned_pdf, output_name="ligero")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == 'attachment; filename="ligero.pdf"'
    assert response.headers["x-compression-applied"] == "true"
    assert int(response.headers["x-original-size"]) == len(scanned_pdf)
    assert int(response.headers["x-compressed-size"]) == len(response.content)
    assert len(response.content) < len(scanned_pdf) * 0.5
    assert len(PdfReader(BytesIO(response.content)).pages) == 2


@needs_ghostscript
def test_stronger_levels_give_smaller_files(client, scanned_pdf):
    sizes = {level: len(compress(client, scanned_pdf, level=level).content) for level in main.COMPRESSION_LEVELS}

    assert sizes["strong"] < sizes["recommended"] < sizes["high"]


@needs_ghostscript
def test_result_is_never_bigger_than_the_original(client):
    # A tiny, already optimal PDF: if Ghostscript cannot shrink it, the
    # original comes back unchanged.
    original = make_pdf(1)

    response = compress(client, original, level="high")

    assert response.status_code == 200
    assert len(response.content) <= len(original)
    if response.headers["x-compression-applied"] == "false":
        assert response.content == original


@needs_ghostscript
def test_corrupt_pdf_is_rejected(client):
    assert compress(client, b"%PDF-1.4 not really a pdf").status_code == 400


@needs_ghostscript
def test_no_temporary_files_are_left(client, scanned_pdf, documents_dir):
    compress(client, scanned_pdf)
    compress(client, b"broken")

    assert list(documents_dir.iterdir()) == []


def test_invalid_level_is_rejected(client):
    assert compress(client, make_pdf(1), level="extreme").status_code == 400


def test_non_pdf_is_rejected(client):
    response = client.post("/api/compress", files={"file": ("foto.jpg", b"x")})

    assert response.status_code == 400


def test_missing_ghostscript_is_reported(client, monkeypatch, documents_dir):
    monkeypatch.setattr(main, "find_ghostscript", lambda: None)

    response = compress(client, make_pdf(1))

    assert response.status_code == 503
    assert list(documents_dir.iterdir()) == []
