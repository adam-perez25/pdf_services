from io import BytesIO

from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject

import app.main as main
from conftest import make_pdf

XMP = b"""<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
<rdf:Description xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:creator>Ana</dc:creator></rdf:Description>
</rdf:RDF></x:xmpmeta><?xpacket end="w"?>"""


def make_pdf_with_metadata(page_count: int = 2) -> bytes:
    writer = PdfWriter(clone_from=PdfReader(BytesIO(make_pdf(page_count))))
    writer.add_metadata(
        {
            "/Title": "Informe anual",
            "/Author": "Ana García",
            "/Subject": "Cuentas",
            "/Keywords": "finanzas, 2026",
            "/Creator": "Writer",
            "/Producer": "LibreOffice",
            "/CreationDate": "D:20260115103000+01'00'",
        }
    )
    xmp = DecodedStreamObject()
    xmp.set_data(XMP)
    xmp.update({NameObject("/Type"): NameObject("/Metadata"), NameObject("/Subtype"): NameObject("/XML")})
    writer.root_object[NameObject("/Metadata")] = writer._add_object(xmp)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def read(client, content: bytes, filename: str = "informe.pdf"):
    return client.post("/api/metadata/read", files={"file": (filename, content)})


def edit(client, content: bytes, filename: str = "informe.pdf", **data):
    return client.post("/api/metadata", files={"file": (filename, content)}, data=data)


def test_page_route(client):
    response = client.get("/metadata")

    assert response.status_code == 200
    assert "/static/metadata.js" in response.text


def test_read_returns_values(client):
    response = read(client, make_pdf_with_metadata(3))

    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "Informe anual"
    assert data["author"] == "Ana García"
    assert data["subject"] == "Cuentas"
    assert data["keywords"] == "finanzas, 2026"
    assert data["creator"] == "Writer"
    assert data["producer"] == "LibreOffice"
    assert data["creation_date"].startswith("2026-01-15T10:30:00")
    assert data["pages"] == 3


def test_read_pdf_without_metadata(client):
    writer = PdfWriter(clone_from=PdfReader(BytesIO(make_pdf(1))))
    writer.metadata = None
    output = BytesIO()
    writer.write(output)

    data = read(client, output.getvalue()).json()

    assert data["pages"] == 1
    assert data["title"] is None and data["creation_date"] is None


def test_edit_round_trip(client):
    response = edit(client, make_pdf_with_metadata(), title="Nuevo título", author="Luis", subject="", keywords="uno")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == 'attachment; filename="informe-metadatos.pdf"'
    reader = PdfReader(BytesIO(response.content))
    assert len(reader.pages) == 2
    assert reader.metadata["/Title"] == "Nuevo título"
    assert reader.metadata["/Author"] == "Luis"
    assert "/Subject" not in reader.metadata
    assert reader.metadata["/Keywords"] == "uno"
    assert "/Metadata" not in reader.trailer["/Root"]

    data = read(client, response.content).json()
    assert data["title"] == "Nuevo título"
    assert data["subject"] is None


def test_missing_fields_are_removed_but_creator_is_kept(client):
    response = edit(client, make_pdf_with_metadata(), title="Otro")

    reader = PdfReader(BytesIO(response.content))
    assert reader.metadata["/Title"] == "Otro"
    assert "/Author" not in reader.metadata
    assert reader.metadata["/Producer"] == "LibreOffice"
    assert b"Ana" not in response.content


def test_remove_all_leaves_no_metadata(client):
    response = edit(client, make_pdf_with_metadata(), title="Ignorado", remove_all="true")

    assert response.status_code == 200
    reader = PdfReader(BytesIO(response.content))
    assert len(reader.pages) == 2
    assert not reader.metadata
    assert "/Metadata" not in reader.trailer["/Root"]
    assert b"Ana" not in response.content
    data = read(client, response.content).json()
    assert all(data[field] is None for field in data if field != "pages")


def test_custom_output_name(client):
    response = edit(client, make_pdf(1), output_name="limpio")

    assert response.headers["content-disposition"] == 'attachment; filename="limpio.pdf"'


def test_too_long_field_is_rejected(client):
    response = edit(client, make_pdf(1), title="x" * (main.MAX_METADATA_LENGTH + 1))

    assert response.status_code == 400
    assert "500" in response.json()["detail"]


def test_non_pdf_is_rejected(client):
    assert read(client, b"x", filename="foto.jpg").status_code == 400
    assert edit(client, b"x", filename="foto.jpg").status_code == 400


def test_corrupt_pdf_is_rejected(client):
    assert read(client, b"%PDF-1.4 not really a pdf").status_code == 400
    assert edit(client, b"%PDF-1.4 not really a pdf").status_code == 400
