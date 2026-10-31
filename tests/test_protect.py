from io import BytesIO

from pypdf import PdfReader, PdfWriter

from conftest import make_pdf


def post(client, endpoint: str, content: bytes, **data):
    return client.post(
        endpoint,
        files={"file": ("informe.pdf", content, "application/pdf")},
        data=data,
    )


def protect(client, content: bytes, password: str = "secreto", **data):
    return post(client, "/api/protect", content, password=password, **data)


def unlock(client, content: bytes, password: str = "secreto", **data):
    return post(client, "/api/unlock", content, password=password, **data)


def test_protect_page_is_served(client):
    response = client.get("/protect")

    assert response.status_code == 200
    assert "/static/protect.js" in response.text


def test_protect_then_unlock_round_trip(client):
    original = make_pdf(3)

    protected = protect(client, original, output_name="informe-protegido")
    assert protected.status_code == 200
    assert protected.headers["content-type"] == "application/pdf"
    assert 'filename="informe-protegido.pdf"' in protected.headers["content-disposition"]

    reader = PdfReader(BytesIO(protected.content))
    assert reader.is_encrypted
    assert reader.decrypt("secreto")
    encrypt = reader.trailer["/Encrypt"].get_object()
    assert encrypt["/V"] == 5 and encrypt["/R"] == 6  # AES-256 (PDF 2.0)
    assert len(reader.pages) == 3

    unlocked = unlock(client, protected.content, output_name="informe-desbloqueado.pdf")
    assert unlocked.status_code == 200
    assert 'filename="informe-desbloqueado.pdf"' in unlocked.headers["content-disposition"]
    reader = PdfReader(BytesIO(unlocked.content))
    assert not reader.is_encrypted
    assert len(reader.pages) == 3


def test_unlock_with_wrong_password(client):
    protected = protect(client, make_pdf(1)).content

    response = unlock(client, protected, password="otra")

    assert response.status_code == 400
    assert response.json()["detail"] == "Contraseña incorrecta"


def test_protect_rejects_already_encrypted_pdf(client):
    protected = protect(client, make_pdf(1)).content

    response = protect(client, protected, password="nueva")

    assert response.status_code == 400
    assert "ya está protegido" in response.json()["detail"]


def test_unlock_rejects_unencrypted_pdf(client):
    response = unlock(client, make_pdf(1))

    assert response.status_code == 400
    assert response.json()["detail"] == "Este PDF no está protegido con contraseña."


def test_empty_password_is_rejected(client):
    assert protect(client, make_pdf(1), password="").status_code == 400
    response = post(client, "/api/protect", make_pdf(1))
    assert response.status_code == 400
    assert response.json()["detail"] == "Escribe una contraseña."

    protected = protect(client, make_pdf(1)).content
    response = unlock(client, protected, password="")
    assert response.status_code == 400
    assert response.json()["detail"] == "Escribe la contraseña del PDF."


def test_unlock_owner_only_pdf_without_password(client):
    # Restrictions only: empty user password, so it opens without one.
    writer = PdfWriter(clone_from=PdfReader(BytesIO(make_pdf(2))))
    writer.encrypt(user_password="", owner_password="dueño", algorithm="AES-256")
    restricted = BytesIO()
    writer.write(restricted)

    response = unlock(client, restricted.getvalue(), password="")

    assert response.status_code == 200
    reader = PdfReader(BytesIO(response.content))
    assert not reader.is_encrypted
    assert len(reader.pages) == 2


def test_too_long_password_is_rejected(client):
    response = protect(client, make_pdf(1), password="x" * 128)

    assert response.status_code == 400


def test_non_pdf_is_rejected(client):
    response = client.post(
        "/api/protect",
        files={"file": ("notas.txt", b"hola", "text/plain")},
        data={"password": "secreto"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "El archivo debe ser un PDF."


def test_invalid_pdf_returns_400(client):
    response = protect(client, b"esto no es un pdf")

    assert response.status_code == 400
    assert response.json()["detail"].startswith("No se pudo proteger el PDF")
