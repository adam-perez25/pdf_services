from pathlib import Path
import re

import pytest

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
TEMPLATES = sorted(TEMPLATES_DIR.glob("*.html"))


def test_stylesheet_has_dark_mode(client):
    response = client.get("/static/styles.css")

    assert response.status_code == 200
    css = response.text
    assert "prefers-color-scheme: dark" in css
    # The dark block redefines the palette instead of hardcoding colors per rule.
    dark_block = css.split("prefers-color-scheme: dark", 1)[1]
    for variable in ("--ink", "--paper", "--white", "--line", "--accent", "--success", "--error"):
        assert f"{variable}:" in dark_block


def test_rules_use_palette_variables():
    css = (TEMPLATES_DIR.parent / "static" / "styles.css").read_text(encoding="utf-8")
    # Outside custom property definitions, rules should not hardcode hex colors.
    rules = [line for line in css.splitlines() if not line.strip().startswith("--")]
    hex_colors = [line for line in rules if re.search(r"#[0-9a-fA-F]{3,8}\b", line)]
    assert hex_colors == []


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda path: path.name)
def test_template_declares_color_scheme(template):
    html = template.read_text(encoding="utf-8")
    assert '<meta name="color-scheme" content="light dark">' in html


def test_pages_serve_color_scheme_meta(client):
    response = client.get("/")
    assert response.status_code == 200
    assert 'name="color-scheme"' in response.text
