import json
import os
import time
from io import BytesIO

from fastapi.testclient import TestClient
from PIL import Image
import pytest

import app.main as main
from conftest import page_layout


def upload(client, content: bytes) -> str:
    response = client.post("/api/documents", files={"file": ("a.pdf", content)})
    assert response.status_code == 200
    return response.json()["id"]


def test_upload_returns_id_and_page_count(client, sample_pdf, documents_dir):
    response = client.post("/api/documents", files={"file": ("a.pdf", sample_pdf)})

    body = response.json()
    assert body["pages"] == 4
    assert (documents_dir / f"{body['id']}.pdf").exists()


def test_upload_rejects_corrupt_pdf_without_leaving_files(client, documents_dir):
    response = client.post("/api/documents", files={"file": ("a.pdf", b"not a pdf")})

    assert response.status_code == 400
    assert list(documents_dir.iterdir()) == []


def test_page_image_is_cacheable_jpeg(client, sample_pdf):
    document_id = upload(client, sample_pdf)

    response = client.get(f"/api/documents/{document_id}/pages/2")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "private, max-age=3600"
    assert Image.open(BytesIO(response.content)).width == main.THUMBNAIL_WIDTH


@pytest.mark.parametrize("requested, expected", [(10, main.MIN_RENDER_WIDTH), (99999, 800)])
def test_page_image_width_is_clamped(client, sample_pdf, requested, expected):
    # Page 2 is 200 points wide; the render scale is capped at 4x (800 px).
    document_id = upload(client, sample_pdf)

    response = client.get(f"/api/documents/{document_id}/pages/2?width={requested}")

    assert Image.open(BytesIO(response.content)).width == expected


def test_page_out_of_range(client, sample_pdf):
    document_id = upload(client, sample_pdf)

    assert client.get(f"/api/documents/{document_id}/pages/9").status_code == 404


def test_invalid_and_unknown_ids(client):
    assert client.get("/api/documents/not-an-id/pages/1").status_code == 404
    assert client.get(f"/api/documents/{'0' * 32}/pages/1").status_code == 410


def test_edit_applies_order_rotation_and_deletion(client, sample_pdf):
    document_id = upload(client, sample_pdf)
    layout = [{"page": 4, "rotation": 90}, {"page": 1}, {"page": 2, "rotation": -90}]

    response = client.post(
        f"/api/documents/{document_id}/edit",
        data={"pages": json.dumps(layout), "output_name": "edited"},
    )

    assert response.status_code == 200
    assert response.headers["content-disposition"] == 'attachment; filename="edited.pdf"'
    assert page_layout(response.content) == [(400, 90), (100, 0), (200, 270)]


def test_download_deletes_the_document(client, sample_pdf, documents_dir):
    document_id = upload(client, sample_pdf)

    client.post(f"/api/documents/{document_id}/edit", data={"pages": json.dumps([{"page": 1}])})

    assert list(documents_dir.iterdir()) == []
    assert client.get(f"/api/documents/{document_id}/pages/1").status_code == 410


@pytest.mark.parametrize(
    "pages",
    ["[]", "not json", json.dumps([{"page": 9}]), json.dumps([{"page": 1, "rotation": 45}])],
)
def test_invalid_edit_keeps_the_document(client, sample_pdf, documents_dir, pages):
    document_id = upload(client, sample_pdf)

    response = client.post(f"/api/documents/{document_id}/edit", data={"pages": pages})

    assert response.status_code == 400
    assert (documents_dir / f"{document_id}.pdf").exists()


def test_delete_document(client, sample_pdf, documents_dir):
    document_id = upload(client, sample_pdf)

    assert client.delete(f"/api/documents/{document_id}").status_code == 204
    assert list(documents_dir.iterdir()) == []


def test_expired_documents_are_swept(client, sample_pdf, documents_dir):
    old_id, recent_id = upload(client, sample_pdf), upload(client, sample_pdf)
    old = time.time() - main.DOCUMENT_TTL_SECONDS - 1
    os.utime(documents_dir / f"{old_id}.pdf", (old, old))

    main.remove_expired_documents()

    assert not (documents_dir / f"{old_id}.pdf").exists()
    assert (documents_dir / f"{recent_id}.pdf").exists()


def test_startup_purges_leftovers(documents_dir, monkeypatch):
    (documents_dir / f"{'a' * 32}.pdf").write_bytes(b"left from a previous run")
    monkeypatch.setattr(main, "DOCUMENT_SWEEP_SECONDS", 3600)

    with TestClient(main.app):
        assert list(documents_dir.iterdir()) == []


def test_two_users_with_the_same_file_are_independent(client, sample_pdf, documents_dir):
    first, second = upload(client, sample_pdf), upload(client, sample_pdf)
    assert first != second

    client.post(f"/api/documents/{first}/edit", data={"pages": json.dumps([{"page": 1}])})

    assert client.get(f"/api/documents/{second}/pages/1").status_code == 200
    response = client.post(f"/api/documents/{second}/edit", data={"pages": json.dumps([{"page": 4}])})
    assert page_layout(response.content) == [(400, 0)]
    assert list(documents_dir.iterdir()) == []
