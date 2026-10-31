from pathlib import Path
import re

import pytest

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
TEMPLATES = sorted(TEMPLATES_DIR.glob("*.html"))
STYLES = TEMPLATES_DIR.parent / "static" / "styles.css"
COLOR_VALUE = re.compile(r"#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?)\(|\bwhite\b|\bblack\b")


def hardcodes_color(prop: str, value: str) -> bool:
    # var(--white) is a palette variable, not a color.
    return not prop.startswith("--") and bool(COLOR_VALUE.search(re.sub(r"var\([^)]*\)", "", value)))


def strip_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def block_after(css: str, marker: str) -> str:
    """Return the text inside the braces that follow marker, matching nesting."""
    start = css.index("{", css.index(marker))
    depth = 0
    for index in range(start, len(css)):
        if css[index] == "{":
            depth += 1
        elif css[index] == "}":
            depth -= 1
            if depth == 0:
                return css[start + 1:index]
    raise AssertionError(f"unclosed block after {marker}")


def declarations(css: str):
    """Yield (property, value) for every declaration in the stylesheet."""
    for body in re.findall(r"\{([^{}]*)\}", css):
        for declaration in body.split(";"):
            if ":" in declaration:
                prop, value = declaration.split(":", 1)
                yield prop.strip(), value.strip()


def test_stylesheet_has_dark_mode(client):
    response = client.get("/static/styles.css")

    assert response.status_code == 200
    css = strip_comments(response.text)
    # The dark block redefines the palette on :root instead of hardcoding
    # colors per rule; only what is inside the media query counts.
    dark_root = block_after(block_after(css, "@media (prefers-color-scheme: dark)"), ":root")
    for variable in ("--ink", "--paper", "--white", "--line", "--accent", "--success", "--error"):
        assert re.search(rf"{variable}\s*:", dark_root), variable


def test_rules_use_palette_variables():
    css = strip_comments(STYLES.read_text(encoding="utf-8"))
    # Colors only live in custom properties; rules must use var(--...). This
    # looks at declaration values, so #id selectors and comments do not count.
    hardcoded = [
        f"{prop}: {value}"
        for prop, value in declarations(css)
        if hardcodes_color(prop, value)
    ]
    assert hardcoded == []


def test_palette_check_catches_hardcoded_colors():
    css = (
        "/* #abc in a comment */ #main { color: var(--white); } "
        ".x { background: #fff; } .y { border: 1px solid white; } :root { --ink: #111; }"
    )
    found = [p for p, v in declarations(strip_comments(css)) if hardcodes_color(p, v)]
    assert found == ["background", "border"]


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda path: path.name)
def test_template_declares_color_scheme(template):
    html = template.read_text(encoding="utf-8")
    assert '<meta name="color-scheme" content="light dark">' in html


def test_pages_serve_color_scheme_meta(client):
    response = client.get("/")
    assert response.status_code == 200
    assert 'name="color-scheme"' in response.text
