from pathlib import Path
import re

import pytest

from app.routers import html_pages

ROOT = Path(__file__).resolve().parent.parent
TOOLS_JS = ROOT / "static" / "tools.js"
TEMPLATES_DIR = ROOT / "templates"


def page_routes() -> set[str]:
    return {route.path for route in html_pages.router.routes if route.path != "/"}


def tools_js_entries() -> list[dict[str, str]]:
    code = TOOLS_JS.read_text(encoding="utf-8")
    tools = code[code.index("const TOOLS"):code.index("];", code.index("const TOOLS"))]
    return [dict(re.findall(r'(\w+): "([^"]*)"', line)) for line in tools.splitlines() if "path:" in line]


def tool_groups() -> set[str]:
    code = TOOLS_JS.read_text(encoding="utf-8")
    groups = code[code.index("const TOOL_GROUPS"):code.index("];", code.index("const TOOL_GROUPS"))]
    return set(re.findall(r'id: "([^"]+)"', groups))


def test_tools_js_lists_every_page_route():
    paths = [tool["path"] for tool in tools_js_entries()]
    assert len(paths) == len(set(paths))
    assert set(paths) == page_routes()


def test_every_tool_has_a_known_group_and_texts():
    groups = tool_groups()
    for tool in tools_js_entries():
        assert tool["group"] in groups, tool
        assert tool["title"] and tool["summary"], tool
    # Every group holds at least one tool, so the home page has no empty section.
    assert groups == {tool["group"] for tool in tools_js_entries()}


def test_home_noscript_links_every_tool(client):
    html = client.get("/").text
    noscript = html[html.index("<noscript>"):html.index("</noscript>")]
    assert set(re.findall(r'href="(/[^"]*)"', noscript)) == page_routes()
    assert "/static/tools.js" in html
    assert 'id="tool-search"' in html


@pytest.mark.parametrize("path", sorted(page_routes()))
def test_tool_pages_have_side_nav_and_skip_link(client, path):
    html = client.get(path).text
    assert 'id="side-nav"' in html
    assert '<a class="skip-link" href="#main">' in html
    assert 'id="main"' in html
    # The fallback link keeps the menu reachable without JavaScript.
    assert '<a class="back-button" href="/">' in html
    # tools.js loads before the page scripts.
    assert html.index("/static/tools.js") < html.index("/static/common.js")


def test_tools_script_is_served(client):
    response = client.get("/static/tools.js")
    assert response.status_code == 200
    assert "TOOL_GROUPS" in response.text


def test_stylesheet_has_no_monospace_labels():
    css = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
    assert "DM Mono" not in css
    assert "text-transform: uppercase" not in css
