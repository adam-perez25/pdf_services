from contextlib import asynccontextmanager
import errno
from io import BytesIO
import json
import math
import mimetypes
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
import uuid
from xml.sax.saxutils import escape as xml_escape
import zipfile

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps, ImageSequence, UnidentifiedImageError
from pypdf import PageObject, PdfReader, PdfWriter
from pypdf.generic import (
    DecodedStreamObject,
    DictionaryObject,
    FloatObject,
    NameObject,
    NumberObject,
    TextStringObject,
)
import pypdfium2
from starlette.background import BackgroundTask

from app import config, external_tools, heavy_jobs
from app.documents import (
    DOCUMENT_ID_PATTERN,
    document_gone,
    get_document_path,
    open_pdfium_document,
    protect_from_sweeper,
    remove_expired_documents,
    start_document_sweeper,
)
from app.errors import encrypted_pdf, logger, processing_error
from app.heavy_jobs import decoded_image_mb, job_estimate_mb
from app.image_helpers import ALLOWED_IMAGE_FORMATS, IMAGE_MAX_PIXELS, IMAGE_TOO_BIG_MESSAGE, image_pixels
from app.middleware import (
    CrossSiteRequestGuard,
    UploadSizeLimit,
    add_security_headers,
    revalidate_static_files,
)
from app.pdf_helpers import (
    PDFIUM_LOCK,
    check_page_list_total,
    check_page_selection_length,
    open_pdf_reader,
    open_pdfium_checked,
    parse_page_ranges,
    parse_user_json,
    pdf_number,
    selected_page_numbers,
    strip_before_pdf_header,
    visible_page_box,
)
from app.routers import health, html_pages
from app.uploads import (
    check_upload_sizes,
    clean_base_name,
    clean_output_name,
    content_disposition,
    file_download,
    new_job_paths,
    unique_pdf_names,
    upload_size,
    write_pdf_to_file,
)


THUMBNAIL_WIDTH = 300
MIN_RENDER_WIDTH = 100
MAX_RENDER_WIDTH = 2000


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

# Routers (one per tool), included in this order.
app.include_router(html_pages.router)
app.include_router(health.router)


# The file-processing endpoints are plain functions, not async: FastAPI runs
# them in a worker thread, so a long merge or conversion does not freeze the
# server for everyone else.
@app.post("/api/merge")
def merge_pdfs(
    files: list[UploadFile] = File(...),
    output_name: str = Form("merged.pdf"),
):
    if not files:
        raise HTTPException(status_code=400, detail="Selecciona al menos un PDF.")
    check_upload_sizes(files)

    writer = PdfWriter()
    try:
        for file in files:
            if not file.filename or not file.filename.lower().endswith(".pdf"):
                raise HTTPException(
                    status_code=400,
                    detail="Todos los archivos deben tener extensión PDF.",
                )
        with heavy_jobs.heavy_job(job_estimate_mb("merge", *map(upload_size, files))):
            for file in files:
                writer.append(open_pdf_reader(file.file))

            output_path = write_pdf_to_file(writer)
        writer.close()
    except HTTPException:
        writer.close()
        raise
    except Exception as error:
        writer.close()
        raise processing_error("No se pudieron unir los PDFs.", error) from error
    finally:
        for file in files:
            file.file.close()

    return file_download(output_path, clean_output_name(output_name))


@app.post("/api/pdf-info")
def pdf_info(file: UploadFile = File(...)):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="El archivo debe ser un PDF.")
    check_upload_sizes([file])

    try:
        reader = open_pdf_reader(file.file)
        return {"pages": len(reader.pages)}
    except HTTPException:
        raise
    except Exception as error:
        raise processing_error("No se pudo leer el PDF.", error) from error
    finally:
        file.file.close()


@app.post("/api/split")
def split_pdf(
    file: UploadFile = File(...),
    output_name: str = Form("split-pdf"),
    start_page: int = Form(1),
    end_page: int | None = Form(None),
):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="El archivo debe ser un PDF.")
    check_upload_sizes([file])

    try:
        with heavy_jobs.heavy_job(job_estimate_mb("split", upload_size(file))):
            reader = open_pdf_reader(file.file)
            page_count = len(reader.pages)
            # Only a missing end_page means "to the last page"; 0 is an error.
            if end_page is None:
                end_page = page_count
            if start_page < 1 or end_page < 1 or end_page < start_page or end_page > page_count:
                raise HTTPException(
                    status_code=400,
                    detail=f"El rango debe estar entre 1 y {page_count}.",
                )

            writer = PdfWriter()
            for page_index in range(start_page - 1, end_page):
                writer.add_page(reader.pages[page_index])

            output_path = write_pdf_to_file(writer)
            writer.close()
    except Exception as error:
        if isinstance(error, HTTPException):
            raise
        raise processing_error("No se pudo dividir el PDF.", error) from error
    finally:
        file.file.close()

    return file_download(output_path, clean_output_name(output_name))


SPLIT_MAX_RANGES = 500


def parse_part_names(value: str, count: int) -> list[str | None]:
    """split-zip names: null/empty for all defaults, or a JSON list with one
    string (or null, for the default) per range."""
    message = "Los nombres no son válidos: deben ser una lista con un nombre por rango."
    if not value.strip():
        return [None] * count
    names = parse_user_json(value, message)
    if names is None:
        return [None] * count
    if (
        not isinstance(names, list)
        or len(names) != count
        or not all(name is None or isinstance(name, str) for name in names)
    ):
        raise HTTPException(status_code=400, detail=message)
    return names


@app.post("/api/split-zip")
def split_pdf_zip(
    file: UploadFile = File(...),
    mode: str = Form("pages"),
    ranges: str = Form(""),
    names: str = Form(""),
    base_name: str = Form("split-pdf"),
):
    """Split a PDF into several files in one request and return them as a ZIP.

    mode "pages" makes one PDF per page named <base_name>-page-NNN.pdf;
    mode "ranges" makes one PDF per range in `ranges` ("1-3, 4-6"), named by the
    JSON list `names`. A single resulting part is returned as a plain PDF.
    """
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="El archivo debe ser un PDF.")
    if mode not in {"pages", "ranges"}:
        raise HTTPException(status_code=400, detail="El modo debe ser pages o ranges.")
    check_upload_sizes([file])

    try:
        with heavy_jobs.heavy_job(job_estimate_mb("split", upload_size(file))):
            reader = open_pdf_reader(file.file)
            page_count = len(reader.pages)
            base = clean_base_name(base_name, "split-pdf")
            if base.lower().endswith(".pdf"):
                base = base[:-4]

            if mode == "pages":
                parts = [(page, page) for page in range(1, page_count + 1)]
                digits = max(3, len(str(page_count)))
                part_names = [f"{base}-page-{page:0{digits}d}.pdf" for page, _ in parts]
                zip_name = f"{base}.zip"
            else:
                parts = parse_page_ranges(ranges, page_count)
                if len(parts) > SPLIT_MAX_RANGES:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Puedes indicar como máximo {SPLIT_MAX_RANGES} rangos.",
                    )
                check_page_list_total(sum(end - start + 1 for start, end in parts), page_count)
                defaults = [f"split-{start}-{end}" for start, end in parts]
                given_names = parse_part_names(names, len(parts))
                part_names = unique_pdf_names(
                    [name or default for name, default in zip(given_names, defaults)], defaults
                )
                zip_name = f"{Path(file.filename).stem}-split.zip"

            def build_part(start: int, end: int) -> Path:
                # Each part goes through a work file, so a big range is never held
                # in memory as a whole.
                writer = PdfWriter()
                try:
                    for page_index in range(start - 1, end):
                        writer.add_page(reader.pages[page_index])
                    return write_pdf_to_file(writer)
                finally:
                    writer.close()

            if len(parts) == 1:
                return file_download(build_part(*parts[0]), part_names[0])

            # The ZIP goes to the documents folder, so it is removed once sent and,
            # if the download is interrupted, by the regular sweep. PDF content is
            # already compressed, so the parts are stored as they are: compressing
            # them again costs a lot of time and saves almost nothing.
            (zip_path,) = new_job_paths(".zip")
            try:
                with protect_from_sweeper(zip_path), zipfile.ZipFile(
                    zip_path, "w", compression=zipfile.ZIP_STORED
                ) as archive:
                    for (start, end), part_name in zip(parts, part_names):
                        part_path = build_part(start, end)
                        try:
                            archive.write(part_path, part_name)
                        finally:
                            part_path.unlink(missing_ok=True)
            except BaseException:
                zip_path.unlink(missing_ok=True)
                raise
    except HTTPException:
        raise
    except Exception as error:
        raise processing_error("No se pudo dividir el PDF.", error) from error
    finally:
        file.file.close()

    return file_download(zip_path, clean_base_name(zip_name, "split-pdf.zip"), "application/zip")


def build_edited_pdf(reader: PdfReader, pages: str) -> bytes:
    # In-memory variant for small results; the endpoints use
    # build_edited_writer + write_pdf_to_file so big PDFs stay on disk.
    writer = build_edited_writer(reader, pages)
    output = BytesIO()
    writer.write(output)
    writer.close()
    return output.getvalue()


def build_edited_writer(reader: PdfReader, pages: str) -> PdfWriter:
    invalid = HTTPException(status_code=400, detail="La lista de páginas no es válida.")
    items = parse_user_json(pages, invalid.detail)
    if not isinstance(items, list) or not items:
        raise invalid
    # Checked before building anything: each entry adds a page to the result.
    page_count = len(reader.pages)
    check_page_list_total(len(items), page_count)
    layout = []
    for item in items:
        if not isinstance(item, dict):
            raise invalid
        page_number, rotation = item.get("page"), item.get("rotation", 0)
        # Whole numbers only: 1.7 (or true) is not a page, and int() would
        # silently turn it into page 1.
        if any(isinstance(value, bool) or not isinstance(value, int) for value in (page_number, rotation)):
            raise invalid
        layout.append((page_number, rotation))

    writer = PdfWriter()
    for page_number, rotation in layout:
        if page_number < 1 or page_number > page_count:
            raise HTTPException(
                status_code=400,
                detail=f"Las páginas deben estar entre 1 y {page_count}.",
            )
        if rotation % 90 != 0:
            raise HTTPException(
                status_code=400,
                detail="La rotación debe ser múltiplo de 90 grados.",
            )
        page = writer.add_page(reader.pages[page_number - 1])
        # page.rotate() adds to the current /Rotate (270 + 90 = 360), so the
        # total is written modulo 360 instead.
        total = (page.rotation + rotation) % 360
        if total != page.get("/Rotate", 0):
            page.rotation = total
    return writer


@app.post("/api/documents")
def upload_document(file: UploadFile = File(...)):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="El archivo debe ser un PDF.")
    check_upload_sizes([file])

    remove_expired_documents()
    document_id = uuid.uuid4().hex
    path = config.DOCUMENTS_DIR / f"{document_id}.pdf"
    try:
        with path.open("wb") as destination:
            shutil.copyfileobj(file.file, destination)
        with PDFIUM_LOCK:
            document = open_pdfium_checked(path)
            page_count = len(document)
            document.close()
    except HTTPException:
        path.unlink(missing_ok=True)
        raise
    except Exception as error:
        path.unlink(missing_ok=True)
        raise processing_error("No se pudo leer el PDF.", error) from error
    finally:
        file.file.close()

    return {"id": document_id, "pages": page_count}


# Previews are sized by width, so a very tall page (500 x 200000 pt) would
# otherwise render as a bitmap of hundreds of megapixels. The area and each
# side are capped; such a page just gets a narrower preview.
THUMBNAIL_MAX_PIXELS = 8_000_000
THUMBNAIL_MAX_SIDE = 16_000
THUMBNAIL_MAX_SCALE = 4


def thumbnail_scale(page_width: float, page_height: float, width: int) -> float:
    """Scale for a preview `width` pixels wide, within the pixel caps."""
    if not (math.isfinite(page_width) and math.isfinite(page_height)) or page_width <= 0 or page_height <= 0:
        raise HTTPException(status_code=400, detail="El PDF tiene páginas con un tamaño no válido.")
    scale = min(width / page_width, THUMBNAIL_MAX_SCALE)
    pixels = page_width * page_height * scale * scale
    if pixels > THUMBNAIL_MAX_PIXELS:
        scale *= (THUMBNAIL_MAX_PIXELS / pixels) ** 0.5
    longest = max(page_width, page_height) * scale
    if longest > THUMBNAIL_MAX_SIDE:
        scale *= THUMBNAIL_MAX_SIDE / longest
    return scale


@app.get("/api/documents/{document_id}/pages/{page}")
def document_page_image(document_id: str, page: int, width: int = THUMBNAIL_WIDTH):
    path = get_document_path(document_id)
    width = max(MIN_RENDER_WIDTH, min(width, MAX_RENDER_WIDTH))

    # pdfium is not thread-safe, so renders run one at a time.
    with PDFIUM_LOCK:
        document = open_pdfium_document(path)
        try:
            if page < 1 or page > len(document):
                raise HTTPException(
                    status_code=404,
                    detail=f"La página debe estar entre 1 y {len(document)}.",
                )
            pdf_page = document[page - 1]
            bitmap = pdf_page.render(
                scale=thumbnail_scale(pdf_page.get_width(), pdf_page.get_height(), width)
            )
            image = bitmap.to_pil()
            output = BytesIO()
            image.convert("RGB").save(output, format="JPEG", quality=80)
            image.close()
            bitmap.close()
            pdf_page.close()
        finally:
            document.close()

    return Response(
        content=output.getvalue(),
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=3600"},
    )


@app.post("/api/documents/{document_id}/edit")
def edit_document(
    document_id: str,
    pages: str = Form(...),
    output_name: str = Form("edited.pdf"),
):
    path = get_document_path(document_id)
    try:
        # An open file, not the path: given a path, pypdf reads the whole
        # file into memory first.
        with protect_from_sweeper(path), heavy_jobs.heavy_job(job_estimate_mb("edit", path.stat().st_size)), \
                path.open("rb") as source:
            writer = build_edited_writer(open_pdf_reader(source), pages)
            try:
                output_path = write_pdf_to_file(writer)
            finally:
                writer.close()
    except FileNotFoundError:
        raise document_gone() from None
    except HTTPException:
        raise
    except Exception as error:
        raise processing_error("No se pudo editar el PDF.", error) from error

    # The result is about to be downloaded, so the uploaded copy is no longer
    # needed. If the user keeps editing, the page uploads the file again.
    path.unlink(missing_ok=True)

    return file_download(output_path, clean_output_name(output_name))


@app.delete("/api/documents/{document_id}", status_code=204)
def delete_document(document_id: str):
    if DOCUMENT_ID_PATTERN.fullmatch(document_id):
        (config.DOCUMENTS_DIR / f"{document_id}.pdf").unlink(missing_ok=True)
    return Response(status_code=204)


@app.post("/api/edit-pages")
def edit_pages(
    file: UploadFile = File(...),
    pages: str = Form(...),
    output_name: str = Form("edited.pdf"),
):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="El archivo debe ser un PDF.")
    check_upload_sizes([file])

    try:
        with heavy_jobs.heavy_job(job_estimate_mb("edit", upload_size(file))):
            writer = build_edited_writer(open_pdf_reader(file.file), pages)
            try:
                output_path = write_pdf_to_file(writer)
            finally:
                writer.close()
    except HTTPException:
        raise
    except Exception as error:
        raise processing_error("No se pudo editar el PDF.", error) from error
    finally:
        file.file.close()

    return file_download(output_path, clean_output_name(output_name))


A4_SHORT_INCHES = 210 / 25.4
A4_LONG_INCHES = 297 / 25.4
MIN_IMAGE_DPI = 72


def a4_resolution(width: int, height: int) -> float:
    """Return the dpi at which an image fits an A4 page in its own orientation.

    Small images keep 72 dpi (their natural size) instead of being blown up.
    """
    short_side, long_side = sorted((width, height))
    return max(short_side / A4_SHORT_INCHES, long_side / A4_LONG_INCHES, MIN_IMAGE_DPI)


def image_page(frame: Image.Image) -> Image.Image:
    """An RGB copy of one image frame, ready to become a PDF page.

    Phone photos are turned upright from their EXIF orientation, and
    transparent areas (PNG, GIF, WebP) are painted white instead of the black
    a plain convert("RGB") gives.
    """
    # exif_transpose always copies, so it only runs when there is something to turn.
    orientation = frame.getexif().get(0x0112, 1)
    upright = (ImageOps.exif_transpose(frame) or frame) if orientation not in (None, 1) else frame
    try:
        if upright.mode == "P" and "transparency" in upright.info:
            upright = upright.convert("RGBA")
        if upright.mode in ("RGBA", "LA", "PA", "La", "RGBa") or "transparency" in upright.info:
            rgba = upright.convert("RGBA")
            background = Image.new("RGBA", rgba.size, "white")
            background.alpha_composite(rgba)
            rgba.close()
            converted = background.convert("RGB")
            background.close()
            return converted
        return upright.convert("RGB")
    finally:
        if upright is not frame:
            upright.close()


@app.post("/api/images-to-pdf")
def images_to_pdf(
    images: list[UploadFile] = File(...),
    output_name: str = Form("images.pdf"),
):
    if not images:
        raise HTTPException(status_code=400, detail="Selecciona al menos una imagen.")
    check_upload_sizes(images)

    def invalid_image(image_file: UploadFile) -> HTTPException:
        return HTTPException(
            status_code=400,
            detail=f"El archivo {image_file.filename or 'seleccionado'} no es una imagen válida.",
        )

    writer = PdfWriter()
    try:
        # Sizes come from the headers, before anything is decoded. Images are
        # decoded one at a time, so the biggest one sets the memory needed;
        # it counts even for small files, so a pixel bomb waits its turn.
        largest = max(
            image_pixels(image_file, ALLOWED_IMAGE_FORMATS, invalid_image(image_file))
            for image_file in images
        )
        estimate = job_estimate_mb(
            "images-to-pdf", *map(upload_size, images), minimum_mb=decoded_image_mb(largest)
        )
        with heavy_jobs.heavy_job(estimate):
            # Pillow applies one resolution to every page, so each image becomes
            # its own one-page PDF at its own dpi and the pages are merged after.
            # Images are decoded one at a time: a decoded photo takes many times
            # the memory of its file, so holding them all could exhaust memory.
            for image_file in images:
                try:
                    with Image.open(image_file.file, formats=ALLOWED_IMAGE_FORMATS) as image:
                        # A multi-page TIFF gives one page per frame; animated
                        # GIF/WebP only their first frame. Frames are decoded
                        # one at a time.
                        frame_count = getattr(image, "n_frames", 1) if image.format == "TIFF" else 1
                        for frame in ImageSequence.Iterator(image):
                            # Pillow checks the size of the first frame only.
                            if frame.width * frame.height > IMAGE_MAX_PIXELS:
                                raise HTTPException(
                                    status_code=400,
                                    detail=IMAGE_TOO_BIG_MESSAGE.format(
                                        name=image_file.filename or "seleccionada",
                                        megapixels=IMAGE_MAX_PIXELS // 1_000_000,
                                    ),
                                )
                            converted = image_page(frame)
                            try:
                                page = BytesIO()
                                converted.save(page, format="PDF", resolution=a4_resolution(*converted.size))
                            finally:
                                converted.close()
                            writer.append(PdfReader(page))
                            frame_count -= 1
                            if frame_count <= 0:
                                break
                except (UnidentifiedImageError, OSError, SyntaxError, ValueError,
                        Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
                    raise invalid_image(image_file) from error
            output_path = write_pdf_to_file(writer)
    except HTTPException:
        raise
    except Exception as error:
        raise processing_error("No se pudieron convertir las imágenes.", error) from error
    finally:
        writer.close()
        for image_file in images:
            image_file.file.close()

    return file_download(output_path, clean_output_name(output_name))


IMAGE_FORMATS = {
    "png": ("PNG", "image/png"),
    "jpg": ("JPEG", "image/jpeg"),
}
IMAGE_DPI_CHOICES = {72, 150, 300}
# A big page at 300 dpi (A0 is about 140 million pixels) would need gigabytes of
# memory, so such pages are rendered at a lower resolution instead.
MAX_IMAGE_PIXELS = 40_000_000

# Estimated bytes per output pixel, used only for the up-front disk check. A
# text page takes well under 0.1 byte per pixel in PNG and a scanned or photo
# page 1-3; 0.5 covers typical mixed documents without rejecting ordinary text
# ones. JPG at quality 90 is several times smaller. The real write still stops
# cleanly if the disk fills anyway.
IMAGE_BYTES_PER_PIXEL = {"png": 0.5, "jpg": 0.2}


def pdf_to_images_timeout(page_count: int) -> float:
    scaled = config.PDF_TO_IMAGES_TIMEOUT_SECONDS + config.PDF_TO_IMAGES_SECONDS_PER_PAGE * page_count
    # The cap never cuts below the base time.
    return min(scaled, max(config.PDF_TO_IMAGES_MAX_TIMEOUT_SECONDS, config.PDF_TO_IMAGES_TIMEOUT_SECONDS))


def render_scale(width: float, height: float, dpi: int) -> float:
    """Scale for rendering a page of width x height points at dpi.

    Pages over MAX_IMAGE_PIXELS are rendered smaller; the estimate and the
    real render both use this function, so they agree.
    """
    scale = dpi / 72
    pixels = width * height * scale * scale
    if pixels > MAX_IMAGE_PIXELS:
        scale *= (MAX_IMAGE_PIXELS / pixels) ** 0.5
    return scale


def format_size_es(size: float) -> str:
    if size >= 1024 ** 3:
        return f"{size / 1024 ** 3:.1f} GB".replace(".", ",")
    return f"{max(1, round(size / 1024 ** 2))} MB"


def format_megapixels_es(pixels: float) -> str:
    megapixels = pixels / 1_000_000
    if megapixels < 10:
        return f"{megapixels:.1f}".replace(".", ",")
    return f"{round(megapixels):,}".replace(",", ".")


def is_disk_full(error: BaseException | None) -> bool:
    # Closing the ZIP after a failed write can raise a second error, so the
    # original one may be further down the context chain.
    while error is not None:
        if isinstance(error, OSError) and error.errno == errno.ENOSPC:
            return True
        error = error.__context__
    return False


def check_pdf_to_images_budget(total_pixels: float, page_total: int, dpi: int, image_format: str):
    """Reject a conversion that is too big, before rendering anything."""
    if not math.isfinite(total_pixels):
        # A broken page size would make every comparison below false.
        raise HTTPException(status_code=400, detail="El PDF tiene páginas con un tamaño no válido.")
    estimated_bytes = total_pixels * IMAGE_BYTES_PER_PIXEL[image_format]
    request = f"Convertir {'1 página' if page_total == 1 else f'{page_total} páginas'} a {dpi} ppp"
    if total_pixels > config.PDF_TO_IMAGES_MAX_MEGAPIXELS * 1_000_000:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{request} daría unos {format_megapixels_es(total_pixels)} millones de "
                f"píxeles (unos {format_size_es(estimated_bytes)} de imágenes), más del máximo "
                f"de {format_megapixels_es(config.PDF_TO_IMAGES_MAX_MEGAPIXELS * 1_000_000)} millones. "
                "Elige menos páginas o una resolución menor."
            ),
        )
    free = shutil.disk_usage(config.DOCUMENTS_DIR).free
    if estimated_bytes + config.PDF_TO_IMAGES_MIN_FREE_MB * 1024 * 1024 > free:
        raise HTTPException(
            status_code=507,
            detail=(
                f"{request} ocuparía unos {format_size_es(estimated_bytes)} y no hay espacio "
                f"suficiente en el disco temporal del servidor ({format_size_es(free)} libres, "
                f"de los que se reservan {format_size_es(config.PDF_TO_IMAGES_MIN_FREE_MB * 1024 * 1024)}). "
                "Elige menos páginas, una resolución menor o el formato JPG."
            ),
        )


DISK_FULL_IMAGES_MESSAGE = (
    "El disco temporal del servidor se ha llenado al crear las imágenes y se ha cancelado "
    "la conversión. Elige menos páginas, una resolución menor o el formato JPG."
)


@app.post("/api/pdf-to-images")
def pdf_to_images(
    file: UploadFile = File(...),
    image_format: str = Form("png"),
    dpi: int = Form(150),
    base_name: str = Form(""),
    pages: str = Form(""),
):
    """Render the pages of a PDF as PNG or JPG images.

    pages uses the parse_page_ranges syntax ("1-3, 5"); empty means all pages.
    The images are named <base_name>-page-NNN.<ext> with the real page number
    and returned as a ZIP; a single selected page is returned as a plain image.
    Before rendering, the total pixels and the estimated output size are
    checked (400 over PDF_TO_IMAGES_MAX_MEGAPIXELS, 507 if it would not fit in
    the temporary disk); a job over pdf_to_images_timeout answers 504, and a
    disk that fills while writing answers 507.
    """
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="El archivo debe ser un PDF.")
    if image_format not in IMAGE_FORMATS:
        raise HTTPException(status_code=400, detail="El formato debe ser png o jpg.")
    if dpi not in IMAGE_DPI_CHOICES:
        raise HTTPException(status_code=400, detail="La resolución debe ser 72, 150 o 300 ppp.")
    check_upload_sizes([file])

    pil_format, media_type = IMAGE_FORMATS[image_format]
    base = clean_base_name(base_name or Path(file.filename).stem, "pdf-images")
    if base.lower().endswith(".pdf"):
        base = base[:-4]

    # The upload and the ZIP live in the documents folder, so the regular sweep
    # removes them if the server stops halfway; normally they are deleted once
    # the response is sent.
    input_path, zip_path = new_job_paths(".in.pdf", ".zip")

    def remove_job_files():
        input_path.unlink(missing_ok=True)
        zip_path.unlink(missing_ok=True)

    def render_page_image(document: pypdfium2.PdfDocument, index: int) -> bytes:
        # pdfium is not thread-safe, so rendering holds the lock, but only for
        # one page at a time: the slow image encoding runs outside it, and the
        # page organizer can still draw thumbnails during a long conversion.
        with PDFIUM_LOCK:
            pdf_page = document[index]
            try:
                scale = render_scale(pdf_page.get_width(), pdf_page.get_height(), dpi)
                bitmap = pdf_page.render(scale=scale)
                image = bitmap.to_pil().convert("RGB")
                bitmap.close()
            finally:
                pdf_page.close()
        output = BytesIO()
        if pil_format == "JPEG":
            image.save(output, format="JPEG", quality=90, dpi=(dpi, dpi))
        else:
            image.save(output, format="PNG", dpi=(dpi, dpi))
        image.close()
        return output.getvalue()

    try:
        with heavy_jobs.heavy_job(job_estimate_mb("pdf-to-images", upload_size(file)), [file]), \
                protect_from_sweeper(input_path, zip_path):
            with input_path.open("wb") as destination:
                shutil.copyfileobj(file.file, destination)

            with PDFIUM_LOCK:
                document = open_pdfium_checked(input_path)
                page_count = len(document)
            try:
                numbers = selected_page_numbers(pages, page_count)
                # Estimate from the page sizes (no page is loaded or rendered).
                with PDFIUM_LOCK:
                    total_pixels = 0.0
                    for number in numbers:
                        width, height = document.get_page_size(number - 1)
                        scale = render_scale(width, height, dpi)
                        total_pixels += width * height * scale * scale
                check_pdf_to_images_budget(total_pixels, len(numbers), dpi, image_format)

                digits = max(3, len(str(page_count)))
                names = [f"{base}-page-{number:0{digits}d}.{image_format}" for number in numbers]
                deadline = time.monotonic() + pdf_to_images_timeout(len(numbers))

                if len(numbers) == 1:
                    single_image = render_page_image(document, numbers[0] - 1)
                else:
                    single_image = None
                    # PNG and JPG are already compressed, so they are stored as is.
                    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_STORED) as archive:
                        for done, (number, name) in enumerate(zip(numbers, names), start=1):
                            archive.writestr(name, render_page_image(document, number - 1))
                            # Rendering runs in-process (no subprocess to
                            # kill), so the time limit is checked between pages.
                            if done < len(numbers) and time.monotonic() > deadline:
                                raise HTTPException(
                                    status_code=504,
                                    detail=(
                                        f"La conversión tardó demasiado y se ha cancelado tras "
                                        f"{done} de {len(numbers)} páginas. Elige menos páginas, "
                                        "una resolución menor o el formato JPG."
                                    ),
                                )
            finally:
                with PDFIUM_LOCK:
                    document.close()
    except HTTPException:
        remove_job_files()
        raise
    except Exception as error:
        remove_job_files()
        if is_disk_full(error):
            logger.warning("Disk full while converting a PDF to images: %s", error)
            raise HTTPException(status_code=507, detail=DISK_FULL_IMAGES_MESSAGE) from error
        raise processing_error("No se pudo convertir el PDF en imágenes.", error) from error
    finally:
        file.file.close()

    if single_image is not None:
        remove_job_files()
        return Response(
            content=single_image,
            media_type=media_type,
            headers={"Content-Disposition": content_disposition(names[0])},
        )

    input_path.unlink(missing_ok=True)
    return file_download(
        zip_path, f"{base}-{image_format}.zip", "application/zip", background=BackgroundTask(remove_job_files)
    )


@app.post("/api/word-to-pdf")
def word_to_pdf(
    file: UploadFile = File(...),
    output_name: str = Form(""),
):
    return convert_office_to_pdf(file, output_name, {".doc", ".docx"}, "DOC o DOCX", "Word")


@app.post("/api/excel-to-pdf")
def excel_to_pdf(
    file: UploadFile = File(...),
    output_name: str = Form(""),
):
    return convert_office_to_pdf(file, output_name, {".xls", ".xlsx"}, "XLS o XLSX", "Excel")


@app.post("/api/powerpoint-to-pdf")
def powerpoint_to_pdf(
    file: UploadFile = File(...),
    output_name: str = Form(""),
):
    return convert_office_to_pdf(file, output_name, {".ppt", ".pptx"}, "PPT o PPTX", "PowerPoint")


# The English defaults the pages used to send; they now mean "name it after
# the uploaded file", like an empty name.
OFFICE_OLD_DEFAULT_NAMES = {"document.pdf", "spreadsheet.pdf", "presentation.pdf"}
# .docx/.xlsx/.pptx are ZIP packages; Office also saves old binary (OLE)
# files with the new extension. The legacy .doc/.xls/.ppt are often RTF,
# HTML or CSV exports, which LibreOffice reads too, so they are not checked.
OFFICE_SIGNATURES = (b"PK\x03\x04", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")
OFFICE_PACKAGE_EXTENSIONS = {".docx", ".xlsx", ".pptx"}


def check_office_content(file: UploadFile, extension: str, kind: str) -> None:
    """Reject empty files, and .docx/.xlsx/.pptx that are neither ZIP nor OLE,
    before LibreOffice is started for nothing."""
    if upload_size(file) == 0:
        file.file.close()
        raise HTTPException(status_code=400, detail="El archivo está vacío.")
    if extension not in OFFICE_PACKAGE_EXTENSIONS:
        return
    position = file.file.tell()
    file.file.seek(0)
    header = file.file.read(8)
    file.file.seek(position)
    if not header.startswith(OFFICE_SIGNATURES):
        file.file.close()
        raise HTTPException(
            status_code=400,
            detail=f"El archivo no parece un documento de {kind} válido.",
        )


def office_output_name(output_name: str, upload_name: str) -> str:
    """The requested name, or the uploaded file's own name with .pdf."""
    name = output_name.strip()
    if not name or name.lower() in OFFICE_OLD_DEFAULT_NAMES:
        name = clean_base_name(Path(upload_name).stem, "documento")
    return clean_output_name(name)

# Settings written into each throwaway LibreOffice profile before converting.
# Without them a document can make the server fetch linked resources: an image
# linked to http://... is downloaded during the conversion (SSRF, tracking)
# and one linked to file:///... is embedded in the PDF. Keys and values come
# from LibreOffice's officecfg schema (Common.xcs, Writer.xcs, Calc.xcs,
# Inet.xcs); the first one alone stops both cases (checked in Docker), the
# rest are defense in depth.
LIBREOFFICE_PROFILE_SETTINGS = [
    # Block every link from a document outside the trusted locations (there
    # are none), which covers linked graphics in ODF and OOXML files.
    ("/org.openoffice.Office.Common/Security/Scripting", "BlockUntrustedRefererLinks", "true"),
    # No embedded active content (OLE objects) and no DDE links.
    ("/org.openoffice.Office.Common/Security/Scripting", "DisableActiveContent", "true"),
    # Never update links on load (Writer: 2 = never; Calc: 1 = never).
    ("/org.openoffice.Office.Writer/Content/Update", "Link", "2"),
    ("/org.openoffice.Office.Calc/Content/Update", "Link", "1"),
    # Anything that still tries HTTP(S) goes to a proxy on the discard port of
    # localhost, where the connection is refused (2 = manual proxy).
    ("/org.openoffice.Inet/Settings", "ooInetProxyType", "2"),
    ("/org.openoffice.Inet/Settings", "ooInetHTTPProxyName", "127.0.0.1"),
    ("/org.openoffice.Inet/Settings", "ooInetHTTPProxyPort", "9"),
    ("/org.openoffice.Inet/Settings", "ooInetHTTPSProxyName", "127.0.0.1"),
    ("/org.openoffice.Inet/Settings", "ooInetHTTPSProxyPort", "9"),
    ("/org.openoffice.Inet/Settings", "ooInetNoProxy", ""),
]


def write_libreoffice_profile(profile_path: Path) -> None:
    """Seed a new LibreOffice user profile with LIBREOFFICE_PROFILE_SETTINGS."""
    items = "".join(
        f'<item oor:path="{path}"><prop oor:name="{name}" oor:op="fuse">'
        f"<value>{value}</value></prop></item>\n"
        for path, name, value in LIBREOFFICE_PROFILE_SETTINGS
    )
    user_path = profile_path / "user"
    user_path.mkdir(parents=True, exist_ok=True)
    (user_path / "registrymodifications.xcu").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<oor:items xmlns:oor="http://openoffice.org/2001/registry" '
        'xmlns:xs="http://www.w3.org/2001/XMLSchema" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">\n'
        f"{items}</oor:items>\n",
        encoding="utf-8",
    )


def convert_office_to_pdf(
    file: UploadFile,
    output_name: str,
    extensions: set[str],
    formats_label: str,
    kind: str,
) -> Response:
    # LibreOffice picks Writer, Calc or Impress from the file extension, so the
    # upload keeps its own extension on disk.
    file_extension = Path(file.filename or "").suffix.lower()
    if file_extension not in extensions:
        file.file.close()
        raise HTTPException(status_code=400, detail=f"El archivo debe ser {formats_label}.")
    check_upload_sizes([file])
    check_office_content(file, file_extension, kind)

    soffice = external_tools.find_soffice()
    if not soffice:
        file.file.close()
        raise HTTPException(
            status_code=503,
            detail="LibreOffice no está instalado o no está disponible en el PATH.",
        )

    try:
        with heavy_jobs.heavy_job(job_estimate_mb("office", upload_size(file))), \
                tempfile.TemporaryDirectory() as temporary_directory:
            temporary_path = Path(temporary_directory)
            input_path = temporary_path / f"source{file_extension}"
            with input_path.open("wb") as destination:
                shutil.copyfileobj(file.file, destination)
            profile_path = temporary_path / "profile"
            write_libreoffice_profile(profile_path)
            result = external_tools.run_tool(
                [
                    soffice,
                    "--headless",
                    f"-env:UserInstallation={profile_path.as_uri()}",
                    "--convert-to",
                    "pdf",
                    "--outdir",
                    str(temporary_path),
                    str(input_path),
                ],
                timeout=config.OFFICE_TIMEOUT_SECONDS,
            )
            output_path = temporary_path / "source.pdf"
            if result.returncode != 0 or not output_path.exists():
                detail = result.stderr.strip() or result.stdout.strip()
                raise RuntimeError(detail or "LibreOffice no pudo convertir el documento.")
            # Moved out before the temporary folder is deleted, and streamed
            # from disk instead of read into memory.
            (result_path,) = new_job_paths(".out.pdf")
            shutil.move(output_path, result_path)
    except subprocess.TimeoutExpired as error:
        raise HTTPException(status_code=504, detail="La conversión tardó demasiado.") from error
    except HTTPException:
        raise
    except Exception as error:
        raise processing_error("No se pudo convertir el documento.", error) from error
    finally:
        file.file.close()

    return file_download(result_path, office_output_name(output_name, file.filename))


# Ghostscript presets: image resolution is what shrinks scanned documents.
COMPRESSION_LEVELS = {
    "strong": "/screen",  # 72 dpi images, smallest file
    "recommended": "/ebook",  # 150 dpi images, good for reading on screen
    "high": "/printer",  # 300 dpi images, good for printing
}


def compression_timeout(page_count: int) -> float:
    scaled = config.COMPRESSION_TIMEOUT_SECONDS + config.COMPRESSION_SECONDS_PER_PAGE * page_count
    # The cap never cuts below the base timeout.
    return min(scaled, max(config.COMPRESSION_MAX_TIMEOUT_SECONDS, config.COMPRESSION_TIMEOUT_SECONDS))


@app.post("/api/compress")
def compress_pdf(
    file: UploadFile = File(...),
    level: str = Form("recommended"),
    output_name: str = Form("compressed.pdf"),
):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="El archivo debe ser un PDF.")
    if level not in COMPRESSION_LEVELS:
        raise HTTPException(status_code=400, detail="El nivel de compresión no es válido.")
    check_upload_sizes([file])

    ghostscript = external_tools.find_ghostscript()
    if not ghostscript:
        file.file.close()
        raise HTTPException(
            status_code=503,
            detail="Ghostscript no está instalado o no está disponible en el PATH.",
        )

    # Both files live in the documents folder, so the regular sweep removes
    # them if the server stops halfway; normally they are deleted once sent.
    input_path, output_path = new_job_paths(".in.pdf", ".out.pdf")

    def remove_job_files():
        input_path.unlink(missing_ok=True)
        output_path.unlink(missing_ok=True)

    try:
        # A big PDF can take longer than DOCUMENT_TTL_SECONDS to compress.
        with heavy_jobs.heavy_job(job_estimate_mb("compress", upload_size(file))), \
                protect_from_sweeper(input_path, output_path):
            with input_path.open("wb") as destination:
                shutil.copyfileobj(file.file, destination)
            strip_before_pdf_header(input_path)
            # Ghostscript "repairs" almost anything without failing, so make sure
            # this really is a readable PDF first.
            with PDFIUM_LOCK:
                document = open_pdfium_checked(input_path)
                page_count = len(document)
                document.close()
            result = external_tools.run_tool(
                [
                    ghostscript,
                    "-dSAFER",
                    "-dBATCH",
                    "-dNOPAUSE",
                    "-dQUIET",
                    "-sDEVICE=pdfwrite",
                    "-dCompatibilityLevel=1.5",
                    f"-dPDFSETTINGS={COMPRESSION_LEVELS[level]}",
                    "-dDetectDuplicateImages=true",
                    # Left to choose, Ghostscript sometimes stores grainy scans with
                    # lossless Flate, which can be bigger than the original JPEG.
                    "-dAutoFilterColorImages=false",
                    "-dColorImageFilter=/DCTEncode",
                    "-dAutoFilterGrayImages=false",
                    "-dGrayImageFilter=/DCTEncode",
                    f"-sOutputFile={output_path}",
                    str(input_path),
                ],
                timeout=compression_timeout(page_count),
            )
        if result.returncode != 0 or not output_path.exists() or output_path.stat().st_size == 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise RuntimeError(detail or "Ghostscript no pudo comprimir el documento.")
    except subprocess.TimeoutExpired as error:
        remove_job_files()
        raise HTTPException(status_code=504, detail="La compresión tardó demasiado.") from error
    except HTTPException:
        remove_job_files()
        raise
    except Exception as error:
        remove_job_files()
        raise processing_error("No se pudo comprimir el PDF.", error) from error
    finally:
        file.file.close()

    original_size = input_path.stat().st_size
    compressed_size = output_path.stat().st_size
    # Already optimized PDFs can come out bigger; then the original is better.
    compressed = compressed_size < original_size
    return file_download(
        output_path if compressed else input_path,
        clean_output_name(output_name),
        headers={
            "X-Original-Size": str(original_size),
            "X-Compressed-Size": str(compressed_size if compressed else original_size),
            "X-Compression-Applied": "true" if compressed else "false",
        },
        background=BackgroundTask(remove_job_files),
    )


# Protect / unlock. Passwords only live in memory for the request: they are
# never logged, stored or echoed back in error messages.
# AES-256 (PDF 2.0, revision 6) passwords are limited to 127 UTF-8 bytes.
MAX_PASSWORD_BYTES = 127


def check_password(password: str) -> None:
    if not password:
        raise HTTPException(status_code=400, detail="Escribe una contraseña.")
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"La contraseña no puede superar {MAX_PASSWORD_BYTES} bytes.",
        )


@app.post("/api/protect")
def protect_pdf(
    file: UploadFile = File(...),
    password: str = Form(""),
    output_name: str = Form("protegido.pdf"),
):
    try:
        if not file.filename or not file.filename.lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail="El archivo debe ser un PDF.")
        check_upload_sizes([file])
        check_password(password)
        try:
            with heavy_jobs.heavy_job(job_estimate_mb("protect", upload_size(file))):
                reader = PdfReader(file.file)
                if reader.is_encrypted:
                    raise HTTPException(
                        status_code=400,
                        detail="Este PDF ya está protegido con contraseña. Desbloquéalo primero.",
                    )
                writer = PdfWriter(clone_from=reader)
                writer.encrypt(user_password=password, owner_password=password, algorithm="AES-256")
                output_path = write_pdf_to_file(writer)
                writer.close()
        except HTTPException:
            raise
        except Exception as error:
            raise processing_error("No se pudo proteger el PDF.", error) from error
    finally:
        file.file.close()

    return file_download(output_path, clean_output_name(output_name))


@app.post("/api/unlock")
def unlock_pdf(
    file: UploadFile = File(...),
    password: str = Form(""),
    output_name: str = Form("desbloqueado.pdf"),
):
    try:
        if not file.filename or not file.filename.lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail="El archivo debe ser un PDF.")
        check_upload_sizes([file])
        # An empty password is allowed here: PDFs that only have an owner
        # password (restrictions, no password to open) decrypt with "".
        if password:
            check_password(password)
        try:
            with heavy_jobs.heavy_job(job_estimate_mb("unlock", upload_size(file))):
                reader = PdfReader(file.file)
                if not reader.is_encrypted:
                    raise HTTPException(
                        status_code=400,
                        detail="Este PDF no está protegido con contraseña.",
                    )
                if not reader.decrypt(password):
                    if not password:
                        raise HTTPException(status_code=400, detail="Escribe la contraseña del PDF.")
                    raise HTTPException(status_code=400, detail="Contraseña incorrecta")
                writer = PdfWriter(clone_from=reader)
                output_path = write_pdf_to_file(writer)
                writer.close()
        except HTTPException:
            raise
        except Exception as error:
            raise processing_error("No se pudo desbloquear el PDF.", error) from error
    finally:
        file.file.close()

    return file_download(output_path, clean_output_name(output_name))


# --- Numerar páginas y marca de agua -----------------------------------------
# The text is drawn with the standard Helvetica font (every PDF viewer has it,
# so nothing is embedded) in WinAnsi encoding, which covers Spanish accents.
# Each page gets an overlay in its own coordinates, turned to match /Rotate so
# the text always reads upright, and merged on top with pypdf.
STAMP_POSITIONS = {
    "bottom-center", "bottom-left", "bottom-right",
    "top-center", "top-left", "top-right",
}
STAMP_NUMBER_FORMATS = {
    "n": "{n}",
    "n-of-total": "{n} / {total}",
    "page-n-of-total": "Página {n} de {total}",
}
STAMP_NUMBER_SIZE_RANGE = (6, 72)
STAMP_WATERMARK_SIZE_RANGE = (8, 200)
STAMP_MAX_START_NUMBER = 100_000
STAMP_WATERMARK_MAX_LENGTH = 100
STAMP_ROTATIONS = {0, 45}
STAMP_MARGIN = 28  # points from the page edge for page numbers
# Helvetica's cap height and descender (from the AFM), in em: the text must
# fit from the descender of "g/p" up to the top of the capitals.
HELVETICA_CAP_HEIGHT = 0.718
HELVETICA_DESCENDER = 0.207
# Helvetica advance widths (1/1000 em) for ASCII 32-126, from the standard AFM.
HELVETICA_ASCII_WIDTHS = [
    278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278,
    556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 278, 278, 584, 584, 584, 556,
    1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778,
    667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556,
    333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556,
    556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584,
]
HELVETICA_EXTRA_WIDTHS = {
    "Æ": 1000, "æ": 889, "Ø": 778, "ø": 611, "ß": 611, "Ð": 722, "ð": 556,
    "Þ": 667, "þ": 556, "Œ": 1000, "œ": 944, "¿": 611, "¡": 333, "€": 556,
    "«": 556, "»": 556, "º": 365, "ª": 370, "°": 400, "©": 737, "®": 737,
    "™": 1000, "…": 1000, "–": 556, "—": 1000, "·": 278, " ": 278,
}


def helvetica_width(text: str, size: float) -> float:
    total = 0
    for char in text:
        code = ord(char)
        if 32 <= code <= 126:
            total += HELVETICA_ASCII_WIDTHS[code - 32]
        elif char in HELVETICA_EXTRA_WIDTHS:
            total += HELVETICA_EXTRA_WIDTHS[char]
        else:
            # Accented letters are as wide as their base letter.
            base = ord(unicodedata.normalize("NFD", char)[0])
            total += HELVETICA_ASCII_WIDTHS[base - 32] if 32 <= base <= 126 else 556
    return total * size / 1000


def stamp_text_operator(text: str) -> str:
    # A hex string needs no escaping of parentheses or backslashes.
    return f"<{text.encode('cp1252').hex()}> Tj"


def parse_stamp_number(value: str, label: str, minimum: float, maximum: float, integer: bool = True) -> float:
    try:
        number = int(value) if integer else float(value.replace(",", "."))
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail=f"{label} no es un número válido.") from None
    if not minimum <= number <= maximum:
        raise HTTPException(
            status_code=400,
            detail=f"{label} debe estar entre {pdf_number(minimum)} y {pdf_number(maximum)}.",
        )
    return number


def number_stream(text: str, size: float, position: str, width: float, height: float) -> str:
    margin = min(STAMP_MARGIN, min(width, height) * 0.08)
    # Shrink the label if it would run off the page (long labels, big font,
    # narrow page): it must fit between the margins, across and up.
    size = min(size, (height - 2 * margin) / (HELVETICA_CAP_HEIGHT + HELVETICA_DESCENDER))
    text_width = helvetica_width(text, size)
    room = width - 2 * margin
    if text_width > room:
        size *= room / text_width
        text_width = room
    vertical, horizontal = position.split("-")
    if horizontal == "left":
        x = margin
    elif horizontal == "right":
        x = width - margin - text_width
    else:
        x = (width - text_width) / 2
    # The baseline sits above the descenders at the bottom and below the cap
    # height at the top, so neither is clipped on very short pages.
    if vertical == "bottom":
        y = margin + size * HELVETICA_DESCENDER
    else:
        y = height - margin - size * HELVETICA_CAP_HEIGHT
    return (
        f"BT /StampFont {pdf_number(size)} Tf 0 g "
        f"{pdf_number(x)} {pdf_number(y)} Td {stamp_text_operator(text)} ET"
    )


def watermark_stream(text: str, size: float, rotation: int, width: float, height: float) -> str:
    # Shrink text whose box (turned by the rotation) would not fit in 90 % of
    # the page, across and up: long text on narrow pages, big letters on
    # short (landscape strip) pages.
    angle = math.radians(rotation)
    cos, sin = math.cos(angle), math.sin(angle)
    em_width = helvetica_width(text, 1)
    em_height = HELVETICA_CAP_HEIGHT + HELVETICA_DESCENDER
    size = min(
        size,
        0.9 * width / (em_width * cos + em_height * sin),
        0.9 * height / (em_width * sin + em_height * cos),
    )
    text_width = em_width * size
    return (
        "/StampAlpha gs 0.5 g "
        f"1 0 0 1 {pdf_number(width / 2)} {pdf_number(height / 2)} cm "
        f"{pdf_number(cos)} {pdf_number(sin)} {pdf_number(-sin)} {pdf_number(cos)} 0 0 cm "
        f"BT /StampFont {pdf_number(size)} Tf "
        f"{pdf_number(-text_width / 2)} {pdf_number(-size * (HELVETICA_CAP_HEIGHT - HELVETICA_DESCENDER) / 2)} Td {stamp_text_operator(text)} ET"
    )


def stamp_overlay(page: PageObject, drawing: str, opacity: float = 1.0) -> PageObject:
    matrix, _, _ = visible_page_box(page)
    content = DecodedStreamObject()
    content.set_data(f"q {' '.join(matrix)} cm {drawing} Q".encode("ascii"))
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
        NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
    })
    alpha = DictionaryObject({
        NameObject("/Type"): NameObject("/ExtGState"),
        NameObject("/CA"): FloatObject(opacity),
        NameObject("/ca"): FloatObject(opacity),
    })
    overlay = PageObject.create_blank_page(
        width=float(page.mediabox.width), height=float(page.mediabox.height)
    )
    overlay[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/StampFont"): font}),
        NameObject("/ExtGState"): DictionaryObject({NameObject("/StampAlpha"): alpha}),
    })
    overlay[NameObject("/Contents")] = content
    return overlay


@app.post("/api/stamp")
def stamp_pdf(
    file: UploadFile = File(...),
    mode: str = Form("numbers"),
    position: str = Form("bottom-center"),
    number_format: str = Form("n"),
    start_number: str = Form("1"),
    font_size: str = Form(""),
    text: str = Form(""),
    opacity: str = Form("0.3"),
    rotation: str = Form("45"),
    output_name: str = Form(""),
):
    """Add page numbers (mode "numbers") or a text watermark (mode "watermark")
    to every page of a PDF. Numeric options arrive as text so that bad values
    get a 400 with a Spanish message."""
    try:
        if not file.filename or not file.filename.lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail="El archivo debe ser un PDF.")
        check_upload_sizes([file])
        if mode not in {"numbers", "watermark"}:
            raise HTTPException(status_code=400, detail="El modo debe ser numbers o watermark.")

        if mode == "numbers":
            if position not in STAMP_POSITIONS:
                raise HTTPException(status_code=400, detail="La posición del número no es válida.")
            if number_format not in STAMP_NUMBER_FORMATS:
                raise HTTPException(status_code=400, detail="El formato del número no es válido.")
            first = int(parse_stamp_number(start_number, "El número inicial", 0, STAMP_MAX_START_NUMBER))
            size = parse_stamp_number(font_size or "12", "El tamaño de letra", *STAMP_NUMBER_SIZE_RANGE)
            suffix = "numerado"
        else:
            text = " ".join(text.split())
            if not text:
                raise HTTPException(status_code=400, detail="Escribe el texto de la marca de agua.")
            if len(text) > STAMP_WATERMARK_MAX_LENGTH:
                raise HTTPException(
                    status_code=400,
                    detail=f"El texto puede tener como máximo {STAMP_WATERMARK_MAX_LENGTH} caracteres.",
                )
            try:
                text.encode("cp1252")
            except UnicodeEncodeError:
                raise HTTPException(
                    status_code=400,
                    detail="El texto contiene caracteres no admitidos (emojis u otros alfabetos).",
                ) from None
            size = parse_stamp_number(font_size or "60", "El tamaño de letra", *STAMP_WATERMARK_SIZE_RANGE)
            alpha = parse_stamp_number(opacity, "La opacidad", 0.1, 1, integer=False)
            # "45.0" is 45 too; anything else gets the same clear message.
            try:
                angle = float(rotation.replace(",", "."))
            except ValueError:
                angle = None
            if angle not in STAMP_ROTATIONS:
                raise HTTPException(status_code=400, detail="La rotación debe ser 0 o 45 grados.")
            angle = int(angle)
            suffix = "marca-de-agua"

        try:
            with heavy_jobs.heavy_job(job_estimate_mb("stamp", upload_size(file))):
                writer = PdfWriter(clone_from=open_pdf_reader(file.file))
                last = first + len(writer.pages) - 1 if mode == "numbers" else 0
                for index, page in enumerate(writer.pages):
                    _, width, height = visible_page_box(page)
                    if mode == "numbers":
                        label = STAMP_NUMBER_FORMATS[number_format].format(n=first + index, total=last)
                        overlay = stamp_overlay(page, number_stream(label, size, position, width, height))
                    else:
                        overlay = stamp_overlay(page, watermark_stream(text, size, angle, width, height), alpha)
                    page.merge_page(overlay)
                output_path = write_pdf_to_file(writer)
                writer.close()
        except HTTPException:
            raise
        except Exception as error:
            raise processing_error("No se pudo modificar el PDF.", error) from error
    finally:
        file.file.close()

    default_name = f"{Path(file.filename).stem}-{suffix}.pdf"
    return file_download(output_path, clean_output_name(output_name or default_name))


# /Info entries kept on an edit besides the editable fields. Anything else
# (custom keys written by other programs) is removed, since it can carry
# private data the form does not show.
METADATA_KEPT_KEYS = {"/Creator", "/Producer", "/CreationDate", "/ModDate", "/Trapped"}
# Editable document properties: form field -> /Info key.
METADATA_FIELDS = {
    "title": "/Title",
    "author": "/Author",
    "subject": "/Subject",
    "keywords": "/Keywords",
}
# Spanish labels for error messages (the form shows the same words).
METADATA_LABELS = {"title": "Título", "author": "Autor", "subject": "Asunto", "keywords": "Palabras clave"}
MAX_METADATA_LENGTH = 500
PDF_DATE_PATTERN = re.compile(
    r"D:(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?(?:(Z)|([+-])(\d{2})'?(\d{2})?'?)?"
)


def pdf_date_to_xmp(value) -> str | None:
    """D:YYYYMMDDHHmmSS+HH'mm' (PDF) -> ISO 8601 (XMP); None if unreadable."""
    match = PDF_DATE_PATTERN.match(str(value))
    if not match:
        return None
    year, month, day, hour, minute, second, utc, sign, tz_hour, tz_minute = match.groups()
    text = f"{year}-{month or '01'}-{day or '01'}T{hour or '00'}:{minute or '00'}:{second or '00'}"
    if utc:
        return text + "Z"
    if sign:
        return f"{text}{sign}{tz_hour}:{tz_minute or '00'}"
    return text


def build_xmp(info) -> bytes:
    """An XMP packet with the same properties as the /Info dictionary.

    Many viewers (Adobe, the Windows property handler) show XMP rather than
    /Info, so after an edit both must say the same thing.
    """

    def text(key):
        value = info.get(key)
        return xml_escape(str(value)) if value is not None and str(value).strip() else None

    def alt(value):
        return f'<rdf:Alt><rdf:li xml:lang="x-default">{value}</rdf:li></rdf:Alt>'

    parts = []
    if text("/Title"):
        parts.append(f"<dc:title>{alt(text('/Title'))}</dc:title>")
    if text("/Author"):
        parts.append(f"<dc:creator><rdf:Seq><rdf:li>{text('/Author')}</rdf:li></rdf:Seq></dc:creator>")
    if text("/Subject"):
        parts.append(f"<dc:description>{alt(text('/Subject'))}</dc:description>")
    if text("/Keywords"):
        parts.append(f"<pdf:Keywords>{text('/Keywords')}</pdf:Keywords>")
    if text("/Producer"):
        parts.append(f"<pdf:Producer>{text('/Producer')}</pdf:Producer>")
    if text("/Creator"):
        parts.append(f"<xmp:CreatorTool>{text('/Creator')}</xmp:CreatorTool>")
    for key, tag in (("/CreationDate", "xmp:CreateDate"), ("/ModDate", "xmp:ModifyDate")):
        date = pdf_date_to_xmp(info[key]) if key in info else None
        if date:
            parts.append(f"<{tag}>{date}</{tag}>")
    return (
        '<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:pdf="http://ns.adobe.com/pdf/1.3/" xmlns:xmp="http://ns.adobe.com/xap/1.0/">'
        + "".join(parts)
        + '</rdf:Description></rdf:RDF></x:xmpmeta><?xpacket end="w"?>'
    ).encode("utf-8")


def open_metadata_reader(file: UploadFile) -> PdfReader:
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="El archivo debe ser un PDF.")
    check_upload_sizes([file])
    try:
        reader = open_pdf_reader(file.file)
        len(reader.pages)
    except HTTPException:
        raise
    except Exception as error:
        raise processing_error("No se pudo leer el PDF.", error) from error
    return reader


def metadata_text(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def metadata_date(info, key: str) -> str | None:
    if info.get(key) is None:
        return None
    try:
        date = info.creation_date if key == "/CreationDate" else info.modification_date
        return date.isoformat()
    except Exception:
        return metadata_text(info.get(key))


@app.post("/api/metadata/read")
def read_metadata(file: UploadFile = File(...)):
    try:
        reader = open_metadata_reader(file)
        try:
            info = reader.metadata
        except Exception:
            info = None
        result = {field: None for field in (*METADATA_FIELDS, "creator", "producer")}
        result.update(creation_date=None, modification_date=None)
        if info:
            for field, key in METADATA_FIELDS.items():
                result[field] = metadata_text(info.get(key))
            result["creator"] = metadata_text(info.get("/Creator"))
            result["producer"] = metadata_text(info.get("/Producer"))
            result["creation_date"] = metadata_date(info, "/CreationDate")
            result["modification_date"] = metadata_date(info, "/ModDate")
        result["pages"] = len(reader.pages)
        return result
    finally:
        file.file.close()


@app.post("/api/metadata")
def edit_metadata(
    file: UploadFile = File(...),
    title: str = Form(""),
    author: str = Form(""),
    subject: str = Form(""),
    keywords: str = Form(""),
    remove_all: bool = Form(False),
    output_name: str = Form(""),
):
    # The four fields replace the current values: an empty or missing field is
    # removed. Creator, producer and dates are kept unless remove_all is set.
    values = {"title": title, "author": author, "subject": subject, "keywords": keywords}
    writer = None
    try:
        for field, value in values.items():
            if len(value.strip()) > MAX_METADATA_LENGTH:
                raise HTTPException(
                    status_code=400,
                    detail=f"El campo «{METADATA_LABELS[field]}» no puede superar {MAX_METADATA_LENGTH} caracteres.",
                )
        reader = open_metadata_reader(file)
        with heavy_jobs.heavy_job(job_estimate_mb("metadata", upload_size(file))):
            writer = PdfWriter(clone_from=reader)
            # The old XMP stream repeats the old properties and many viewers
            # prefer it, so it is always dropped; an edit writes a fresh one below.
            if "/Metadata" in writer.root_object:
                del writer.root_object["/Metadata"]
            if remove_all:
                writer.metadata = None
                # Pages (and so their XMP) can carry metadata streams too.
                for page in writer.pages:
                    if "/Metadata" in page:
                        del page[NameObject("/Metadata")]
            else:
                if writer._info is None:
                    writer.metadata = {}
                info = writer._info.get_object()
                editable = set(METADATA_FIELDS.values())
                for key in list(info.keys()):
                    if key not in editable and key not in METADATA_KEPT_KEYS:
                        del info[key]
                for field, value in values.items():
                    key = METADATA_FIELDS[field]
                    if value.strip():
                        info[NameObject(key)] = TextStringObject(value.strip())
                    elif key in info:
                        del info[key]
                xmp = DecodedStreamObject()
                xmp.set_data(build_xmp(info))
                xmp.update({NameObject("/Type"): NameObject("/Metadata"), NameObject("/Subtype"): NameObject("/XML")})
                writer.root_object[NameObject("/Metadata")] = writer._add_object(xmp)
            # The clone copied every object, including the old /Info and XMP
            # stream; drop whatever is no longer referenced so they are not written.
            writer.compress_identical_objects(remove_duplicates=False, remove_unreferenced=True)
            output_path = write_pdf_to_file(writer)
    except HTTPException:
        raise
    except Exception as error:
        raise processing_error("No se pudieron editar los metadatos.", error) from error
    finally:
        if writer is not None:
            writer.close()
        file.file.close()

    if output_name.strip():
        filename = clean_output_name(output_name)
    else:
        filename = clean_output_name(clean_base_name(Path(file.filename).stem, "documento") + "-metadatos")
    return file_download(output_path, filename)


# Extract text (PDF to TXT). The text is streamed page by page into a file in
# the documents folder, so a big document never becomes one huge string.
TEXT_SEPARATORS = {"none", "formfeed", "header"}
EMPTY_TEXT_MESSAGE = (
    "No se encontró texto en el PDF. Probablemente es un documento escaneado: "
    "pásalo primero por la herramienta OCR (/ocr) y vuelve a intentarlo."
)


def clean_text_output_name(output_name: str, default: str) -> str:
    """Like clean_output_name, but for a .txt file (same sanitisation rules)."""
    name = clean_base_name(output_name, default)
    if name.lower().endswith((".txt", ".pdf")):
        name = name[:-4].strip(" .") or default
    return f"{name}.txt"


def normalize_text(text: str) -> str:
    # pdfium ends lines with \r\n; NULs can appear in broken text layers.
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")


def pdfium_page_text(document: pypdfium2.PdfDocument, index: int) -> str:
    page = document[index]
    try:
        textpage = page.get_textpage()
        try:
            return normalize_text(textpage.get_text_range())
        finally:
            textpage.close()
    finally:
        page.close()


@app.post("/api/extract-text")
def extract_text(
    file: UploadFile = File(...),
    pages: str = Form(""),
    page_separator: str = Form("header"),
    output_name: str = Form(""),
):
    """Extract the text layer of a PDF as a UTF-8 .txt file.

    pages uses the parse_page_ranges syntax ("1-3, 5"); empty means all pages.
    page_separator: none, formfeed (a form feed between pages) or header
    (a "--- Página N ---" line before each page).
    """
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="El archivo debe ser un PDF.")
    if page_separator not in TEXT_SEPARATORS:
        raise HTTPException(status_code=400, detail="El separador de páginas no es válido.")
    check_upload_sizes([file])

    # Both files live in the documents folder, so the regular sweep removes
    # them if the server stops halfway; normally they are deleted once sent.
    config.DOCUMENTS_DIR.mkdir(exist_ok=True)
    job_id = uuid.uuid4().hex
    input_path = config.DOCUMENTS_DIR / f"{job_id}.in.pdf"
    text_path = config.DOCUMENTS_DIR / f"{job_id}.txt"

    def remove_job_files():
        input_path.unlink(missing_ok=True)
        text_path.unlink(missing_ok=True)

    # A long extraction must not lose its files to the periodic sweep.
    with heavy_jobs.heavy_job(job_estimate_mb("extract-text", upload_size(file)), [file]), \
            protect_from_sweeper(input_path, text_path):
        try:
            with input_path.open("wb") as destination:
                shutil.copyfileobj(file.file, destination)

            character_count = 0
            has_text = False
            # pdfium is not thread-safe, so the whole extraction holds the lock.
            with PDFIUM_LOCK:
                document = open_pdfium_checked(input_path)
                try:
                    page_count = len(document)
                    if pages.strip():
                        # Unlike PDF to images, ranges keep their order and a
                        # repeated page is extracted again, but the list is
                        # as long as pdf-to-images allows and the total pages
                        # are capped (checked before expanding anything).
                        check_page_selection_length(pages)
                        ranges = parse_page_ranges(pages, page_count)
                        check_page_list_total(sum(end - start + 1 for start, end in ranges), page_count)
                        numbers = [number for start, end in ranges for number in range(start, end + 1)]
                    else:
                        numbers = list(range(1, page_count + 1))

                    with text_path.open("w", encoding="utf-8", newline="\n") as output:
                        for position, number in enumerate(numbers):
                            text = pdfium_page_text(document, number - 1).rstrip()
                            character_count += len(text)
                            has_text = has_text or bool(text.strip())
                            if position:
                                output.write("\n\f" if page_separator == "formfeed" else "\n\n")
                            if page_separator == "header":
                                output.write(f"--- Página {number} ---\n")
                                if text:
                                    output.write("\n")
                            output.write(text)
                        output.write("\n")
                finally:
                    document.close()

            if not has_text:
                raise HTTPException(status_code=400, detail=EMPTY_TEXT_MESSAGE)
        except HTTPException:
            remove_job_files()
            raise
        except Exception as error:
            remove_job_files()
            raise processing_error("No se pudo extraer el texto del PDF.", error) from error
        finally:
            file.file.close()

    input_path.unlink(missing_ok=True)
    filename = clean_text_output_name(output_name.strip() or Path(file.filename).stem, "texto")
    return file_download(
        text_path,
        filename,
        "text/plain; charset=utf-8",
        headers={
            "X-Page-Count": str(len(numbers)),
            "X-Character-Count": str(character_count),
        },
        background=BackgroundTask(remove_job_files),
    )


# --- Firmar PDF (visual signature) -------------------------------------------
# Places a drawn or uploaded signature image on pages of a document already in
# the organizer's store. It is only a picture on the page, not a cryptographic
# (digital) signature.
SIGNATURE_IMAGE_FORMATS = ["PNG", "JPEG"]
SIGNATURE_MAX_MB = 10
SIGNATURE_MAX_SOURCE_PIXELS = 40_000_000
SIGNATURE_MAX_SIDE = 2000
SIGNATURE_MAX_PLACEMENTS = 200
# Boxes may overshoot the page edge by this much (rounding in the browser).
SIGNATURE_TOLERANCE = 0.001


SIGNATURE_INVALID_MESSAGE = "La firma debe ser una imagen PNG o JPEG."


def check_signature_upload(file: UploadFile) -> int:
    """Check the signature's file size and pixel size (read from its header,
    nothing is decoded yet) and return its pixel count."""
    check_upload_sizes([file])
    if upload_size(file) > SIGNATURE_MAX_MB * 1024 * 1024:
        raise HTTPException(
            status_code=413,
            detail=f"La imagen de la firma puede ocupar como máximo {SIGNATURE_MAX_MB} MB.",
        )
    # formats= keeps Pillow away from EPS (which would run Ghostscript) and
    # every other format a signature does not need.
    invalid = HTTPException(status_code=400, detail=SIGNATURE_INVALID_MESSAGE)
    too_big = HTTPException(status_code=400, detail="La imagen de la firma es demasiado grande.")
    try:
        pixels = image_pixels(file, SIGNATURE_IMAGE_FORMATS, invalid)
    except HTTPException as error:
        if error is invalid:
            raise
        raise too_big from error  # over IMAGE_MAX_PIXELS
    if pixels > SIGNATURE_MAX_SOURCE_PIXELS:
        raise too_big
    return pixels


def load_signature_image(file: UploadFile) -> Image.Image:
    """Open the uploaded PNG/JPEG signature as an RGBA image of bounded size."""
    check_signature_upload(file)
    invalid = HTTPException(status_code=400, detail=SIGNATURE_INVALID_MESSAGE)
    try:
        with Image.open(file.file, formats=SIGNATURE_IMAGE_FORMATS) as image:
            image.verify()
        file.file.seek(0)
        with Image.open(file.file, formats=SIGNATURE_IMAGE_FORMATS) as image:
            signature = ImageOps.exif_transpose(image).convert("RGBA")
    except HTTPException:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError,
            Image.DecompressionBombWarning) as error:
        raise invalid from error
    signature.thumbnail((SIGNATURE_MAX_SIDE, SIGNATURE_MAX_SIDE))
    # A fully transparent or plain white image would "sign" with nothing
    # visible and still use up the uploaded document.
    on_white = Image.alpha_composite(Image.new("RGBA", signature.size, "white"), signature)
    if on_white.convert("L").getextrema()[0] > 245:
        raise HTTPException(status_code=400, detail="La imagen de la firma está vacía.")
    return signature


def parse_signature_placements(value: str) -> list[dict]:
    """Validate the placements JSON: a list of {page, x, y, width, height}
    with the box given as fractions of the page as displayed."""
    invalid = HTTPException(status_code=400, detail="La lista de posiciones de la firma no es válida.")
    outside = HTTPException(status_code=400, detail="La firma debe quedar dentro de la página.")
    try:
        items = json.loads(value)
    except (ValueError, RecursionError) as error:
        # RecursionError: "[[[[...]]]]" nested deeper than the parser allows.
        raise invalid from error
    if not isinstance(items, list) or not items:
        raise HTTPException(status_code=400, detail="Coloca la firma en al menos una página.")
    if len(items) > SIGNATURE_MAX_PLACEMENTS:
        raise HTTPException(
            status_code=400,
            detail=f"Puedes colocar la firma como máximo {SIGNATURE_MAX_PLACEMENTS} veces.",
        )

    placements = []
    for item in items:
        if not isinstance(item, dict):
            raise invalid
        page = item.get("page")
        if isinstance(page, bool) or not isinstance(page, int):
            raise invalid
        box = {}
        for key in ("x", "y", "width", "height"):
            number = item.get(key)
            if isinstance(number, bool) or not isinstance(number, (int, float)):
                raise invalid
            try:
                # float() of a JSON integer with hundreds of digits overflows.
                number = float(number)
            except OverflowError:
                raise invalid from None
            if not math.isfinite(number):
                raise invalid
            box[key] = number
        x, y, width, height = box["x"], box["y"], box["width"], box["height"]
        if (
            width <= 0 or height <= 0
            or x < -SIGNATURE_TOLERANCE or y < -SIGNATURE_TOLERANCE
            or x + width > 1 + SIGNATURE_TOLERANCE or y + height > 1 + SIGNATURE_TOLERANCE
        ):
            raise outside
        # Pull a box that overshoots only by the tolerance back onto the page.
        x, y = max(x, 0.0), max(y, 0.0)
        width, height = min(width, 1.0 - x), min(height, 1.0 - y)
        if width <= 0 or height <= 0:
            raise outside
        placements.append({"page": page, "x": x, "y": y, "width": width, "height": height})
    return placements


def signature_xobject(writer: PdfWriter, image: Image.Image):
    """Add an RGBA image to the writer as an image XObject and return its
    reference. The alpha channel becomes a soft mask, so the transparent parts
    of the signature leave the page visible."""

    def image_stream(data: bytes, color_space: str):
        stream = DecodedStreamObject()
        stream.set_data(data)
        stream = stream.flate_encode()
        stream.update({
            NameObject("/Type"): NameObject("/XObject"),
            NameObject("/Subtype"): NameObject("/Image"),
            NameObject("/Width"): NumberObject(image.width),
            NameObject("/Height"): NumberObject(image.height),
            NameObject("/ColorSpace"): NameObject(color_space),
            NameObject("/BitsPerComponent"): NumberObject(8),
        })
        return stream

    picture = image_stream(image.convert("RGB").tobytes(), "/DeviceRGB")
    alpha = image.getchannel("A")
    if alpha.getextrema() != (255, 255):
        # Streams must be indirect objects (pypdf would otherwise inline them
        # in the dictionary), so both are registered with the writer.
        picture[NameObject("/SMask")] = writer._add_object(image_stream(alpha.tobytes(), "/DeviceGray"))
    return writer._add_object(picture)


def signature_overlay(page: PageObject, xobject, boxes: list[dict]) -> PageObject:
    """Build an overlay page that draws the signature in each box. Boxes are
    fractions of the page as displayed (top-left origin); visible_page_box maps
    them onto the page's own coordinates whatever its /Rotate and cropbox."""
    matrix, width, height = visible_page_box(page)
    drawing = []
    for box in boxes:
        left = box["x"] * width
        bottom = (1 - box["y"] - box["height"]) * height
        drawing.append(
            f"q {pdf_number(box['width'] * width)} 0 0 {pdf_number(box['height'] * height)} "
            f"{pdf_number(left)} {pdf_number(bottom)} cm /Signature Do Q"
        )
    content = DecodedStreamObject()
    content.set_data(f"q {' '.join(matrix)} cm {' '.join(drawing)} Q".encode("ascii"))
    overlay = PageObject.create_blank_page(
        width=float(page.mediabox.width), height=float(page.mediabox.height)
    )
    overlay[NameObject("/Resources")] = DictionaryObject({
        NameObject("/XObject"): DictionaryObject({NameObject("/Signature"): xobject}),
    })
    overlay[NameObject("/Contents")] = content
    return overlay


@app.post("/api/documents/{document_id}/sign")
def sign_document(
    document_id: str,
    signature: UploadFile = File(...),
    placements: str = Form(...),
    output_name: str = Form(""),
):
    try:
        path = get_document_path(document_id)
        boxes = parse_signature_placements(placements)
        # The decoded signature counts towards the job's memory, so the
        # pixel size is checked (and the image decoded) inside heavy_job.
        decoded_mb = decoded_image_mb(check_signature_upload(signature))
        image = None
        try:
            # An open file, not the path: pypdf reads a path fully into memory.
            with protect_from_sweeper(path), \
                    heavy_jobs.heavy_job(job_estimate_mb("sign", path.stat().st_size, minimum_mb=decoded_mb)), \
                    path.open("rb") as source:
                image = load_signature_image(signature)
                output_path = sign_to_file(source, boxes, image)
        except FileNotFoundError:
            raise document_gone() from None
        except HTTPException:
            raise
        except Exception as error:
            raise processing_error("No se pudo firmar el PDF.", error) from error
        finally:
            if image is not None:
                image.close()
    finally:
        signature.file.close()

    # Like the organizer's save: the result is about to be downloaded, so the
    # stored copy goes. If the user keeps working, the page uploads it again.
    path.unlink(missing_ok=True)
    return file_download(output_path, clean_output_name(output_name or "firmado.pdf"))


def sign_to_file(source, boxes: list[dict], image: Image.Image) -> Path:
    reader = open_pdf_reader(source)
    page_count = len(reader.pages)
    by_page: dict[int, list[dict]] = {}
    for box in boxes:
        if box["page"] < 1 or box["page"] > page_count:
            raise HTTPException(
                status_code=400,
                detail=f"Las páginas deben estar entre 1 y {page_count}.",
            )
        by_page.setdefault(box["page"], []).append(box)

    writer = PdfWriter(clone_from=reader)
    try:
        xobject = signature_xobject(writer, image)
        for number, page_boxes in by_page.items():
            page = writer.pages[number - 1]
            page.merge_page(signature_overlay(page, xobject, page_boxes))
        return write_pdf_to_file(writer)
    finally:
        writer.close()


# OCR: make a scanned PDF searchable with ocrmypdf + Tesseract. ocrmypdf runs
# as a subprocess through run_tool, so a timeout kills it and every Tesseract
# and Ghostscript worker it started.
OCR_LANGUAGES = {"spa", "eng", "spa+eng"}
OCR_JOBS = 2  # Tesseract workers per request, so one OCR does not take every core
OCR_EXIT_ENCRYPTED = 8
OCR_EXIT_MISSING_DEPENDENCY = 3
OCR_EXIT_INVALID_CONFIG = 9  # e.g. a Tesseract language pack is missing
OCR_MISSING_MESSAGE = "El OCR no está disponible: falta ocrmypdf o Tesseract en el servidor."


def ocr_timeout(page_count: int) -> float:
    return min(config.OCR_TIMEOUT_BASE_SECONDS + config.OCR_TIMEOUT_PER_PAGE_SECONDS * page_count, config.OCR_TIMEOUT_MAX_SECONDS)


def page_list(numbers: list[int]) -> str:
    """1-based page numbers as ocrmypdf's --pages syntax: [1, 2, 3, 7] -> "1-3,7"."""
    parts = []
    start = previous = None
    for number in numbers:
        if previous is not None and number == previous + 1:
            previous = number
            continue
        if start is not None:
            parts.append(str(start) if start == previous else f"{start}-{previous}")
        start = previous = number
    if start is not None:
        parts.append(str(start) if start == previous else f"{start}-{previous}")
    return ",".join(parts)


def pages_with_images(document: pypdfium2.PdfDocument) -> list[int]:
    """1-based numbers of the pages that draw at least one image (also inside
    form XObjects). Only those can hold scanned text; call under PDFIUM_LOCK."""
    numbers = []
    for index in range(len(document)):
        page = document[index]
        try:
            if next(iter(page.get_objects(filter=[pypdfium2.raw.FPDF_PAGEOBJ_IMAGE])), None) is not None:
                numbers.append(index + 1)
        finally:
            page.close()
    return numbers


def has_form_fields(path: Path) -> bool:
    # ocrmypdf refuses --redo-ocr on PDFs with fillable forms.
    try:
        with path.open("rb") as source:
            form = PdfReader(source).trailer["/Root"].get("/AcroForm")
            return bool(form and form.get_object().get("/Fields"))
    except Exception:
        return False


def ocr_command(
    language: str, input_path: Path, output_path: Path, image_pages: list[int] | None = None
) -> list[str]:
    """The ocrmypdf command line.

    With image_pages, those pages get --redo-ocr: the image parts are OCRed
    even when the page already has some visible text (a stamped page number),
    which is kept as it is, and an old invisible OCR layer is replaced. Pages
    without images (digital text) are left out with --pages, so they are
    copied unchanged and fast. Without image_pages (nothing to OCR, or a
    fillable form, which --redo-ocr rejects) --skip-text copies pages that
    have text. --rotate-pages turns sideways or upside-down scans upright
    (Tesseract's osd data) so they are read correctly.
    """
    if image_pages:
        mode = ["--redo-ocr", "--pages", page_list(image_pages)]
    else:
        mode = ["--skip-text"]
    return [
        sys.executable,
        "-m",
        "ocrmypdf",
        *mode,
        "--rotate-pages",
        "-l",
        language,
        # A plain PDF: PDF/A conversion is slower and changes more.
        "--output-type",
        "pdf",
        "--optimize",
        "0",
        "--jobs",
        str(OCR_JOBS),
        "--quiet",
        str(input_path),
        str(output_path),
    ]


@app.post("/api/ocr")
def ocr_pdf(
    file: UploadFile = File(...),
    language: str = Form("spa"),
    output_name: str = Form(""),
):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        file.file.close()
        raise HTTPException(status_code=400, detail="El archivo debe ser un PDF.")
    if language not in OCR_LANGUAGES:
        file.file.close()
        raise HTTPException(status_code=400, detail="El idioma del OCR no es válido.")
    check_upload_sizes([file])

    if not external_tools.ocr_available():
        file.file.close()
        raise HTTPException(status_code=503, detail=OCR_MISSING_MESSAGE)

    # Same layout as Compress: both files live in the documents folder, so the
    # sweep removes them if the server stops halfway.
    config.DOCUMENTS_DIR.mkdir(exist_ok=True)
    job_id = uuid.uuid4().hex
    input_path = config.DOCUMENTS_DIR / f"{job_id}.in.pdf"
    output_path = config.DOCUMENTS_DIR / f"{job_id}.out.pdf"

    def remove_job_files():
        input_path.unlink(missing_ok=True)
        output_path.unlink(missing_ok=True)

    # OCR can run for hours on a big scan: keep the sweep away from its files.
    with heavy_jobs.heavy_job(job_estimate_mb("ocr", upload_size(file)), [file]), \
            protect_from_sweeper(input_path, output_path):
        try:
            with input_path.open("wb") as destination:
                shutil.copyfileobj(file.file, destination)
            # ocrmypdf hands the file to Ghostscript too (see Compress).
            strip_before_pdf_header(input_path)
            with PDFIUM_LOCK:
                document = open_pdfium_checked(input_path)
                try:
                    page_count = len(document)
                    image_pages = pages_with_images(document) if page_count <= config.OCR_MAX_PAGES else []
                finally:
                    document.close()
            if page_count > config.OCR_MAX_PAGES:
                raise HTTPException(
                    status_code=400,
                    detail=f"El OCR admite como máximo {config.OCR_MAX_PAGES} páginas por documento.",
                )
            if image_pages and has_form_fields(input_path):
                image_pages = []
            # ocrmypdf keeps rasterized pages and intermediate PDFs in a scratch
            # folder under TMPDIR. A timeout kills it before it can clean up, so it
            # gets a private folder that is removed here whatever happens.
            def run_ocr(pages: list[int]) -> subprocess.CompletedProcess:
                with tempfile.TemporaryDirectory(prefix="pdf-toolkit-ocr-") as scratch:
                    return external_tools.run_tool(
                        ocr_command(language, input_path, output_path, pages),
                        timeout=ocr_timeout(page_count),
                        env={**os.environ, "TMPDIR": scratch, "TEMP": scratch, "TMP": scratch},
                    )

            result = run_ocr(image_pages)
            known_exits = (0, OCR_EXIT_ENCRYPTED, OCR_EXIT_MISSING_DEPENDENCY, OCR_EXIT_INVALID_CONFIG)
            if image_pages and result.returncode not in known_exits:
                # ocrmypdf refuses --redo-ocr on some PDFs (tagged or signed,
                # depending on the version): try once more with --skip-text.
                logger.warning(
                    "ocrmypdf --redo-ocr failed (exit %s), retrying with --skip-text: %s",
                    result.returncode, result.stderr.strip(),
                )
                output_path.unlink(missing_ok=True)
                result = run_ocr([])
            if result.returncode == OCR_EXIT_ENCRYPTED:
                raise encrypted_pdf()
            if result.returncode in (OCR_EXIT_MISSING_DEPENDENCY, OCR_EXIT_INVALID_CONFIG):
                logger.warning("ocrmypdf cannot run (exit %s): %s", result.returncode, result.stderr.strip())
                raise HTTPException(status_code=503, detail=OCR_MISSING_MESSAGE)
            if result.returncode != 0 or not output_path.exists() or output_path.stat().st_size == 0:
                detail = result.stderr.strip() or result.stdout.strip()
                raise RuntimeError(f"ocrmypdf exit {result.returncode}: {detail}")
        except subprocess.TimeoutExpired as error:
            remove_job_files()
            raise HTTPException(status_code=504, detail="El OCR tardó demasiado.") from error
        except HTTPException:
            remove_job_files()
            raise
        except Exception as error:
            remove_job_files()
            raise processing_error("No se pudo aplicar el OCR al PDF.", error) from error
        finally:
            file.file.close()

    if output_name.strip():
        filename = clean_output_name(output_name)
    else:
        filename = clean_output_name(clean_base_name(Path(file.filename).stem, "documento") + "-ocr")
    return file_download(output_path, filename, background=BackgroundTask(remove_job_files))
