"""HTML pages: one plain template per tool, read from templates/ on each request."""
from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app import config


router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def home():
    return render_page("index.html")


@router.get("/merge", response_class=HTMLResponse)
async def merge_page():
    return render_page("merge.html")


@router.get("/split", response_class=HTMLResponse)
async def split_page():
    return render_page("split.html")


@router.get("/images", response_class=HTMLResponse)
async def images_page():
    return render_page("images.html")


@router.get("/word", response_class=HTMLResponse)
async def word_page():
    return render_page("word.html")


@router.get("/excel", response_class=HTMLResponse)
async def excel_page():
    return render_page("excel.html")


@router.get("/powerpoint", response_class=HTMLResponse)
async def powerpoint_page():
    return render_page("powerpoint.html")


@router.get("/pages", response_class=HTMLResponse)
async def pages_page():
    return render_page("pages.html")


@router.get("/compress", response_class=HTMLResponse)
async def compress_page():
    return render_page("compress.html")


@router.get("/pdf-to-images", response_class=HTMLResponse)
async def pdf_to_images_page():
    return render_page("pdf-to-images.html")


@router.get("/protect", response_class=HTMLResponse)
async def protect_page():
    return render_page("protect.html")


@router.get("/metadata", response_class=HTMLResponse)
async def metadata_page():
    return render_page("metadata.html")


def render_page(template_name: str) -> HTMLResponse:
    html = (config.BASE_DIR / "templates" / template_name).read_text(encoding="utf-8")
    return HTMLResponse(html)


@router.get("/stamp", response_class=HTMLResponse)
async def stamp_page():
    return render_page("stamp.html")


# Flujo de trabajo: the chaining runs in the browser (static/workflow.js), which
# calls the existing /api endpoints one after another, so only the page lives here.
@router.get("/workflow", response_class=HTMLResponse)
async def workflow_page():
    return render_page("workflow.html")


@router.get("/extract-text", response_class=HTMLResponse)
async def extract_text_page():
    return render_page("extract-text.html")


@router.get("/sign", response_class=HTMLResponse)
async def sign_page():
    return render_page("sign.html")


@router.get("/ocr", response_class=HTMLResponse)
async def ocr_page():
    return render_page("ocr.html")
