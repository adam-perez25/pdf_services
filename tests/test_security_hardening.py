"""Regression tests for the security audit fixes (one section per finding)."""
from contextlib import contextmanager
from io import BytesIO
import json
from pathlib import Path
import struct
from urllib.parse import unquote, urlparse
import xml.etree.ElementTree as ElementTree
import zipfile
import zlib

from PIL import Image
from pypdf import PageObject, PdfReader, PdfWriter
import pytest

import app.main as main
import app.documents as documents
import app.errors as errors
import app.external_tools as external_tools
import app.heavy_jobs as heavy_jobs
import app.image_helpers as image_helpers
import app.middleware as middleware
import app.pdf_helpers as pdf_helpers
import app.uploads as uploads
from conftest import make_pdf, make_png

BASE_DIR = Path(__file__).resolve().parent.parent
needs_ghostscript = pytest.mark.skipif(external_tools.find_ghostscript() is None, reason="Ghostscript is not installed")
needs_libreoffice = pytest.mark.skipif(external_tools.find_soffice() is None, reason="LibreOffice is not installed")


# --- 1. LibreOffice must not fetch linked resources ---------------------------

def profile_settings(profile_path: Path) -> dict[tuple[str, str], str]:
    tree = ElementTree.parse(profile_path / "user" / "registrymodifications.xcu")
    oor = "{http://openoffice.org/2001/registry}"
    return {
        (item.get(f"{oor}path"), prop.get(f"{oor}name")): prop.findtext("value") or ""
        for item in tree.getroot()
        for prop in item
    }


def test_office_conversion_seeds_a_locked_down_profile(client, monkeypatch):
    seen = {}

    def fake_run_tool(command, timeout, env=None):
        option = next(part for part in command if part.startswith("-env:UserInstallation="))
        uri = urlparse(option.split("=", 1)[1])
        profile = Path(unquote(uri.path).lstrip("/") if uri.path[2:3] == ":" else unquote(uri.path))
        seen.update(profile_settings(profile))
        source = Path(command[-1])
        (source.parent / "source.pdf").write_bytes(make_pdf(1))
        return external_tools.subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(external_tools, "find_soffice", lambda: "soffice")
    monkeypatch.setattr(external_tools, "run_tool", fake_run_tool)
    response = client.post("/api/word-to-pdf", files={"file": ("doc.docx", b"PK fake")})

    assert response.status_code == 200
    scripting = "/org.openoffice.Office.Common/Security/Scripting"
    assert seen[(scripting, "BlockUntrustedRefererLinks")] == "true"
    assert seen[(scripting, "DisableActiveContent")] == "true"
    assert seen[("/org.openoffice.Office.Writer/Content/Update", "Link")] == "2"  # never
    assert seen[("/org.openoffice.Office.Calc/Content/Update", "Link")] == "1"  # never
    assert seen[("/org.openoffice.Inet/Settings", "ooInetProxyType")] == "2"
    assert seen[("/org.openoffice.Inet/Settings", "ooInetHTTPProxyPort")] == "9"


def linked_image_odt(href: str) -> bytes:
    content = f'''<?xml version="1.0" encoding="UTF-8"?>
<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
 xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"
 xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"
 xmlns:svg="urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0"
 xmlns:xlink="http://www.w3.org/1999/xlink" office:version="1.2">
<office:body><office:text><text:p>Imagen enlazada
<draw:frame draw:name="i1" text:anchor-type="as-char" svg:width="5cm" svg:height="5cm">
<draw:image xlink:href="{href}" xlink:type="simple" xlink:show="embed" xlink:actuate="onLoad"/>
</draw:frame></text:p></office:text></office:body></office:document-content>'''
    output = BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(zipfile.ZipInfo("mimetype"), "application/vnd.oasis.opendocument.text")
        archive.writestr(
            "META-INF/manifest.xml",
            '<?xml version="1.0" encoding="UTF-8"?><manifest:manifest '
            'xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" manifest:version="1.2">'
            '<manifest:file-entry manifest:full-path="/" '
            'manifest:media-type="application/vnd.oasis.opendocument.text"/>'
            '<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>'
            "</manifest:manifest>",
        )
        archive.writestr("content.xml", content)
    return output.getvalue()


@needs_libreoffice
def test_linked_local_image_is_not_embedded(client, tmp_path):
    # Before the fix, an image linked to file:///... was read from the
    # server's disk and embedded in the PDF.
    secret = tmp_path / "secret.png"
    secret.write_bytes(make_png("red"))
    document = linked_image_odt(secret.as_uri())

    response = client.post("/api/word-to-pdf", files={"file": ("enlazada.docx", document)})

    assert response.status_code == 200
    reader = PdfReader(BytesIO(response.content))
    assert "Imagen enlazada" in reader.pages[0].extract_text()
    assert sum(len(page.images) for page in reader.pages) == 0


# --- 2. PostScript before the PDF header --------------------------------------

POSTSCRIPT = b"%!PS-Adobe-3.0\n/Helvetica findfont 40 scalefont setfont 72 400 moveto (PWNED) show showpage\n"


def polyglot() -> bytes:
    return POSTSCRIPT + make_pdf(1)


def test_polyglot_opens_in_pdfium(tmp_path):
    # The premise of the attack: pdfium tolerates the prefix.
    path = tmp_path / "p.pdf"
    path.write_bytes(polyglot())
    with pdf_helpers.PDFIUM_LOCK:
        document = documents.open_pdfium_document(path)
        assert len(document) == 1
        document.close()


@pytest.mark.parametrize("prefix", [b"", POSTSCRIPT, b"x" * 1000])
def test_strip_before_pdf_header(tmp_path, prefix):
    pdf = make_pdf(2)
    path = tmp_path / "in.pdf"
    path.write_bytes(prefix + pdf)
    pdf_helpers.strip_before_pdf_header(path)
    assert path.read_bytes() == pdf


def test_header_missing_or_too_late_is_rejected(tmp_path):
    path = tmp_path / "in.pdf"
    path.write_bytes(b"x" * 1100 + make_pdf(1))
    with pytest.raises(errors.HTTPException) as error:
        pdf_helpers.strip_before_pdf_header(path)
    assert error.value.status_code == 400


def test_compress_gives_ghostscript_a_clean_pdf(client, monkeypatch, documents_dir):
    received = []

    def fake_run_tool(command, timeout, env=None):
        received.append(Path(command[-1]).read_bytes())
        output = next(part for part in command if part.startswith("-sOutputFile="))
        Path(output.split("=", 1)[1]).write_bytes(b"%PDF-1.4 tiny")
        return external_tools.subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(external_tools, "find_ghostscript", lambda: "gs")
    monkeypatch.setattr(external_tools, "run_tool", fake_run_tool)
    response = client.post("/api/compress", files={"file": ("p.pdf", polyglot())})

    assert response.status_code == 200
    assert received[0].startswith(b"%PDF-")
    assert b"PWNED" not in received[0]


def test_compress_rejects_file_without_header(client, monkeypatch, documents_dir):
    monkeypatch.setattr(external_tools, "find_ghostscript", lambda: "gs")
    monkeypatch.setattr(external_tools, "run_tool", lambda *args, **kwargs: pytest.fail("Ghostscript must not run"))
    response = client.post("/api/compress", files={"file": ("p.pdf", b"%!PS\n(PWNED) show\n")})
    assert response.status_code == 400
    assert response.json()["detail"] == pdf_helpers.NOT_A_PDF_MESSAGE
    assert list(documents_dir.iterdir()) == []


def test_ocr_gives_ocrmypdf_a_clean_pdf(client, monkeypatch, documents_dir):
    received = []

    def fake_run_tool(command, timeout, env=None):
        received.append(Path(command[-2]).read_bytes())
        Path(command[-1]).write_bytes(make_pdf(1))
        return external_tools.subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(external_tools, "ocr_available", lambda: True)
    monkeypatch.setattr(external_tools, "run_tool", fake_run_tool)
    response = client.post("/api/ocr", files={"file": ("p.pdf", polyglot())})

    assert response.status_code == 200
    assert received[0].startswith(b"%PDF-")
    assert b"PWNED" not in received[0]


@needs_ghostscript
def test_real_ghostscript_does_not_run_the_postscript(client, documents_dir):
    response = client.post("/api/compress", files={"file": ("p.pdf", polyglot())})
    assert response.status_code == 200
    assert response.content.startswith(b"%PDF-")
    assert b"PWNED" not in response.content
    text = "".join(page.extract_text() or "" for page in PdfReader(BytesIO(response.content)).pages)
    assert "PWNED" not in text


# --- 3. Capped page lists -------------------------------------------------------

def test_page_list_limit():
    assert pdf_helpers.page_list_limit(1) == pdf_helpers.PAGE_LIST_MIN_LIMIT
    assert pdf_helpers.page_list_limit(1500) == 1500 * pdf_helpers.PAGE_LIST_FACTOR


def test_extract_text_caps_repeated_ranges(client, documents_dir):
    def extract(pages):
        return client.post("/api/extract-text", files={"file": ("a.pdf", make_pdf(4))}, data={"pages": pages})

    too_many = extract(",".join(["1-4"] * 26))  # 104 pages > 100
    assert too_many.status_code == 400
    assert "máximo para este PDF es 100" in too_many.json()["detail"]

    too_long = extract("1," * 600)
    assert too_long.status_code == 400
    assert "1000 caracteres" in too_long.json()["detail"]

    # Within the cap the repeated ranges get past the check (these pages
    # have no text, hence the "no text" answer).
    allowed = extract(",".join(["1-4"] * 25))
    assert allowed.json()["detail"] == main.EMPTY_TEXT_MESSAGE
    assert list(documents_dir.iterdir()) == []


def test_split_zip_caps_ranges(client, monkeypatch):
    def split(ranges):
        return client.post(
            "/api/split-zip", files={"file": ("a.pdf", make_pdf(4))}, data={"mode": "ranges", "ranges": ranges}
        )

    total = split(",".join(["1-4"] * 26))
    assert total.status_code == 400
    assert "máximo para este PDF es 100" in total.json()["detail"]

    monkeypatch.setattr(pdf_helpers, "PAGE_LIST_MIN_LIMIT", 10_000)
    count = split(",".join(["1"] * (main.SPLIT_MAX_RANGES + 1)))
    assert count.status_code == 400
    assert f"{main.SPLIT_MAX_RANGES} rangos" in count.json()["detail"]

    monkeypatch.setattr(pdf_helpers, "PAGE_LIST_MIN_LIMIT", 100)
    normal = split("1-2, 3-4")
    assert normal.status_code == 200
    assert len(zipfile.ZipFile(BytesIO(normal.content)).namelist()) == 2


def test_edit_pages_caps_layout(client):
    def edit(count):
        layout = json.dumps([{"page": 1}] * count)
        return client.post("/api/edit-pages", files={"file": ("a.pdf", make_pdf(4))}, data={"pages": layout})

    assert edit(101).status_code == 400
    assert "máximo para este PDF es 100" in edit(101).json()["detail"]
    allowed = edit(100)
    assert allowed.status_code == 200
    assert len(PdfReader(BytesIO(allowed.content)).pages) == 100


def test_organizer_edit_caps_layout(client):
    document_id = client.post("/api/documents", files={"file": ("a.pdf", make_pdf(4))}).json()["id"]
    response = client.post(
        f"/api/documents/{document_id}/edit", data={"pages": json.dumps([{"page": 2}] * 101)}
    )
    assert response.status_code == 400
    assert "máximo para este PDF es 100" in response.json()["detail"]


# --- 4. Thumbnail pixel cap -------------------------------------------------------

def test_thumbnail_scale_is_capped():
    scale = main.thumbnail_scale(500, 200_000, 2000)
    assert 500 * scale * 200_000 * scale <= main.THUMBNAIL_MAX_PIXELS * 1.001
    assert 200_000 * scale <= main.THUMBNAIL_MAX_SIDE + 1
    # Ordinary pages are unchanged: width / page width, at most 4x.
    assert main.thumbnail_scale(595, 842, 300) == pytest.approx(300 / 595)
    assert main.thumbnail_scale(100, 100, 2000) == main.THUMBNAIL_MAX_SCALE


def test_huge_page_thumbnail_is_bounded(client):
    writer = PdfWriter()
    writer.add_page(PageObject.create_blank_page(width=500, height=200_000))
    output = BytesIO()
    writer.write(output)
    document_id = client.post("/api/documents", files={"file": ("tall.pdf", output.getvalue())}).json()["id"]

    response = client.get(f"/api/documents/{document_id}/pages/1", params={"width": 2000})

    assert response.status_code == 200
    with Image.open(BytesIO(response.content)) as image:
        assert image.width * image.height <= main.THUMBNAIL_MAX_PIXELS * 1.01
        assert max(image.size) <= main.THUMBNAIL_MAX_SIDE + 1


# --- 5. Image decompression bombs ---------------------------------------------------

def png_header_only(width: int, height: int) -> bytes:
    """A tiny PNG that declares width x height pixels (data is truncated)."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(b"\x00" * 64)) + chunk(b"IEND", b"")


@contextmanager
def recorded_heavy_job(megabytes, uploads=()):
    recorded_heavy_job.calls.append(megabytes)
    yield


def test_pillow_bomb_limit_matches_the_cap():
    assert Image.MAX_IMAGE_PIXELS == image_helpers.IMAGE_MAX_PIXELS


def test_images_to_pdf_rejects_pixel_bomb_before_decoding(client, monkeypatch):
    recorded_heavy_job.calls = []
    monkeypatch.setattr(heavy_jobs, "heavy_job", recorded_heavy_job)
    bomb = png_header_only(13_000, 13_000)
    assert len(bomb) < 1000

    response = client.post("/api/images-to-pdf", files=[("images", ("bomba.png", bomb, "image/png"))])

    assert response.status_code == 400
    assert "demasiado grande" in response.json()["detail"]
    assert recorded_heavy_job.calls == []  # rejected before any work


def test_images_to_pdf_estimate_counts_decoded_pixels(client, monkeypatch):
    recorded_heavy_job.calls = []
    monkeypatch.setattr(heavy_jobs, "heavy_job", recorded_heavy_job)
    big = png_header_only(9_000, 9_000)  # 81 Mpx, under the cap, file < 1 KB

    client.post("/api/images-to-pdf", files=[
        ("images", ("normal.png", make_png(), "image/png")),
        ("images", ("grande.png", big, "image/png")),
    ])

    assert recorded_heavy_job.calls == [heavy_jobs.decoded_image_mb(81_000_000)]
    assert recorded_heavy_job.calls[0] >= heavy_jobs.HEAVY_JOB_MIN_MB  # it waits its turn


def test_small_images_still_skip_the_queue(client, monkeypatch):
    recorded_heavy_job.calls = []
    monkeypatch.setattr(heavy_jobs, "heavy_job", recorded_heavy_job)
    response = client.post("/api/images-to-pdf", files=[("images", ("a.png", make_png(), "image/png"))])
    assert response.status_code == 200
    assert recorded_heavy_job.calls[0] < heavy_jobs.HEAVY_JOB_MIN_MB


def test_job_estimate_minimum_applies_to_small_files():
    assert heavy_jobs.job_estimate_mb("images-to-pdf", 1000, minimum_mb=500) == 500
    assert heavy_jobs.job_estimate_mb("images-to-pdf", 1000) == 0


def test_signature_pixel_bomb_is_rejected(client):
    document_id = client.post("/api/documents", files={"file": ("a.pdf", make_pdf(1))}).json()["id"]
    placements = json.dumps([{"page": 1, "x": 0.1, "y": 0.1, "width": 0.3, "height": 0.1}])
    response = client.post(
        f"/api/documents/{document_id}/sign",
        files={"signature": ("firma.png", png_header_only(13_000, 13_000), "image/png")},
        data={"placements": placements},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "La imagen de la firma es demasiado grande."


# --- 8. Cross-site requests ------------------------------------------------------------

def pdf_info(client, headers):
    return client.post("/api/pdf-info", files={"file": ("a.pdf", make_pdf(1))}, headers=headers)


@pytest.mark.parametrize("headers", [
    {"Sec-Fetch-Site": "cross-site"},
    {"Sec-Fetch-Site": "same-site"},
    {"Origin": "http://evil.example"},
    {"Origin": "http://testserver.evil.example"},
    {"Origin": "http://testserver:8001"},
    {"Sec-Fetch-Site": "cross-site", "Origin": "http://testserver"},
])
def test_cross_site_post_is_rejected(client, headers):
    response = pdf_info(client, headers)
    assert response.status_code == 403
    assert response.json()["detail"] == middleware.CROSS_SITE_MESSAGE
    assert response.headers["X-Frame-Options"] == "DENY"


@pytest.mark.parametrize("headers", [
    {},
    {"Sec-Fetch-Site": "same-origin"},
    {"Sec-Fetch-Site": "none"},
    {"Origin": "http://testserver"},
    {"Origin": "http://TESTSERVER:80"},
    {"Origin": "null"},
    # A modern browser on the app's own page with Referrer-Policy: no-referrer.
    {"Sec-Fetch-Site": "same-origin", "Origin": "null"},
])
def test_same_origin_and_scripted_posts_work(client, headers):
    assert pdf_info(client, headers).status_code == 200


def test_cross_site_delete_is_rejected(client, documents_dir):
    document_id = client.post("/api/documents", files={"file": ("a.pdf", make_pdf(1))}).json()["id"]
    response = client.delete(f"/api/documents/{document_id}", headers={"Sec-Fetch-Site": "cross-site"})
    assert response.status_code == 403
    assert (documents_dir / f"{document_id}.pdf").exists()


def test_cross_site_get_is_not_affected(client):
    assert client.get("/health", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 200
    assert client.get("/", headers={"Origin": "http://evil.example"}).status_code == 200


# --- 9. Info items -------------------------------------------------------------------------

@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_api_docs_are_disabled(client, path):
    assert client.get(path).status_code == 404


def test_output_names_are_capped(client):
    long_name = "a" * 5000
    assert uploads.clean_output_name(long_name) == "a" * uploads.MAX_OUTPUT_NAME_LENGTH + ".pdf"
    assert uploads.clean_output_name(long_name + ".pdf") == "a" * uploads.MAX_OUTPUT_NAME_LENGTH + ".pdf"
    assert main.clean_text_output_name(long_name, "texto") == "a" * uploads.MAX_OUTPUT_NAME_LENGTH + ".txt"
    assert uploads.clean_output_name("informe.pdf") == "informe.pdf"

    response = client.post("/api/merge", files=[("files", ("a.pdf", make_pdf(1)))], data={"output_name": long_name})
    assert response.status_code == 200
    assert len(response.headers["content-disposition"]) < 200


# --- 6/7. Container hardening in docker-compose.yml -------------------------------------------

def test_compose_hardening():
    compose_file = BASE_DIR / "docker-compose.yml"
    if not compose_file.exists():
        pytest.skip("docker-compose.yml is not in the image (tests run inside Docker)")
    compose = compose_file.read_text(encoding="utf-8")
    for line in ("init: true", "cap_drop:", "- ALL", '"no-new-privileges:true"', "pids_limit: 512"):
        assert line in compose
    assert "read_only" not in compose
