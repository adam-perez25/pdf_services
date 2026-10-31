from io import BytesIO

from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
import pytest

import app.main as main
from conftest import make_pdf


def text_pdf(pages: list[list[str]]) -> bytes:
    """Build a PDF whose page n shows the lines of pages[n] in Helvetica.

    WinAnsiEncoding covers Spanish accents, so á and ñ are real text.
    """
    writer = PdfWriter()
    font = writer._add_object(
        DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
                NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
            }
        )
    )
    for lines in pages:
        page = writer.add_blank_page(595, 842)
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
        )
        operators = ["BT", "/F1 14 Tf", "16 TL", "72 760 Td"]
        for line in lines:
            escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            operators.append(f"({escaped}) Tj T*")
        operators.append("ET")
        stream = DecodedStreamObject()
        stream.set_data("\n".join(operators).encode("cp1252"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def extract(client, content: bytes, filename: str = "informe.pdf", **data):
    return client.post(
        "/api/extract-text", files={"file": (filename, content, "application/pdf")}, data=data
    )


THREE_PAGES = [["Primera pagina"], ["Segunda pagina", "con dos lineas"], ["Tercera pagina"]]


def test_extracts_all_pages_with_headers_by_default(client, documents_dir):
    response = extract(client, text_pdf(THREE_PAGES))

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert response.headers["content-disposition"] == 'attachment; filename="informe.txt"'
    assert response.headers["x-page-count"] == "3"
    assert response.content.decode("utf-8") == (
        "--- Página 1 ---\n\nPrimera pagina\n\n"
        "--- Página 2 ---\n\nSegunda pagina\ncon dos lineas\n\n"
        "--- Página 3 ---\n\nTercera pagina\n"
    )
    assert b"\r" not in response.content
    assert int(response.headers["x-character-count"]) == len(
        "Primera pagina" "Segunda pagina\ncon dos lineas" "Tercera pagina"
    )
    assert list(documents_dir.iterdir()) == []


def test_page_range_keeps_requested_order(client):
    response = extract(client, text_pdf(THREE_PAGES), pages="3, 1")

    assert response.status_code == 200
    assert response.headers["x-page-count"] == "2"
    assert response.text == "--- Página 3 ---\n\nTercera pagina\n\n--- Página 1 ---\n\nPrimera pagina\n"


@pytest.mark.parametrize(
    ("separator", "expected"),
    [
        ("formfeed", "Primera pagina\n\fSegunda pagina\ncon dos lineas\n"),
        ("none", "Primera pagina\n\nSegunda pagina\ncon dos lineas\n"),
    ],
)
def test_separator_modes(client, separator, expected):
    response = extract(client, text_pdf(THREE_PAGES), pages="1-2", page_separator=separator)

    assert response.status_code == 200
    assert response.text == expected


def test_accents_round_trip_in_utf8(client):
    response = extract(client, text_pdf([["Árbol, canción y año: ñandú"]]), page_separator="none")

    assert response.status_code == 200
    assert response.content.decode("utf-8") == "Árbol, canción y año: ñandú\n"


def test_output_name_is_sanitised_and_ends_in_txt(client):
    pdf = text_pdf(THREE_PAGES)

    named = extract(client, pdf, output_name="../mi texto.pdf")
    unsafe = extract(client, pdf, output_name='notas"<>.txt')

    # FileResponse sends names with spaces in the RFC 5987 form; downloadName
    # in common.js decodes it.
    assert named.headers["content-disposition"] == "attachment; filename*=utf-8''mi%20texto.txt"
    assert unsafe.headers["content-disposition"] == 'attachment; filename="notas___.txt"'


def test_pdf_without_text_suggests_ocr(client, documents_dir):
    response = extract(client, make_pdf(2))

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "escaneado" in detail
    assert "OCR" in detail
    assert list(documents_dir.iterdir()) == []


def test_encrypted_pdf_is_rejected(client, documents_dir):
    writer = PdfWriter(clone_from=PdfReader(BytesIO(text_pdf(THREE_PAGES))))
    writer.encrypt("secreto")
    output = BytesIO()
    writer.write(output)

    response = extract(client, output.getvalue())

    assert response.status_code == 400
    assert response.json()["detail"] == main.ENCRYPTED_PDF_MESSAGE
    assert list(documents_dir.iterdir()) == []


def test_rejects_non_pdf_and_broken_pdf(client, documents_dir):
    wrong_extension = extract(client, b"hola", filename="notas.txt")
    broken = extract(client, b"esto no es un PDF")

    assert wrong_extension.status_code == 400
    assert broken.status_code == 400
    assert broken.json()["detail"] == "No se pudo extraer el texto del PDF."
    assert list(documents_dir.iterdir()) == []


@pytest.mark.parametrize(
    "data",
    [{"pages": "4"}, {"pages": "a-b"}, {"page_separator": "tabs"}],
)
def test_rejects_invalid_options(client, documents_dir, data):
    response = extract(client, text_pdf(THREE_PAGES), **data)

    assert response.status_code == 400
    assert list(documents_dir.iterdir()) == []


def test_page_route_and_home_card(client):
    page = client.get("/extract-text")
    home = client.get("/")

    assert page.status_code == 200
    assert "SERVICIO 14 / EXTRAER TEXTO" in page.text
    assert "/static/extract-text.js" in page.text
    assert 'href="/extract-text"' in home.text
