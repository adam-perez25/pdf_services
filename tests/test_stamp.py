import re
from io import BytesIO

import pypdfium2
from pypdf import PdfReader, PdfWriter
import pytest

import app.main as main
from conftest import make_pdf


def stamp(client, content: bytes, filename: str = "informe.pdf", **data):
    return client.post("/api/stamp", files={"file": (filename, content)}, data=data)


def page_texts(content: bytes) -> list[str]:
    return [page.extract_text() for page in PdfReader(BytesIO(content)).pages]


def rotated_pdf(rotations: list[int]) -> bytes:
    # A4 portrait pages with the given /Rotate values.
    writer = PdfWriter()
    for rotation in rotations:
        page = writer.add_blank_page(595, 842)
        if rotation:
            page.rotate(rotation)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def ink_box(content: bytes, page_index: int) -> tuple[float, float, float, float]:
    """Return the bounding box of the dark pixels of a page as it is displayed,
    as fractions (left, top, right, bottom) of the rendered width and height."""
    document = pypdfium2.PdfDocument(content)
    try:
        image = document[page_index].render(scale=0.5).to_pil().convert("L")
    finally:
        document.close()
    left, top, right, bottom = image.point(lambda value: 255 if value < 200 else 0).getbbox()
    return left / image.width, top / image.height, right / image.width, bottom / image.height


def test_numbers_every_page_with_default_options(client, sample_pdf):
    response = stamp(client, sample_pdf, mode="numbers")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == 'attachment; filename="informe-numerado.pdf"'
    assert [text.strip() for text in page_texts(response.content)] == ["1", "2", "3", "4"]


@pytest.mark.parametrize(
    ("number_format", "expected"),
    [
        ("n-of-total", ["5 / 8", "6 / 8", "7 / 8", "8 / 8"]),
        ("page-n-of-total", ["Página 5 de 8", "Página 6 de 8", "Página 7 de 8", "Página 8 de 8"]),
    ],
)
def test_number_formats_and_start_number(client, sample_pdf, number_format, expected):
    response = stamp(client, sample_pdf, mode="numbers", number_format=number_format, start_number="5")

    assert response.status_code == 200
    assert [text.strip() for text in page_texts(response.content)] == expected


@pytest.mark.parametrize(
    ("position", "horizontal", "vertical"),
    [
        ("bottom-center", "center", "bottom"),
        ("bottom-left", "left", "bottom"),
        ("top-right", "right", "top"),
    ],
)
def test_number_position(client, position, horizontal, vertical):
    response = stamp(client, rotated_pdf([0]), mode="numbers", position=position, font_size="20")

    left, top, right, bottom = ink_box(response.content, 0)
    assert (bottom > 0.9) if vertical == "bottom" else (top < 0.1)
    middle = (left + right) / 2
    assert {"left": middle < 0.2, "center": 0.4 < middle < 0.6, "right": middle > 0.8}[horizontal]


@pytest.mark.parametrize("rotation", [90, 180, 270])
def test_numbers_are_upright_on_rotated_pages(client, rotation):
    response = stamp(client, rotated_pdf([rotation]), mode="numbers", font_size="20")

    assert response.status_code == 200
    reader = PdfReader(BytesIO(response.content))
    assert reader.pages[0].rotation == rotation
    assert reader.pages[0].extract_text().strip() == "1"
    # As displayed, the number is at the bottom center and is wider than tall,
    # so it was not drawn sideways.
    left, top, right, bottom = ink_box(response.content, 0)
    assert bottom > 0.9
    assert 0.4 < (left + right) / 2 < 0.6
    document = pypdfium2.PdfDocument(response.content)
    try:
        width, height = document[0].get_size()
    finally:
        document.close()
    assert (rotation in (90, 270)) == (width > height)


def test_watermark_on_every_page(client, sample_pdf):
    response = stamp(client, sample_pdf, mode="watermark", text="Borrador confidencial")

    assert response.status_code == 200
    assert response.headers["content-disposition"] == 'attachment; filename="informe-marca-de-agua.pdf"'
    texts = page_texts(response.content)
    assert len(texts) == 4
    assert all("Borrador confidencial" in text for text in texts)


def test_watermark_keeps_spanish_accents_and_is_centered(client):
    response = stamp(
        client, rotated_pdf([0, 90]), mode="watermark",
        text="Copia número único", rotation="0", opacity="0.8", output_name="copia",
    )

    assert response.status_code == 200
    assert response.headers["content-disposition"] == 'attachment; filename="copia.pdf"'
    assert all("Copia número único" in text for text in page_texts(response.content))
    for page_index in range(2):
        left, top, right, bottom = ink_box(response.content, page_index)
        assert abs((left + right) / 2 - 0.5) < 0.05
        assert abs((top + bottom) / 2 - 0.5) < 0.05
        assert right - left > bottom - top


def test_diagonal_watermark_is_diagonal(client):
    response = stamp(client, rotated_pdf([0]), mode="watermark", text="BORRADOR", rotation="45", opacity="1")

    left, top, right, bottom = ink_box(response.content, 0)
    # A 45 degree line across the center covers a tall box, not a flat strip.
    assert bottom - top > 0.25
    assert abs((left + right) / 2 - 0.5) < 0.05


def test_page_content_is_kept(client):
    original = make_pdf(2)

    response = stamp(client, original, mode="numbers")

    reader = PdfReader(BytesIO(response.content))
    assert [round(float(page.mediabox.width)) for page in reader.pages] == [100, 200]


@pytest.mark.parametrize(
    "data",
    [
        {"mode": "stamp"},
        {"mode": "numbers", "position": "middle"},
        {"mode": "numbers", "number_format": "roman"},
        {"mode": "numbers", "start_number": "abc"},
        {"mode": "numbers", "start_number": "-1"},
        {"mode": "numbers", "font_size": "200"},
        {"mode": "watermark", "text": "   "},
        {"mode": "watermark", "text": "x" * 101},
        {"mode": "watermark", "text": "Hola 😀"},
        {"mode": "watermark", "text": "Hola", "opacity": "0"},
        {"mode": "watermark", "text": "Hola", "opacity": "mucho"},
        {"mode": "watermark", "text": "Hola", "rotation": "30"},
        {"mode": "watermark", "text": "Hola", "font_size": "2"},
    ],
)
def test_invalid_options_are_rejected(client, sample_pdf, data):
    response = stamp(client, sample_pdf, **data)

    assert response.status_code == 400
    assert response.json()["detail"]


def test_non_pdf_is_rejected(client):
    assert stamp(client, b"x", filename="foto.jpg", mode="numbers").status_code == 400


def test_corrupt_pdf_is_rejected(client):
    assert stamp(client, b"%PDF-1.4 not really a pdf", mode="numbers").status_code == 400


def test_page_route(client):
    response = client.get("/stamp")

    assert response.status_code == 200
    assert "/static/stamp.js" in response.text


@pytest.mark.parametrize("position", sorted(main.STAMP_POSITIONS))
def test_long_page_numbers_stay_inside_narrow_pages(position):
    # "Página 100000 de 100099" at 72 pt is far wider than a 120 pt page.
    label = "Página 100000 de 100099"
    width, height = 120.0, 400.0
    stream = main.number_stream(label, 72, position, width, height)

    size, x, y = (float(v) for v in re.search(r"/StampFont (\S+) Tf 0 g (\S+) (\S+) Td", stream).groups())
    margin = min(main.STAMP_MARGIN, min(width, height) * 0.08)
    assert size < 72
    assert x >= margin - 0.01
    assert x + main.helvetica_width(label, size) <= width - margin + 0.01
    assert margin - 0.01 <= y <= height - margin - size * 0.718 + 0.01


def test_huge_number_font_fits_short_page():
    stream = main.number_stream("7", 72, "top-center", 300.0, 60.0)
    size, _, y = (float(v) for v in re.search(r"/StampFont (\S+) Tf 0 g (\S+) (\S+) Td", stream).groups())
    assert y >= 0 and y + size * 0.718 <= 60.0
