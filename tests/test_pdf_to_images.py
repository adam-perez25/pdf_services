import errno
from io import BytesIO
import zipfile

from PIL import Image
import pytest

import app.main as main
import app.config as config
import app.errors as errors
import app.pdf_helpers as pdf_helpers
import app.routers.pdf_to_images as pdf_to_images_router
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


def test_unicode_file_name_survives_in_zip_and_entries(client, sample_pdf):
    response = client.post(
        "/api/pdf-to-images", files={"file": ("Informe año ñ.pdf", sample_pdf)}, data={"dpi": "72"}
    )

    assert response.status_code == 200
    assert response.headers["content-disposition"] == (
        "attachment; filename=\"Informe ano n-png.zip\"; filename*=UTF-8''Informe%20a%C3%B1o%20%C3%B1-png.zip"
    )
    assert list(zip_images(response))[0] == "Informe año ñ-page-001.png"


def test_unicode_name_of_a_single_image(client):
    response = convert(client, make_pdf(1), base_name="Página única")

    assert response.headers["content-disposition"] == (
        "attachment; filename=\"Pagina unica-page-001.png\"; filename*=UTF-8''P%C3%A1gina%20%C3%BAnica-page-001.png"
    )


def test_single_page_pdf_is_returned_as_an_image(client):
    response = convert(client, make_pdf(1), image_format="jpg", base_name="solo")

    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["content-disposition"] == 'attachment; filename="solo-page-001.jpg"'
    assert Image.open(BytesIO(response.content)).format == "JPEG"


def test_huge_pages_are_rendered_at_a_lower_resolution(client, monkeypatch):
    monkeypatch.setattr(pdf_to_images_router, "MAX_IMAGE_PIXELS", 10_000)
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


# --- Page selection ---------------------------------------------------------


def test_page_selection_keeps_the_real_page_numbers(client, sample_pdf):
    response = convert(client, sample_pdf, base_name="hoja", dpi="72", pages="4, 2-3, 2")

    assert response.status_code == 200
    images = zip_images(response)
    # Repeated pages are converted once, in the order given.
    assert list(images) == ["hoja-page-004.png", "hoja-page-002.png", "hoja-page-003.png"]
    assert images["hoja-page-004.png"].size == (400, 300)


def test_single_selected_page_is_returned_as_an_image(client, sample_pdf):
    response = convert(client, sample_pdf, base_name="hoja", dpi="72", pages="3")

    assert response.headers["content-type"] == "image/png"
    assert response.headers["content-disposition"] == 'attachment; filename="hoja-page-003.png"'
    assert Image.open(BytesIO(response.content)).size == (300, 300)


@pytest.mark.parametrize("pages", ["0", "5", "3-1", "a", ","])
def test_invalid_page_ranges_are_rejected(client, sample_pdf, documents_dir, pages):
    response = convert(client, sample_pdf, pages=pages)

    assert response.status_code == 400
    assert list(documents_dir.iterdir()) == []


# --- Size, disk and time guards --------------------------------------------


def test_too_many_pixels_are_rejected_before_rendering(client, sample_pdf, documents_dir, monkeypatch):
    rendered = []
    monkeypatch.setattr(pdf_to_images_router, "render_scale", lambda *args: rendered.append(args) or 300 / 72)
    monkeypatch.setattr(config, "PDF_TO_IMAGES_MAX_MEGAPIXELS", 1)

    # 4 pages of 1000 x 300 points in total at 300 ppp: about 5.2 megapixels.
    response = convert(client, sample_pdf, dpi="300")

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "4 páginas a 300 ppp" in detail
    assert "5,2 millones de píxeles" in detail
    assert "máximo de 1,0 millones" in detail
    assert "menos páginas" in detail and "resolución menor" in detail
    assert len(rendered) == 4  # only the estimate ran, no page was rendered
    assert list(documents_dir.iterdir()) == []


def test_pixel_cap_counts_only_the_selected_pages_and_dpi(client, sample_pdf, monkeypatch):
    monkeypatch.setattr(config, "PDF_TO_IMAGES_MAX_MEGAPIXELS", 1)

    assert convert(client, sample_pdf, dpi="72").status_code == 200  # 0.3 Mpx
    assert convert(client, sample_pdf, dpi="300", pages="1").status_code == 200  # 0.5 Mpx


def test_estimate_respects_the_per_image_pixel_limit(client, monkeypatch):
    # Pages are rendered at no more than MAX_IMAGE_PIXELS, so a huge page
    # counts as that size in the estimate.
    monkeypatch.setattr(pdf_to_images_router, "MAX_IMAGE_PIXELS", 10_000)
    monkeypatch.setattr(config, "PDF_TO_IMAGES_MAX_MEGAPIXELS", 1)

    assert convert(client, make_pdf(4), dpi="300").status_code == 200


class FakeUsage:
    def __init__(self, free):
        self.total, self.used, self.free = free * 2, free, free


def test_rejected_when_the_output_does_not_fit_on_disk(client, sample_pdf, documents_dir, monkeypatch):
    monkeypatch.setattr(pdf_to_images_router.shutil, "disk_usage", lambda path: FakeUsage(600 * 1024 * 1024))
    monkeypatch.setattr(pdf_to_images_router, "IMAGE_BYTES_PER_PIXEL", {"png": 20.0, "jpg": 2.0})

    # About 5.2 Mpx at 20 bytes each is ~99 MB, plus the 512 MB margin > 600 MB.
    response = convert(client, sample_pdf, dpi="300")

    assert response.status_code == 507
    detail = response.json()["detail"]
    assert "4 páginas a 300 ppp ocuparía unos 99 MB" in detail
    assert "600 MB libres" in detail
    assert "formato JPG" in detail
    assert list(documents_dir.iterdir()) == []

    # JPG is estimated smaller and fits.
    assert convert(client, sample_pdf, dpi="300", image_format="jpg").status_code == 200


def test_small_documents_pass_the_disk_check(client, sample_pdf, monkeypatch):
    monkeypatch.setattr(pdf_to_images_router.shutil, "disk_usage", lambda path: FakeUsage(600 * 1024 * 1024))

    assert convert(client, sample_pdf, dpi="300").status_code == 200


def test_time_limit_stops_and_cleans_up(client, sample_pdf, documents_dir, monkeypatch):
    monkeypatch.setattr(pdf_to_images_router, "pdf_to_images_timeout", lambda pages: -1)

    response = convert(client, sample_pdf, dpi="72")

    assert response.status_code == 504
    detail = response.json()["detail"]
    assert "tras 1 de 4 páginas" in detail
    assert "menos páginas" in detail
    assert list(documents_dir.iterdir()) == []


def test_time_limit_does_not_cut_a_single_page(client, sample_pdf, monkeypatch):
    monkeypatch.setattr(pdf_to_images_router, "pdf_to_images_timeout", lambda pages: -1)

    assert convert(client, sample_pdf, dpi="72", pages="2").status_code == 200


def test_timeout_scales_with_pages_and_is_capped(monkeypatch):
    monkeypatch.setattr(config, "PDF_TO_IMAGES_TIMEOUT_SECONDS", 300)
    monkeypatch.setattr(config, "PDF_TO_IMAGES_SECONDS_PER_PAGE", 3)
    monkeypatch.setattr(config, "PDF_TO_IMAGES_MAX_TIMEOUT_SECONDS", 7200)

    assert pdf_to_images_router.pdf_to_images_timeout(20) == 360
    assert pdf_to_images_router.pdf_to_images_timeout(1000) == 3300
    assert pdf_to_images_router.pdf_to_images_timeout(5000) == 7200
    monkeypatch.setattr(config, "PDF_TO_IMAGES_MAX_TIMEOUT_SECONDS", 10)
    assert pdf_to_images_router.pdf_to_images_timeout(5000) == 300  # never below the base


def test_disk_full_while_writing_cleans_up(client, sample_pdf, documents_dir, monkeypatch):
    original = zipfile.ZipFile.writestr
    calls = []

    def writestr(self, name, data, *args, **kwargs):
        calls.append(name)
        if len(calls) == 2:
            raise OSError(errno.ENOSPC, "No space left on device")
        return original(self, name, data, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "writestr", writestr)

    response = convert(client, sample_pdf, dpi="72")

    assert response.status_code == 507
    detail = response.json()["detail"]
    assert "disco temporal del servidor se ha llenado" in detail
    assert "No space" not in detail
    assert list(documents_dir.iterdir()) == []


def test_other_write_errors_stay_generic(client, sample_pdf, documents_dir, monkeypatch):
    def writestr(self, name, data, *args, **kwargs):
        raise OSError(errno.EIO, "I/O error")

    monkeypatch.setattr(zipfile.ZipFile, "writestr", writestr)

    response = convert(client, sample_pdf, dpi="72")

    assert response.status_code == 400
    assert response.json()["detail"] == "No se pudo convertir el PDF en imágenes."
    assert list(documents_dir.iterdir()) == []


def test_overlong_page_list_is_rejected_quickly(client, documents_dir):
    # "1-3,1-3,..." far past the limit must not be expanded page by page.
    response = convert(client, make_pdf(3), pages=",".join(["1-3"] * 1000))

    assert response.status_code == 400
    assert str(pdf_helpers.PAGE_SELECTION_MAX_LENGTH) in response.json()["detail"]
    assert list(documents_dir.iterdir()) == []


def test_non_finite_pixel_total_is_rejected():
    with pytest.raises(errors.HTTPException) as error:
        pdf_to_images_router.check_pdf_to_images_budget(float("nan"), 1, 150, "png")
    assert error.value.status_code == 400


def test_reversed_range_says_why(client):
    response = convert(client, make_pdf(5), pages="3-1")

    assert response.status_code == 400
    assert "el inicio no puede ser mayor que el final" in response.json()["detail"]
