from io import BytesIO
import logging

from pypdf import PdfReader, PdfWriter
import pytest

import app.main as main
from conftest import make_pdf, make_png


ENCRYPTED_DETAIL = main.ENCRYPTED_PDF_MESSAGE


def make_encrypted_pdf(user_password: str = "secreto", owner_password: str | None = None) -> bytes:
    writer = PdfWriter(clone_from=PdfReader(BytesIO(make_pdf(2))))
    writer.encrypt(user_password, owner_password=owner_password)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


@pytest.fixture
def small_limits(monkeypatch):
    # 0.01 MB is about 10 KB: a sample PDF fits, a padded one does not.
    monkeypatch.setattr(main, "MAX_UPLOAD_MB", 0.01)
    monkeypatch.setattr(main, "MAX_TOTAL_UPLOAD_MB", 0.015)


BIG_PDF = make_pdf(1) + b"%" + b"x" * 20_000


@pytest.mark.parametrize(
    "url, data",
    [
        ("/api/pdf-info", {}),
        ("/api/split", {}),
        ("/api/split-zip", {}),
        ("/api/documents", {}),
        ("/api/edit-pages", {"pages": '[{"page": 1}]'}),
        ("/api/pdf-to-images", {}),
        ("/api/compress", {}),
        ("/api/protect", {"password": "secreto"}),
        ("/api/unlock", {"password": "secreto"}),
        ("/api/stamp", {}),
        ("/api/metadata/read", {}),
        ("/api/metadata", {}),
    ],
)
def test_single_pdf_endpoints_reject_too_big_files(client, small_limits, url, data):
    response = client.post(url, files={"file": ("big.pdf", BIG_PDF, "application/pdf")}, data=data)

    assert response.status_code == 413
    assert "MB" in response.json()["detail"]


def test_office_conversion_rejects_too_big_files(client, small_limits):
    response = client.post(
        "/api/word-to-pdf",
        files={"file": ("big.docx", b"x" * 20_000, "application/octet-stream")},
    )

    assert response.status_code == 413


def test_merge_rejects_one_too_big_file(client, small_limits):
    response = client.post(
        "/api/merge",
        files=[
            ("files", ("a.pdf", make_pdf(1), "application/pdf")),
            ("files", ("big.pdf", BIG_PDF, "application/pdf")),
        ],
    )

    assert response.status_code == 413
    assert "Cada archivo" in response.json()["detail"]


def test_merge_rejects_too_big_total(client, monkeypatch):
    pdf = make_pdf(1)
    monkeypatch.setattr(main, "MAX_UPLOAD_MB", 1)
    monkeypatch.setattr(main, "MAX_TOTAL_UPLOAD_MB", len(pdf) * 2.5 / (1024 * 1024))

    response = client.post(
        "/api/merge",
        files=[("files", (f"{n}.pdf", pdf, "application/pdf")) for n in range(3)],
    )

    assert response.status_code == 413
    assert "en total" in response.json()["detail"]


def test_images_to_pdf_rejects_too_big_total(client, monkeypatch):
    png = make_png()
    monkeypatch.setattr(main, "MAX_TOTAL_UPLOAD_MB", len(png) * 1.5 / (1024 * 1024))

    response = client.post(
        "/api/images-to-pdf",
        files=[("images", (f"{n}.png", png, "image/png")) for n in range(2)],
    )

    assert response.status_code == 413


def test_files_within_the_limit_are_accepted(client, small_limits):
    response = client.post("/api/pdf-info", files={"file": ("a.pdf", make_pdf(2), "application/pdf")})

    assert response.status_code == 200
    assert response.json() == {"pages": 2}


def test_errors_do_not_leak_exception_text(client, monkeypatch, caplog):
    def broken_reader(source):
        raise RuntimeError("detalle-interno-secreto /ruta/privada")

    monkeypatch.setattr(main, "PdfReader", broken_reader)
    with caplog.at_level(logging.WARNING):
        response = client.post("/api/pdf-info", files={"file": ("a.pdf", make_pdf(1), "application/pdf")})

    assert response.status_code == 400
    assert response.json()["detail"] == "No se pudo leer el PDF."
    assert "detalle-interno-secreto" in caplog.text


@pytest.mark.parametrize(
    "url, data",
    [
        ("/api/pdf-info", {}),
        ("/api/split", {}),
        ("/api/split-zip", {}),
        ("/api/documents", {}),
        ("/api/edit-pages", {"pages": '[{"page": 1}]'}),
        ("/api/protect", {"password": "secreto"}),
        ("/api/unlock", {"password": "secreto"}),
        ("/api/stamp", {}),
        ("/api/metadata/read", {}),
        ("/api/metadata", {}),
    ],
)
def test_corrupt_pdf_error_is_generic(client, url, data):
    response = client.post(url, files={"file": ("bad.pdf", b"not a pdf at all", "application/pdf")}, data=data)

    assert response.status_code == 400
    assert response.json()["detail"].startswith("No se pudo")
    assert ":" not in response.json()["detail"]


@pytest.mark.parametrize(
    "url, data",
    [
        ("/api/pdf-info", {}),
        ("/api/split", {}),
        ("/api/split-zip", {}),
        ("/api/documents", {}),
        ("/api/edit-pages", {"pages": '[{"page": 1}]'}),
        ("/api/pdf-to-images", {}),
        ("/api/stamp", {}),
        ("/api/metadata/read", {}),
        ("/api/metadata", {}),
    ],
)
def test_encrypted_pdf_is_rejected(client, documents_dir, url, data):
    response = client.post(
        url, files={"file": ("locked.pdf", make_encrypted_pdf(), "application/pdf")}, data=data
    )

    assert response.status_code == 400
    assert response.json()["detail"] == ENCRYPTED_DETAIL
    assert list(documents_dir.iterdir()) == []


def test_merge_rejects_encrypted_pdf(client):
    response = client.post(
        "/api/merge",
        files=[
            ("files", ("a.pdf", make_pdf(1), "application/pdf")),
            ("files", ("locked.pdf", make_encrypted_pdf(), "application/pdf")),
        ],
    )

    assert response.status_code == 400
    assert response.json()["detail"] == ENCRYPTED_DETAIL


@pytest.mark.skipif(main.find_ghostscript() is None, reason="Ghostscript no está instalado")
def test_compress_rejects_encrypted_pdf(client, documents_dir):
    response = client.post(
        "/api/compress", files={"file": ("locked.pdf", make_encrypted_pdf(), "application/pdf")}
    )

    assert response.status_code == 400
    assert response.json()["detail"] == ENCRYPTED_DETAIL
    assert list(documents_dir.iterdir()) == []


def test_pdf_with_only_owner_password_still_works(client):
    content = make_encrypted_pdf(user_password="", owner_password="propietario")

    info = client.post("/api/pdf-info", files={"file": ("owner.pdf", content, "application/pdf")})
    upload = client.post("/api/documents", files={"file": ("owner.pdf", content, "application/pdf")})

    assert info.status_code == 200
    assert info.json() == {"pages": 2}
    assert upload.status_code == 200
    assert upload.json()["pages"] == 2


def test_too_big_content_length_is_rejected_before_reading(client, monkeypatch):
    # Limit is MAX_TOTAL_UPLOAD_MB + MULTIPART_OVERHEAD_MB (1 MB) = ~1.01 MB.
    monkeypatch.setattr(main, "MAX_TOTAL_UPLOAD_MB", 0.01)
    body = make_pdf(1) + b"%" + b"x" * (1_200_000)

    response = client.post("/api/pdf-info", files={"file": ("big.pdf", body, "application/pdf")})

    assert response.status_code == 413
    assert "petición" in response.json()["detail"]
    assert response.headers["X-Frame-Options"] == "DENY"


def test_chunked_upload_is_cut_off_at_the_limit(client, monkeypatch):
    monkeypatch.setattr(main, "MAX_TOTAL_UPLOAD_MB", 0.01)
    chunks_sent = []

    def body():
        # No Content-Length: the middleware has to count the bytes.
        for _ in range(40):
            chunks_sent.append(1)
            yield b"x" * 65_536

    response = client.post(
        "/api/pdf-info",
        content=body(),
        headers={"Content-Type": "multipart/form-data; boundary=abc"},
    )

    assert response.status_code == 413
    assert "petición" in response.json()["detail"]


def test_requests_within_the_limit_pass_the_middleware(client):
    response = client.post("/api/pdf-info", files={"file": ("a.pdf", make_pdf(2), "application/pdf")})

    assert response.status_code == 200
