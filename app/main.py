from contextlib import asynccontextmanager
import mimetypes

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app import config
from app.documents import start_document_sweeper
from app.middleware import (
    CrossSiteRequestGuard,
    UploadSizeLimit,
    add_security_headers,
    revalidate_static_files,
)
from app.routers import (
    compress,
    extract_text,
    health,
    html_pages,
    images,
    merge,
    metadata,
    ocr,
    office,
    pages,
    pdf_to_images,
    protect,
    sign,
    split,
    stamp,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    start_document_sweeper()
    yield


# No /docs, /redoc or /openapi.json: the strict CSP breaks the Swagger pages
# anyway, and the API is described in the README.
app = FastAPI(title="Unir PDF", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.exception_handler(RequestValidationError)
async def validation_error(request, error: RequestValidationError):
    """Answer FastAPI's English 422 (missing field, "abc" for a number...) with
    a Spanish 400 like every other bad input. It runs inside the middlewares,
    so the security headers are still added."""
    fields = []
    for item in error.errors():
        location = [str(part) for part in item.get("loc", ()) if part not in ("body", "query", "path")]
        name = location[0] if location else "petición"
        if name not in fields:
            fields.append(name)
    quoted = ", ".join(f"«{name}»" for name in fields) or "«petición»"
    if len(fields) > 1:
        detail = f"Faltan los campos {quoted} o sus valores no son válidos."
    else:
        detail = f"Falta el campo {quoted} o su valor no es válido."
    return JSONResponse({"detail": detail}, status_code=400)


# Added before the function middlewares below, so they run inside them and
# their 413 / 403 still get the security headers. The guard is outermost of
# the two, so a cross-site upload is refused before its body is read.
app.add_middleware(UploadSizeLimit)
app.add_middleware(CrossSiteRequestGuard)
# The slim image's MIME table has no entry for .woff2, so the bundled fonts
# would otherwise be served as text/plain.
mimetypes.add_type("font/woff2", ".woff2")
app.mount("/static", StaticFiles(directory=config.BASE_DIR / "static"), name="static")
app.middleware("http")(revalidate_static_files)
app.middleware("http")(add_security_headers)

# One router per tool (app/routers/).
app.include_router(html_pages.router)
app.include_router(health.router)
app.include_router(merge.router)
app.include_router(split.router)
app.include_router(pages.router)
app.include_router(images.router)
app.include_router(pdf_to_images.router)
app.include_router(office.router)
app.include_router(compress.router)
app.include_router(protect.router)
app.include_router(stamp.router)
app.include_router(metadata.router)
app.include_router(extract_text.router)
app.include_router(sign.router)
app.include_router(ocr.router)
