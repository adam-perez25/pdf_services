import app.main as main


def test_health_reports_ok(client):
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["storage"] is True
    assert isinstance(body["libreoffice"], bool)


def test_health_leaves_no_files(client, documents_dir):
    client.get("/health")

    assert list(documents_dir.iterdir()) == []


def test_health_fails_when_storage_is_unusable(client, tmp_path, monkeypatch):
    # A regular file where the folder should be makes uploads impossible.
    blocked = tmp_path / "not-a-folder"
    blocked.write_text("x")
    monkeypatch.setattr(main, "DOCUMENTS_DIR", blocked)

    response = client.get("/health")

    assert response.status_code == 503
    assert response.json()["status"] == "error"


def test_health_reports_missing_libreoffice(client, monkeypatch):
    monkeypatch.setattr(main, "find_soffice", lambda: None)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["libreoffice"] is False
