from io import BytesIO
import json

from PIL import Image
import pypdfium2
from pypdf import PdfReader, PdfWriter
import pytest

import app.main as main
from conftest import make_pdf, make_png


def blank_pdf(rotations: list[int], width: float = 600, height: float = 800, cropbox=None) -> bytes:
    writer = PdfWriter()
    for rotation in rotations:
        page = writer.add_blank_page(width, height)
        if cropbox:
            page.cropbox.lower_left = cropbox[:2]
            page.cropbox.upper_right = cropbox[2:]
        if rotation:
            page.rotate(rotation)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def solid_png(color=(0, 0, 160, 255), size=(200, 100)) -> bytes:
    output = BytesIO()
    Image.new("RGBA", size, color).save(output, format="PNG")
    return output.getvalue()


def ring_png() -> bytes:
    # A transparent image with an opaque frame: only the frame should cover the page.
    image = Image.new("RGBA", (200, 100), (0, 0, 0, 0))
    for x in range(200):
        for y in range(100):
            if x < 20 or x >= 180 or y < 20 or y >= 80:
                image.putpixel((x, y), (0, 0, 0, 255))
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def upload(client, content: bytes) -> str:
    response = client.post("/api/documents", files={"file": ("contrato.pdf", content)})
    assert response.status_code == 200
    return response.json()["id"]


def sign(client, document_id: str, placements, signature: bytes | None = None,
         filename: str = "firma.png", **data):
    if not isinstance(placements, str):
        placements = json.dumps(placements)
    return client.post(
        f"/api/documents/{document_id}/sign",
        files={"signature": (filename, solid_png() if signature is None else signature)},
        data={"placements": placements, **data},
    )


def render(content: bytes, page_index: int = 0) -> Image.Image:
    """Render a page as displayed (pdfium applies /Rotate and the cropbox)."""
    with main.PDFIUM_LOCK:
        document = pypdfium2.PdfDocument(content)
        try:
            return document[page_index].render(scale=0.5).to_pil().convert("L")
        finally:
            document.close()


def ink_box(image: Image.Image):
    box = image.point(lambda value: 255 if value < 200 else 0).getbbox()
    if box is None:
        return None
    left, top, right, bottom = box
    return left / image.width, top / image.height, right / image.width, bottom / image.height


def assert_box_close(actual, expected, tolerance=0.02):
    assert actual is not None
    assert all(abs(a - e) <= tolerance for a, e in zip(actual, expected)), (actual, expected)


BOX = {"x": 0.5, "y": 0.1, "width": 0.3, "height": 0.1}


def test_page_route(client):
    response = client.get("/sign")
    assert response.status_code == 200
    assert "Firmar PDF" in response.text
    assert "/static/sign.js" in response.text


def test_signs_the_chosen_page_inside_the_box(client, documents_dir):
    document_id = upload(client, blank_pdf([0, 0, 0]))

    response = sign(client, document_id, [{"page": 2, **BOX}], output_name="contrato-firmado.pdf")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == 'attachment; filename="contrato-firmado.pdf"'
    assert len(PdfReader(BytesIO(response.content)).pages) == 3
    assert_box_close(ink_box(render(response.content, 1)), (0.5, 0.1, 0.8, 0.2))
    # Other pages are untouched.
    assert ink_box(render(response.content, 0)) is None
    assert ink_box(render(response.content, 2)) is None
    # Like the organizer's save, the stored copy is removed after the download.
    assert list(documents_dir.iterdir()) == []


def test_several_placements_share_one_image(client):
    document_id = upload(client, blank_pdf([0, 0]))
    placements = [
        {"page": 1, "x": 0.05, "y": 0.05, "width": 0.2, "height": 0.1},
        {"page": 1, "x": 0.7, "y": 0.85, "width": 0.2, "height": 0.1},
        {"page": 2, **BOX},
    ]

    response = sign(client, document_id, placements)

    assert response.status_code == 200
    assert response.content.count(b"/Subtype /Image") == 1
    assert_box_close(ink_box(render(response.content, 0)), (0.05, 0.05, 0.9, 0.95))
    assert_box_close(ink_box(render(response.content, 1)), (0.5, 0.1, 0.8, 0.2))


def test_default_output_name(client):
    document_id = upload(client, blank_pdf([0]))
    response = sign(client, document_id, [{"page": 1, **BOX}])
    assert response.headers["content-disposition"] == 'attachment; filename="firmado.pdf"'


@pytest.mark.parametrize("rotation", [90, 180, 270])
def test_rotated_page_lands_where_it_was_placed(client, rotation):
    document_id = upload(client, blank_pdf([rotation]))

    response = sign(client, document_id, [{"page": 1, **BOX}])

    assert response.status_code == 200
    image = render(response.content)
    # A 600x800 page with /Rotate 90/270 is shown landscape.
    assert (image.width > image.height) == (rotation in (90, 270))
    assert_box_close(ink_box(image), (0.5, 0.1, 0.8, 0.2))


def test_cropbox_origin_is_respected(client):
    document_id = upload(client, blank_pdf([0], cropbox=(100, 200, 500, 700)))

    response = sign(client, document_id, [{"page": 1, "x": 0.0, "y": 0.0, "width": 0.5, "height": 0.2}])

    assert response.status_code == 200
    assert_box_close(ink_box(render(response.content)), (0.0, 0.0, 0.5, 0.2))


def test_transparent_png_keeps_the_page_visible(client):
    document_id = upload(client, blank_pdf([0]))

    response = sign(client, document_id, [{"page": 1, "x": 0.2, "y": 0.2, "width": 0.5, "height": 0.25}],
                    signature=ring_png())

    assert response.status_code == 200
    assert b"/SMask" in response.content
    image = render(response.content)
    # The frame is drawn but the transparent middle of the box stays white.
    assert_box_close(ink_box(image), (0.2, 0.2, 0.7, 0.45))
    middle = image.getpixel((int(0.45 * image.width), int(0.325 * image.height)))
    assert middle > 240


def test_jpeg_signature(client):
    document_id = upload(client, blank_pdf([0]))
    output = BytesIO()
    Image.new("RGB", (120, 60), (20, 20, 20)).save(output, format="JPEG")

    response = sign(client, document_id, [{"page": 1, **BOX}], signature=output.getvalue(), filename="firma.jpg")

    assert response.status_code == 200
    assert b"/SMask" not in response.content
    assert_box_close(ink_box(render(response.content)), (0.5, 0.1, 0.8, 0.2))


def test_large_signature_is_scaled_down(client):
    document_id = upload(client, blank_pdf([0]))
    response = sign(client, document_id, [{"page": 1, **BOX}], signature=solid_png(size=(3000, 1000)))

    assert response.status_code == 200
    reader = PdfReader(BytesIO(response.content))
    xobject = reader.pages[0]["/Resources"]["/XObject"]
    image = next(iter(xobject.values())).get_object()
    assert max(image["/Width"], image["/Height"]) == main.SIGNATURE_MAX_SIDE


@pytest.mark.parametrize(
    "placements",
    [
        "no es json",
        "{}",
        "[]",
        [{"page": 1}],
        [{"page": "1", **BOX}],
        [{"page": True, **BOX}],
        [{"page": 1, **BOX, "x": "0.1"}],
        [{"page": 1, **BOX, "x": None}],
        ["page"],
    ],
)
def test_bad_placements_json(client, documents_dir, placements):
    document_id = upload(client, blank_pdf([0]))

    response = sign(client, document_id, placements)

    assert response.status_code == 400
    # A failed request keeps the upload so the user can fix it and retry.
    assert len(list(documents_dir.iterdir())) == 1


@pytest.mark.parametrize(
    "box",
    [
        {"x": -0.1, "y": 0.1, "width": 0.3, "height": 0.1},
        {"x": 0.8, "y": 0.1, "width": 0.3, "height": 0.1},
        {"x": 0.1, "y": 0.95, "width": 0.3, "height": 0.1},
        {"x": 0.1, "y": 0.1, "width": 0, "height": 0.1},
        {"x": 0.1, "y": 0.1, "width": 0.3, "height": -0.1},
    ],
)
def test_box_outside_the_page(client, box):
    document_id = upload(client, blank_pdf([0]))
    response = sign(client, document_id, [{"page": 1, **box}])
    assert response.status_code == 400
    assert response.json()["detail"] == "La firma debe quedar dentro de la página."


def test_tiny_overshoot_is_tolerated(client):
    document_id = upload(client, blank_pdf([0]))
    response = sign(client, document_id, [{"page": 1, "x": 0.7, "y": 0.9, "width": 0.3005, "height": 0.1004}])
    assert response.status_code == 200


@pytest.mark.parametrize("page", [0, 3, -1])
def test_page_out_of_range(client, page):
    document_id = upload(client, blank_pdf([0, 0]))
    response = sign(client, document_id, [{"page": page, **BOX}])
    assert response.status_code == 400
    assert response.json()["detail"] == "Las páginas deben estar entre 1 y 2."


def test_too_many_placements(client):
    document_id = upload(client, blank_pdf([0]))
    placements = [{"page": 1, **BOX}] * (main.SIGNATURE_MAX_PLACEMENTS + 1)
    response = sign(client, document_id, placements)
    assert response.status_code == 400


def test_non_image_signature(client):
    document_id = upload(client, blank_pdf([0]))
    response = sign(client, document_id, [{"page": 1, **BOX}], signature=b"no soy una imagen", filename="firma.png")
    assert response.status_code == 400
    assert response.json()["detail"] == "La firma debe ser una imagen PNG o JPEG."


@pytest.mark.parametrize("image_format", ["GIF", "BMP", "WEBP"])
def test_other_image_formats_rejected(client, image_format):
    document_id = upload(client, blank_pdf([0]))
    output = BytesIO()
    Image.new("RGB", (40, 20), "black").save(output, format=image_format)
    response = sign(client, document_id, [{"page": 1, **BOX}], signature=output.getvalue())
    assert response.status_code == 400


def test_eps_signature_rejected(client, monkeypatch):
    document_id = upload(client, blank_pdf([0]))
    # Pillow would hand EPS to Ghostscript; it must never get that far.
    monkeypatch.setattr(main.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("ran a process"))
    eps = b"%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 10 10\nnewpath 0 0 moveto 10 10 lineto stroke\n%%EOF\n"
    response = sign(client, document_id, [{"page": 1, **BOX}], signature=eps, filename="firma.eps")
    assert response.status_code == 400


def test_oversized_signature_rejected(client, monkeypatch):
    document_id = upload(client, blank_pdf([0]))
    monkeypatch.setattr(main, "SIGNATURE_MAX_MB", 0.0001)
    response = sign(client, document_id, [{"page": 1, **BOX}], signature=make_png())
    assert response.status_code == 413


def test_unknown_document_is_gone(client):
    response = sign(client, "0" * 32, [{"page": 1, **BOX}])
    assert response.status_code == 410


def test_invalid_document_id(client):
    response = sign(client, "../../etc", [{"page": 1, **BOX}])
    assert response.status_code == 404


def test_processing_error_does_not_leak(client, documents_dir):
    document_id = upload(client, make_pdf(1))
    # Corrupt the stored copy after the upload checked it.
    (documents_dir / f"{document_id}.pdf").write_bytes(b"%PDF-1.4 roto")
    response = sign(client, document_id, [{"page": 1, **BOX}])
    assert response.status_code == 400
    assert response.json()["detail"] == "No se pudo firmar el PDF."
