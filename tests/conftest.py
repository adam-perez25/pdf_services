from io import BytesIO

from fastapi.testclient import TestClient
from PIL import Image
from pypdf import PdfReader
import pytest

import app.main as main


@pytest.fixture
def documents_dir(tmp_path, monkeypatch):
    # Keep uploaded documents out of the real temporary folder.
    path = tmp_path / "documents"
    path.mkdir()
    monkeypatch.setattr(main, "DOCUMENTS_DIR", path)
    return path


@pytest.fixture
def client(documents_dir):
    return TestClient(main.app)


def make_pdf(page_count: int) -> bytes:
    # Page n is n * 100 points wide, so tests can tell pages apart by width.
    images = [Image.new("RGB", (n * 100, 300), "white") for n in range(1, page_count + 1)]
    output = BytesIO()
    images[0].save(output, format="PDF", save_all=True, append_images=images[1:], resolution=72)
    return output.getvalue()


def make_png(color: str = "teal") -> bytes:
    output = BytesIO()
    Image.new("RGB", (60, 80), color).save(output, format="PNG")
    return output.getvalue()


def page_layout(content: bytes) -> list[tuple[int, int]]:
    """Return (width in points, rotation) for each page of a PDF."""
    reader = PdfReader(BytesIO(content))
    return [(round(float(page.mediabox.width)), page.rotation) for page in reader.pages]


@pytest.fixture
def sample_pdf() -> bytes:
    return make_pdf(4)
