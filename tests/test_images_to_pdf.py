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


def render_page(content: bytes, index: int = 0) -> Image.Image:
    import pypdfium2

    with main.PDFIUM_LOCK:
        document = pypdfium2.PdfDocument(content)
        try:
            return document[index].render(scale=1).to_pil().convert("RGB")
        finally:
            document.close()


def encoded(image: Image.Image, image_format: str, **options) -> bytes:
    output = BytesIO()
    image.save(output, format=image_format, **options)
    return output.getvalue()


def transparent_images() -> list[tuple[str, bytes]]:
    rgba = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    rgba.paste((200, 0, 0, 255), (0, 0, 50, 100))  # left half opaque red
    la = Image.new("LA", (100, 100), (0, 0))
    la.paste((0, 255), (0, 0, 50, 100))  # left half opaque black
    palette = rgba.convert("RGB").convert("P", palette=Image.ADAPTIVE, colors=4)
    transparent_index = palette.getpixel((99, 0))
    return [
        ("rgba.png", encoded(rgba, "PNG")),
        ("la.png", encoded(la, "PNG")),
        ("palette.png", encoded(palette, "PNG", transparency=transparent_index)),
        ("palette.gif", encoded(palette, "GIF", transparency=transparent_index)),
        ("rgba.webp", encoded(rgba, "WEBP", lossless=True)),
    ]


@pytest.mark.parametrize(("name", "content"), transparent_images())
def test_transparent_areas_become_white_not_black(client, name, content):
    response = client.post("/api/images-to-pdf", files=[("images", (name, content))])

    assert response.status_code == 200
    page = render_page(response.content)
    width, height = page.size
    # The transparent right half is white; the opaque left half keeps its ink.
    assert min(page.getpixel((width * 3 // 4, height // 2))) > 240
    assert min(page.getpixel((width // 4, height // 2))) < 220


def test_phone_photo_is_turned_upright_from_exif(client):
    # Stored landscape (200x100) with EXIF orientation 6 ("rotate 90° CW"),
    # like a phone photo taken in portrait: the page must be portrait.
    image = Image.new("RGB", (200, 100), "white")
    image.paste((0, 0, 0), (0, 0, 100, 100))  # left half black
    exif = Image.Exif()
    exif[0x0112] = 6
    response = client.post(
        "/api/images-to-pdf", files=[("images", ("foto.jpg", encoded(image, "JPEG", exif=exif.tobytes())))]
    )

    assert response.status_code == 200
    (width, height), = page_sizes(response.content)
    assert height > width
    page = render_page(response.content)
    # After the 90° clockwise turn the black half is at the top.
    assert sum(page.getpixel((page.width // 2, page.height // 4))) < 100
    assert sum(page.getpixel((page.width // 2, page.height * 3 // 4))) > 600


def test_multi_page_tiff_gives_one_page_per_frame(client):
    frames = [Image.new("RGB", (40 + 10 * n, 30), color) for n, color in enumerate(["red", "green", "blue"])]
    tiff = BytesIO()
    frames[0].save(tiff, format="TIFF", save_all=True, append_images=frames[1:])

    response = client.post(
        "/api/images-to-pdf",
        files=[("images", ("varias.tiff", tiff.getvalue())), ("images", ("otra.png", make_image(20, 20)))],
    )

    assert response.status_code == 200
    assert page_sizes(response.content) == [(40, 30), (50, 30), (60, 30), (20, 20)]


def test_animated_gif_uses_only_the_first_frame(client):
    frames = [Image.new("RGB", (40, 30), color) for color in ["red", "green", "blue"]]
    gif = BytesIO()
    frames[0].save(gif, format="GIF", save_all=True, append_images=frames[1:], duration=100, loop=0)

    response = client.post("/api/images-to-pdf", files=[("images", ("animado.gif", gif.getvalue()))])

    assert response.status_code == 200
    assert len(PdfReader(BytesIO(response.content)).pages) == 1
    red, green, blue = render_page(response.content).getpixel((20, 15))
    assert red > 200 and green < 60 and blue < 60


def test_huge_later_tiff_frame_is_rejected(client):
    # Pillow checks the size of the first frame only: a tiny TIFF whose second
    # frame is 14000x14000 would decode to ~600 MB without this check.
    small = Image.new("1", (10, 10), 1)
    huge = Image.new("1", (14000, 14000), 1)
    tiff = BytesIO()
    small.save(tiff, format="TIFF", save_all=True, append_images=[huge], compression="group4")
    huge.close()

    response = client.post(
        "/api/images-to-pdf",
        files=[("images", ("bomba.tif", tiff.getvalue(), "image/tiff"))],
    )

    assert response.status_code == 400
    assert "demasiado grande" in response.json()["detail"]
