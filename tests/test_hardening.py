from io import BytesIO
import logging

from pypdf import PdfReader, PdfWriter
import pytest

import app.main as main
import app.config as config
import app.errors as errors
import app.external_tools as external_tools
import app.pdf_helpers as pdf_helpers
import app.uploads as uploads
from conftest import make_pdf, make_png


ENCRYPTED_DETAIL = errors.ENCRYPTED_PDF_MESSAGE


def make_encrypted_pdf(user_password: str = "secreto", owner_password: str | None = None) -> bytes:
    writer = PdfWriter(clone_from=PdfReader(BytesIO(make_pdf(2))))
    writer.encrypt(user_password, owner_password=owner_password)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


@pytest.fixture
def small_limits(monkeypatch):
    # 0.01 MB is about 10 KB: a sample PDF fits, a padded one does not.
    monkeypatch.setattr(config, "MAX_UPLOAD_MB", 0.01)
    monkeypatch.setattr(config, "MAX_TOTAL_UPLOAD_MB", 0.015)


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
        ("/api/extract-text", {}),
        ("/api/ocr", {}),
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
    monkeypatch.setattr(config, "MAX_UPLOAD_MB", 1)
    monkeypatch.setattr(config, "MAX_TOTAL_UPLOAD_MB", len(pdf) * 2.5 / (1024 * 1024))

    response = client.post(
        "/api/merge",
        files=[("files", (f"{n}.pdf", pdf, "application/pdf")) for n in range(3)],
    )

    assert response.status_code == 413
    assert "en total" in response.json()["detail"]


def test_images_to_pdf_rejects_too_big_total(client, monkeypatch):
    png = make_png()
    monkeypatch.setattr(config, "MAX_TOTAL_UPLOAD_MB", len(png) * 1.5 / (1024 * 1024))

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

    monkeypatch.setattr(pdf_helpers, "PdfReader", broken_reader)
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
        ("/api/extract-text", {}),
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
        ("/api/extract-text", {}),
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


@pytest.mark.skipif(external_tools.find_ghostscript() is None, reason="Ghostscript no está instalado")
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
    monkeypatch.setattr(config, "MAX_TOTAL_UPLOAD_MB", 0.01)
    body = make_pdf(1) + b"%" + b"x" * (1_200_000)

    response = client.post("/api/pdf-info", files={"file": ("big.pdf", body, "application/pdf")})

    assert response.status_code == 413
    assert "petición" in response.json()["detail"]
    assert response.headers["X-Frame-Options"] == "DENY"


def test_chunked_upload_is_cut_off_at_the_limit(client, monkeypatch):
    monkeypatch.setattr(config, "MAX_TOTAL_UPLOAD_MB", 0.01)
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


# --- Download names (Content-Disposition) -----------------------------------

@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("informe.pdf", 'attachment; filename="informe.pdf"'),
        ("mi informe.pdf", 'attachment; filename="mi informe.pdf"'),
        ("Informe año ñ.pdf",
         "attachment; filename=\"Informe ano n.pdf\"; filename*=UTF-8''Informe%20a%C3%B1o%20%C3%B1.pdf"),
        ("日本.pdf", "attachment; filename=\"__.pdf\"; filename*=UTF-8''%E6%97%A5%E6%9C%AC.pdf"),
    ],
)
def test_content_disposition_has_ascii_fallback_and_utf8_name(name, expected):
    assert uploads.content_disposition(name) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Informe año ñ", "Informe año ñ"),
        ("Ελληνικά-Кириллица_1", "Ελληνικά-Кириллица_1"),
        ("Café", "Café"),  # decomposed accents are composed first
        ('a<b>c:d"e|f?g*h', "a_b_c_d_e_f_g_h"),
        ("../../etc/passwd", "passwd"),
        ("bad\r\nSet-Cookie: x=1", "bad__Set-Cookie_ x_1"),
        ("rtl‮txt.exe", "rtl_txt.exe"),
        ("   ...   ", "fallback"),
    ],
)
def test_clean_base_name_keeps_letters_of_any_alphabet(raw, expected):
    assert uploads.clean_base_name(raw, "fallback") == expected


@pytest.mark.parametrize(
    "name",
    ['x"; filename="evil.exe', "x\r\nX-Injected: 1", "x\\\";a", "ñ\r\nX-Injected: 1"],
)
def test_download_name_cannot_inject_headers(client, name):
    response = client.post("/api/merge", files=[("files", ("a.pdf", make_pdf(1)))], data={"output_name": name})

    assert response.status_code == 200
    header = response.headers["content-disposition"]
    assert "x-injected" not in response.headers
    assert "\r" not in header and "\n" not in header
    # Only the two quotes around the filename="..." value: no way out of it.
    assert header.count('"') == 2
    assert 'filename="evil.exe"' not in header


# --- FastAPI validation errors -> Spanish 400 ---------------------------------

@pytest.mark.parametrize(
    ("url", "files", "data", "field"),
    [
        ("/api/pdf-to-images", {"file": ("a.pdf", make_pdf(1))}, {"dpi": "abc"}, "dpi"),
        ("/api/split", {"file": ("a.pdf", make_pdf(1))}, {"start_page": "abc"}, "start_page"),
        ("/api/merge", None, {"output_name": "x"}, "files"),
        ("/api/metadata", {"file": ("a.pdf", make_pdf(1))}, {"remove_all": "maybe"}, "remove_all"),
        ("/api/edit-pages", {"file": ("a.pdf", make_pdf(1))}, {}, "pages"),
    ],
)
def test_validation_errors_are_spanish_400(client, url, files, data, field):
    response = client.post(url, files=files, data=data)

    assert response.status_code == 400
    assert response.json() == {"detail": f"Falta el campo «{field}» o su valor no es válido."}
    # The handler runs inside the middlewares, so the headers are still set.
    assert response.headers["x-frame-options"] == "DENY"
    assert "content-security-policy" in response.headers


def test_several_invalid_fields_are_all_named(client):
    response = client.post("/api/split", data={"start_page": "abc"})

    assert response.status_code == 400
    assert response.json()["detail"] == "Faltan los campos «file», «start_page» o sus valores no son válidos."


def test_validation_error_in_a_path_parameter(client):
    response = client.get("/api/documents/" + "a" * 32 + "/pages/abc")

    assert response.status_code == 400
    assert "«page»" in response.json()["detail"]
