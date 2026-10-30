import pytest

import app.main as main


@pytest.mark.parametrize("path", ["/", "/pages", "/health", "/static/common.js"])
def test_responses_carry_security_headers(client, path):
    response = client.get(path)

    assert response.status_code == 200
    for name, value in main.SECURITY_HEADERS.items():
        assert response.headers[name] == value


def test_error_responses_carry_security_headers(client):
    response = client.get("/api/documents/not-an-id/pages/1")

    assert response.status_code == 404
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_templates_have_no_inline_scripts_or_styles():
    # The Content-Security-Policy would block them.
    for template in (main.BASE_DIR / "templates").glob("*.html"):
        html = template.read_text(encoding="utf-8")
        assert "<script>" not in html, template.name
        assert "<style" not in html, template.name
        assert 'style="' not in html, template.name
