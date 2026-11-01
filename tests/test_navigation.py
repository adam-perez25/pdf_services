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


@pytest.mark.parametrize("path", sorted(page_routes()))
def test_tool_pages_share_one_layout(client, path):
    html = client.get(path).text
    # The three-step strip, starting on step 1.
    strip = html[html.index('<ol class="steps"'):html.index("</ol>", html.index('<ol class="steps"'))]
    assert strip.count('<li class="step"') == 3
    assert strip.count('aria-current="step"') == 1
    # Files on one side, options and the action bar (button + status) on the other.
    form = html[html.index('class="tool-layout'):html.index("</form>")]
    assert form.index('class="tool-files"') < form.index('class="tool-options"')
    bar = form[form.index('class="action-bar"'):]
    assert 'class="primary-button"' in bar
    assert 'class="status" role="status"' in bar


def test_reorderable_lists_use_the_shared_handle():
    for script in ("merge.js", "images.js", "workflow.js"):
        code = (ROOT / "static" / script).read_text(encoding="utf-8")
        assert "reorderableList(" in code, script
        assert "dragHandle(" in code, script
        # The old row-wide HTML5 drag is gone: only the handle drags.
        assert "draggable = true" not in code, script
    pages = (ROOT / "static" / "pages.js").read_text(encoding="utf-8")
    assert "dragHandle(" in pages
    common = (ROOT / "static" / "common.js").read_text(encoding="utf-8")
    for needle in ("ArrowUp", "ArrowDown", "drop-line", 'aria-live", "assertive'):
        assert needle in common, needle


def test_every_success_message_shows_the_result_panel():
    # Each tool marks its finished download with the "done" state.
    for script in sorted((ROOT / "static").glob("*.js")):
        if script.name in {"tools.js", "common.js"}:
            continue
        code = script.read_text(encoding="utf-8")
        if "downloadBlob(" in code:
            assert '"done")' in code, script.name


def test_move_buttons_are_touch_sized():
    css = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
    assert re.search(r"\.move-button, \.remove-button \{[^}]*min-width: 44px; min-height: 44px", css)
    assert re.search(r"\.drag-handle \{[^}]*width: 44px; height: 44px", css)
