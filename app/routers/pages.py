"""Page organizer: upload once, thumbnails by id, then save the new layout
(reorder, rotate, delete, duplicate). Also the one-shot /api/edit-pages.
The document store itself is in documents.py.
"""
from io import BytesIO
import math
import shutil
import uuid

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pypdf import PdfReader, PdfWriter

from app import config, heavy_jobs
from app.documents import (
    DOCUMENT_ID_PATTERN,
    document_gone,
    get_document_path,
    open_pdfium_document,
    protect_from_sweeper,
    remove_expired_documents,
)
from app.errors import processing_error
from app.heavy_jobs import job_estimate_mb
from app.pdf_helpers import (
    PDFIUM_LOCK,
    check_page_list_total,
    open_pdf_reader,
    open_pdfium_checked,
    parse_user_json,
)
from app.uploads import check_upload_sizes, clean_output_name, file_download, upload_size, write_pdf_to_file


router = APIRouter()


THUMBNAIL_WIDTH = 300
MIN_RENDER_WIDTH = 100
MAX_RENDER_WIDTH = 2000


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


@router.post("/api/documents")
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


@router.get("/api/documents/{document_id}/pages/{page}")
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


@router.post("/api/documents/{document_id}/edit")
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


@router.delete("/api/documents/{document_id}", status_code=204)
def delete_document(document_id: str):
    if DOCUMENT_ID_PATTERN.fullmatch(document_id):
        (config.DOCUMENTS_DIR / f"{document_id}.pdf").unlink(missing_ok=True)
    return Response(status_code=204)


@router.post("/api/edit-pages")
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
