from io import BytesIO
import zipfile

from PIL import Image
import pytest

import app.main as main
from conftest import make_pdf


def convert(client, content: bytes, **data):
    return client.post("/api/pdf-to-images", files={"file": ("informe.pdf", content)}, data=data)


def zip_images(response) -> dict[str, Image.Image]:
    with zipfile.ZipFile(BytesIO(response.content)) as archive:
        return {name: Image.open(BytesIO(archive.read(name))) for name in archive.namelist()}


def test_one_png_per_page_in_a_zip(client, sample_pdf):
    response = convert(client, sample_pdf, base_name="hoja", dpi="72")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["content-disposition"] == 'attachment; filename="hoja-png.zip"'
    images = zip_images(response)
    assert list(images) == [f"hoja-page-00{n}.png" for n in range(1, 5)]
    assert all(image.format == "PNG" for image in images.values())
    # Page n is n * 100 points wide; at 72 dpi one point is one pixel.
    assert [image.size for image in images.values()] == [(n * 100, 300) for n in range(1, 5)]


def test_jpg_and_resolution(client, sample_pdf):
    response = convert(client, sample_pdf, image_format="jpg", dpi="150", base_name="foto")

    images = zip_images(response)
    assert list(images)[0] == "foto-page-001.jpg"
    first = images["foto-page-001.jpg"]
    assert first.format == "JPEG"
    assert first.size == (209, 625)
    assert round(first.info["dpi"][0]) == 150


def test_base_name_defaults_to_the_file_name(client, sample_pdf):
    response = convert(client, sample_pdf)

    assert response.headers["content-disposition"] == 'attachment; filename="informe-png.zip"'
    assert list(zip_images(response))[0] == "informe-page-001.png"


def test_single_page_pdf_is_returned_as_an_image(client):
    response = convert(client, make_pdf(1), image_format="jpg", base_name="solo")

    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["content-disposition"] == 'attachment; filename="solo-page-001.jpg"'
    assert Image.open(BytesIO(response.content)).format == "JPEG"


def test_huge_pages_are_rendered_at_a_lower_resolution(client, monkeypatch):
    monkeypatch.setattr(main, "MAX_IMAGE_PIXELS", 10_000)
    response = convert(client, make_pdf(1), dpi="300")

    width, height = Image.open(BytesIO(response.content)).size
    # pdfium rounds each side up, so allow a few pixels over the limit.
    assert width * height <= 10_000 * 1.05
    assert width * height > 10_000 * 0.9


@pytest.mark.parametrize("data", [{"image_format": "gif"}, {"dpi": "600"}, {"dpi": "abc"}])
def test_invalid_options_are_rejected(client, sample_pdf, data):
    assert convert(client, sample_pdf, **data).status_code in {400, 422}


def test_rejects_non_pdf_and_corrupt_files(client):
    assert client.post("/api/pdf-to-images", files={"file": ("a.txt", b"x")}).status_code == 400
    assert client.post("/api/pdf-to-images", files={"file": ("a.pdf", b"x")}).status_code == 400


def test_no_temporary_files_are_left(client, sample_pdf, documents_dir):
    convert(client, sample_pdf)
    convert(client, make_pdf(1))
    convert(client, b"not a pdf")

    assert list(documents_dir.iterdir()) == []
