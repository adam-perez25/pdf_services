from io import BytesIO

from PIL import Image
from pypdf import PdfReader
import pytest

import app.main as main


def make_image(width: int, height: int, noise: bool = False) -> bytes:
    # Noise looks like a photo to JPEG, so it does not compress to almost nothing.
    if noise:
        image = Image.effect_noise((width, height), 64).convert("RGB")
    else:
        image = Image.new("RGB", (width, height), "white")
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def page_sizes(content: bytes) -> list[tuple[int, int]]:
    """Return (width, height) in points for each page of a PDF."""
    reader = PdfReader(BytesIO(content))
    return [
        (round(float(page.mediabox.width)), round(float(page.mediabox.height)))
        for page in reader.pages
    ]


def convert(client, *images: bytes):
    response = client.post(
        "/api/images-to-pdf",
        files=[("images", (f"{index}.png", image)) for index, image in enumerate(images)],
    )
    assert response.status_code == 200
    return response


def test_a4_scan_fills_an_a4_page(client):
    response = convert(client, make_image(2480, 3508))

    [(width, height)] = page_sizes(response.content)
    assert width == pytest.approx(595, abs=2)
    assert height == pytest.approx(842, abs=2)


def test_landscape_image_gets_a_landscape_a4_page(client):
    # A 4:3 phone photo is wider than A4's ratio allows, so its height limits it.
    response = convert(client, make_image(4032, 3024))

    [(width, height)] = page_sizes(response.content)
    assert width > height
    assert width <= 843
    assert height == pytest.approx(595, abs=2)


def test_small_image_is_not_scaled_up(client):
    response = convert(client, make_image(60, 80))

    assert page_sizes(response.content) == [(60, 80)]


def test_each_image_keeps_its_own_page_size(client):
    response = convert(client, make_image(2480, 3508), make_image(60, 80), make_image(3508, 2480))

    sizes = page_sizes(response.content)
    assert sizes[1] == (60, 80)
    assert sizes[0][0] == pytest.approx(595, abs=2)
    assert sizes[2][0] == pytest.approx(842, abs=2)


@pytest.mark.skipif(
    getattr(main, "find_ghostscript", lambda: None)() is None,
    reason="Ghostscript is not installed",
)
def test_phone_photo_pdf_can_be_compressed(client):
    pdf = convert(client, make_image(2480, 3508, noise=True)).content

    response = client.post(
        "/api/compress",
        files={"file": ("photo.pdf", pdf)},
        data={"level": "recommended"},
    )

    assert response.status_code == 200
    assert len(response.content) < len(pdf) * 0.8


@pytest.mark.parametrize("image_format", ["WEBP", "GIF", "BMP", "TIFF"])
def test_other_raster_formats_are_accepted(client, image_format):
    image = BytesIO()
    Image.new("RGB", (40, 30), "teal").save(image, format=image_format)
    response = client.post("/api/images-to-pdf", files=[("images", ("imagen", image.getvalue()))])

    assert response.status_code == 200
    assert len(PdfReader(BytesIO(response.content)).pages) == 1


def test_eps_is_rejected_without_running_ghostscript(client, monkeypatch):
    # Pillow would render an EPS by running Ghostscript on the upload.
    from PIL import EpsImagePlugin

    def fail(*args, **kwargs):
        raise AssertionError("Ghostscript must not run")

    monkeypatch.setattr(EpsImagePlugin, "Ghostscript", fail)
    eps = b"%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 50 50\nnewpath 0 0 moveto 50 0 lineto 50 50 lineto closepath fill\nshowpage\n"
    response = client.post("/api/images-to-pdf", files=[("images", ("dibujo.png", eps, "image/png"))])

    assert response.status_code == 400
    assert "no es una imagen válida" in response.json()["detail"]
