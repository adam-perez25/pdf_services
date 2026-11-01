import json
import zipfile
from io import BytesIO

import pytest

import app.main as main
import app.external_tools as external_tools
from conftest import make_pdf, make_png, page_layout


@pytest.mark.parametrize("path", ["/", "/merge", "/split", "/images", "/word", "/excel", "/powerpoint", "/pages", "/compress", "/pdf-to-images"])
def test_pages_load(client, path):
    response = client.get(path)

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_static_files_are_revalidated(client):
    response = client.get("/static/styles.css")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"


def test_merge_keeps_order(client):
    first, second = make_pdf(2), make_pdf(1)

    response = client.post(
        "/api/merge",
        files=[("files", ("a.pdf", first)), ("files", ("b.pdf", second))],
        data={"output_name": "result"},
    )

    assert response.status_code == 200
    assert response.headers["content-disposition"] == 'attachment; filename="result.pdf"'
    assert [width for width, _ in page_layout(response.content)] == [100, 200, 100]


def test_merge_rejects_non_pdf(client):
    response = client.post("/api/merge", files=[("files", ("notes.txt", b"hello"))])

    assert response.status_code == 400


def test_merge_rejects_corrupt_pdf(client):
    response = client.post("/api/merge", files=[("files", ("broken.pdf", b"not a pdf"))])

    assert response.status_code == 400


def test_output_name_is_sanitized(client, sample_pdf):
    response = client.post(
        "/api/merge",
        files=[("files", ("a.pdf", sample_pdf))],
        data={"output_name": "../../etc/pass\"wd"},
    )

    assert response.headers["content-disposition"] == 'attachment; filename="pass_wd.pdf"'


def test_pdf_info_counts_pages(client, sample_pdf):
    response = client.post("/api/pdf-info", files={"file": ("a.pdf", sample_pdf)})

    assert response.json() == {"pages": 4}


def test_split_extracts_range(client, sample_pdf):
    response = client.post(
        "/api/split",
        files={"file": ("a.pdf", sample_pdf)},
        data={"start_page": 2, "end_page": 3},
    )

    assert response.status_code == 200
    assert [width for width, _ in page_layout(response.content)] == [200, 300]


@pytest.mark.parametrize("start, end", [(0, 2), (3, 2), (1, 9)])
def test_split_rejects_invalid_range(client, sample_pdf, start, end):
    response = client.post(
        "/api/split",
        files={"file": ("a.pdf", sample_pdf)},
        data={"start_page": start, "end_page": end},
    )

    assert response.status_code == 400


@pytest.mark.parametrize("data", [{"end_page": 0}, {"start_page": 0}, {"start_page": -1, "end_page": 2},
                                  {"start_page": 2, "end_page": -1}])
def test_split_zero_or_negative_pages_are_rejected(client, sample_pdf, data):
    # end_page=0 used to mean "to the end"; only a missing end_page does now.
    response = client.post("/api/split", files={"file": ("a.pdf", sample_pdf)}, data=data)

    assert response.status_code == 400
    assert response.json()["detail"] == "El rango debe estar entre 1 y 4."


@pytest.mark.parametrize("data", [{"start_page": 3}, {"start_page": 3, "end_page": ""}])
def test_split_without_end_page_goes_to_the_last_page(client, sample_pdf, data):
    response = client.post("/api/split", files={"file": ("a.pdf", sample_pdf)}, data=data)

    assert response.status_code == 200
    assert [width for width, _ in page_layout(response.content)] == [300, 400]


def test_merge_error_has_spanish_accents(client):
    response = client.post("/api/merge", files=[("files", ("notes.txt", b"hello"))])

    assert response.json()["detail"] == "Todos los archivos deben tener extensión PDF."


def test_merge_keeps_accents_in_the_download_name(client, sample_pdf):
    response = client.post(
        "/api/merge",
        files=[("files", ("a.pdf", sample_pdf))],
        data={"output_name": "Informe año ñ.pdf"},
    )

    assert response.status_code == 200
    assert response.headers["content-disposition"] == (
        "attachment; filename=\"Informe ano n.pdf\"; filename*=UTF-8''Informe%20a%C3%B1o%20%C3%B1.pdf"
    )


@pytest.mark.parametrize(
    "pages",
    [
        [{"page": 1.7}],
        [{"page": True}],
        [{"page": "1"}],
        [{"page": 1, "rotation": 90.5}],
        [{"page": 1, "rotation": False}],
        [1],
    ],
)
def test_edit_pages_requires_whole_numbers(client, sample_pdf, pages):
    response = client.post(
        "/api/edit-pages", files={"file": ("a.pdf", sample_pdf)}, data={"pages": json.dumps(pages)}
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "La lista de páginas no es válida."


def test_edit_pages_rejects_deeply_nested_json(client, sample_pdf):
    response = client.post(
        "/api/edit-pages", files={"file": ("a.pdf", sample_pdf)}, data={"pages": "[" * 100_000 + "]" * 100_000}
    )

    assert response.status_code == 400


def test_edit_pages_writes_rotation_modulo_360(client):
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(100, 200).rotation = 270
    writer.add_blank_page(100, 200)
    source = BytesIO()
    writer.write(source)

    response = client.post(
        "/api/edit-pages",
        files={"file": ("a.pdf", source.getvalue())},
        data={"pages": json.dumps([{"page": 1, "rotation": 90}, {"page": 2, "rotation": 450}])},
    )

    assert response.status_code == 200
    pages = PdfReader(BytesIO(response.content)).pages
    assert pages[0].get("/Rotate", 0) == 0  # 270 + 90 = 360 is written as 0
    assert pages[1]["/Rotate"] == 90


def test_images_to_pdf_one_page_per_image(client):
    response = client.post(
        "/api/images-to-pdf",
        files=[("images", ("a.png", make_png("red"))), ("images", ("b.png", make_png("blue")))],
    )

    assert response.status_code == 200
    assert len(page_layout(response.content)) == 2


def test_images_to_pdf_rejects_non_image(client):
    response = client.post("/api/images-to-pdf", files=[("images", ("a.png", b"not an image"))])

    assert response.status_code == 400


def test_word_rejects_other_extensions(client):
    response = client.post("/api/word-to-pdf", files={"file": ("notes.txt", b"hello")})

    assert response.status_code == 400


def test_word_reports_missing_libreoffice(client, monkeypatch):
    monkeypatch.setattr(external_tools, "find_soffice", lambda: None)

    response = client.post("/api/word-to-pdf", files={"file": ("a.docx", make_docx("Hola"))})

    assert response.status_code == 503


@pytest.mark.skipif(external_tools.find_soffice() is None, reason="LibreOffice is not installed")
def test_word_converts_docx(client):
    response = client.post("/api/word-to-pdf", files={"file": ("a.docx", make_docx("Hola"))})

    assert response.status_code == 200
    assert response.content.startswith(b"%PDF")


def test_stateless_edit_pages(client, sample_pdf):
    response = client.post(
        "/api/edit-pages",
        files={"file": ("a.pdf", sample_pdf)},
        data={"pages": json.dumps([{"page": 3, "rotation": 90}, {"page": 1}])},
    )

    assert response.status_code == 200
    assert page_layout(response.content) == [(300, 90), (100, 0)]


def make_docx(text: str) -> bytes:
    # The smallest .docx LibreOffice accepts: one paragraph of text.
    output = BytesIO()
    with zipfile.ZipFile(output, "w") as docx:
        docx.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>",
        )
        docx.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            "</Relationships>",
        )
        docx.writestr(
            "word/document.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f"<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body>"
            "</w:document>",
        )
    return output.getvalue()
