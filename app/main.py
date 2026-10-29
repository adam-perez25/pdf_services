from contextlib import asynccontextmanager
from io import BytesIO
import json
import logging
import mimetypes
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
import zipfile

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, UnidentifiedImageError
import pypdfium2
from pypdf import PdfReader, PdfWriter
from starlette.background import BackgroundTask


BASE_DIR = Path(__file__).resolve().parent.parent
DOCUMENTS_DIR = Path(tempfile.gettempdir()) / "pdf-toolkit-documents"
DOCUMENTS_DIR.mkdir(exist_ok=True)
DOCUMENT_TTL_SECONDS = 30 * 60
DOCUMENT_SWEEP_SECONDS = 5 * 60
DOCUMENT_ID_PATTERN = re.compile(r"[0-9a-f]{32}")
PDFIUM_LOCK = threading.Lock()
THUMBNAIL_WIDTH = 300
MIN_RENDER_WIDTH = 100
MAX_RENDER_WIDTH = 2000
logger = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(app: FastAPI):
    start_document_sweeper()
    yield


app = FastAPI(title="Unir PDF", lifespan=lifespan)
# The slim image's MIME table has no entry for .woff2, so the bundled fonts
# would otherwise be served as text/plain.
mimetypes.add_type("font/woff2", ".woff2")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


@app.middleware("http")
async def revalidate_static_files(request, call_next):
    # Make browsers check for a newer CSS/JS after the image is rebuilt instead
    # of reusing a stale copy; unchanged files are answered with a cheap 304.
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/", response_class=HTMLResponse)
async def home():
    return render_page("index.html")


@app.get("/merge", response_class=HTMLResponse)
async def merge_page():
    return render_page("merge.html")


@app.get("/split", response_class=HTMLResponse)
async def split_page():
    return render_page("split.html")


@app.get("/images", response_class=HTMLResponse)
async def images_page():
    return render_page("images.html")


@app.get("/word", response_class=HTMLResponse)
async def word_page():
    return render_page("word.html")


@app.get("/excel", response_class=HTMLResponse)
async def excel_page():
    return render_page("excel.html")


@app.get("/powerpoint", response_class=HTMLResponse)
async def powerpoint_page():
    return render_page("powerpoint.html")


@app.get("/pages", response_class=HTMLResponse)
async def pages_page():
    return render_page("pages.html")


@app.get("/compress", response_class=HTMLResponse)
async def compress_page():
    return render_page("compress.html")


@app.get("/pdf-to-images", response_class=HTMLResponse)
async def pdf_to_images_page():
    return render_page("pdf-to-images.html")


@app.get("/health")
def health():
    # Used by the Docker HEALTHCHECK. The service is unhealthy only if it cannot
    # store uploads; a missing LibreOffice or Ghostscript is reported but only
    # disables the Office conversions or Compress PDF.
    try:
        DOCUMENTS_DIR.mkdir(exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=DOCUMENTS_DIR, prefix=".health-"):
            pass
        storage_ok = True
    except OSError:
        storage_ok = False

    return JSONResponse(
        status_code=200 if storage_ok else 503,
        content={
            "status": "ok" if storage_ok else "error",
            "storage": storage_ok,
            "libreoffice": find_soffice() is not None,
            "ghostscript": find_ghostscript() is not None,
        },
    )


def find_soffice() -> str | None:
    return shutil.which("soffice") or shutil.which("soffice.exe")


def find_ghostscript() -> str | None:
    return shutil.which("gs") or shutil.which("gswin64c") or shutil.which("gswin32c")


def render_page(template_name: str) -> HTMLResponse:
    html = (BASE_DIR / "templates" / template_name).read_text(encoding="utf-8")
    return HTMLResponse(html)


def clean_base_name(output_name: str, default: str) -> str:
    name = Path(output_name.strip()).name
    name = re.sub(r"[^A-Za-z0-9._ -]", "_", name).strip(" .")
    if not name:
        name = default
    return name


def clean_output_name(output_name: str) -> str:
    name = clean_base_name(output_name, "merged")
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    return name


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

    writer = PdfWriter()
    try:
        for file in files:
            if not file.filename or not file.filename.lower().endswith(".pdf"):
                raise HTTPException(
                    status_code=400,
                    detail="Todos los archivos deben tener extension PDF.",
                )
            writer.append(file.file)

        output = BytesIO()
        writer.write(output)
        writer.close()
        output.seek(0)
    except HTTPException:
        writer.close()
        raise
    except Exception as error:
        writer.close()
        raise HTTPException(
            status_code=400,
            detail=f"No se pudieron unir los PDFs: {error}",
        ) from error
    finally:
        for file in files:
            file.file.close()

    filename = clean_output_name(output_name)
    return Response(
        content=output.getvalue(),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@app.post("/api/pdf-info")
def pdf_info(file: UploadFile = File(...)):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="El archivo debe ser un PDF.")

    try:
        reader = PdfReader(file.file)
        return {"pages": len(reader.pages)}
    except Exception as error:
        raise HTTPException(
            status_code=400,
            detail=f"No se pudo leer el PDF: {error}",
        ) from error
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

    try:
        reader = PdfReader(file.file)
        page_count = len(reader.pages)
        end_page = end_page or page_count
        if start_page < 1 or end_page < start_page or end_page > page_count:
            raise HTTPException(
                status_code=400,
                detail=f"El rango debe estar entre 1 y {page_count}.",
            )

        writer = PdfWriter()
        for page_index in range(start_page - 1, end_page):
            writer.add_page(reader.pages[page_index])

        output = BytesIO()
        writer.write(output)
        writer.close()
        output.seek(0)
    except Exception as error:
        if isinstance(error, HTTPException):
            raise
        raise HTTPException(
            status_code=400,
            detail=f"No se pudo dividir el PDF: {error}",
        ) from error
    finally:
        file.file.close()

    return Response(
        content=output.getvalue(),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{clean_output_name(output_name)}"',
        },
    )


def parse_page_ranges(value: str, page_count: int) -> list[tuple[int, int]]:
    ranges = []
    for part in filter(None, (part.strip() for part in value.split(","))):
        match = re.fullmatch(r"(\d+)(?:\s*-\s*(\d+))?", part)
        if not match:
            raise HTTPException(status_code=400, detail=f"Rango inválido: {part}")
        start = int(match.group(1))
        end = int(match.group(2) or start)
        if start < 1 or end < start or end > page_count:
            raise HTTPException(
                status_code=400,
                detail=f"Los rangos deben estar entre 1 y {page_count}.",
            )
        ranges.append((start, end))
    if not ranges:
        raise HTTPException(status_code=400, detail="Escribe al menos un rango de páginas.")
    return ranges


def unique_pdf_names(names: list[str]) -> list[str]:
    # Two parts with the same name would overwrite each other inside the ZIP.
    seen: dict[str, int] = {}
    unique = []
    for name in names:
        stem = clean_output_name(name)[:-4]
        count = seen.get(stem.lower(), 0) + 1
        seen[stem.lower()] = count
        unique.append(f"{stem}.pdf" if count == 1 else f"{stem}-{count}.pdf")
    return unique


@app.post("/api/split-zip")
def split_pdf_zip(
    file: UploadFile = File(...),
    mode: str = Form("pages"),
    ranges: str = Form(""),
    names: str = Form("[]"),
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

    try:
        reader = PdfReader(file.file)
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
            try:
                given_names = [str(name) for name in json.loads(names)]
            except (ValueError, TypeError) as error:
                raise HTTPException(status_code=400, detail="Los nombres no son válidos.") from error
            given_names += [f"split-{start}-{end}" for start, end in parts[len(given_names):]]
            part_names = unique_pdf_names(given_names[:len(parts)])
            zip_name = f"{Path(file.filename).stem}-split.zip"

        def build_part(start: int, end: int) -> bytes:
            writer = PdfWriter()
            for page_index in range(start - 1, end):
                writer.add_page(reader.pages[page_index])
            output = BytesIO()
            writer.write(output)
            writer.close()
            return output.getvalue()

        if len(parts) == 1:
            return Response(
                content=build_part(*parts[0]),
                media_type="application/pdf",
                headers={"Content-Disposition": f'attachment; filename="{part_names[0]}"'},
            )

        # The ZIP goes to the documents folder, so it is removed once sent and,
        # if the download is interrupted, by the regular sweep. PDF content is
        # already compressed, so the parts are stored as they are: compressing
        # them again costs a lot of time and saves almost nothing.
        DOCUMENTS_DIR.mkdir(exist_ok=True)
        zip_path = DOCUMENTS_DIR / f"{uuid.uuid4().hex}.zip"
        try:
            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_STORED) as archive:
                for (start, end), part_name in zip(parts, part_names):
                    archive.writestr(part_name, build_part(start, end))
        except BaseException:
            zip_path.unlink(missing_ok=True)
            raise
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(
            status_code=400,
            detail=f"No se pudo dividir el PDF: {error}",
        ) from error
    finally:
        file.file.close()

    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename=clean_base_name(zip_name, "split-pdf.zip"),
        background=BackgroundTask(zip_path.unlink, missing_ok=True),
    )


# The page organizer uploads a PDF once and then refers to it by id, so
# thumbnails, previews and the final save never upload the file again.
# Documents live in a temporary folder and are removed after the download, after
# DOCUMENT_TTL_SECONDS without use, or when the page asks for it.
def get_document_path(document_id: str) -> Path:
    if not DOCUMENT_ID_PATTERN.fullmatch(document_id):
        raise HTTPException(status_code=404, detail="Documento no encontrado.")
    path = DOCUMENTS_DIR / f"{document_id}.pdf"
    try:
        # Refresh the last-use time; unlike Path.touch this never recreates a
        # file that another request has just deleted.
        os.utime(path)
    except FileNotFoundError:
        raise document_gone() from None
    return path


def document_gone() -> HTTPException:
    # 410 tells the page the upload expired, so it can send the file again.
    return HTTPException(
        status_code=410,
        detail="El documento ha caducado. Vuelve a seleccionar el PDF.",
    )


def open_pdfium_document(path: Path) -> pypdfium2.PdfDocument:
    try:
        return pypdfium2.PdfDocument(path)
    except Exception:
        if not path.exists():
            raise document_gone() from None
        raise


def remove_expired_documents(max_age: float = DOCUMENT_TTL_SECONDS) -> None:
    limit = time.time() - max_age
    for path in DOCUMENTS_DIR.iterdir():
        try:
            if path.stat().st_mtime < limit:
                path.unlink()
        except FileNotFoundError:
            pass


def sweep_documents_forever() -> None:
    while True:
        time.sleep(DOCUMENT_SWEEP_SECONDS)
        try:
            remove_expired_documents()
        except Exception:
            logger.exception("No se pudieron limpiar los documentos temporales.")


def start_document_sweeper() -> None:
    # Anything left from a previous run (crash, `docker restart`) is stale.
    remove_expired_documents(max_age=0)
    threading.Thread(target=sweep_documents_forever, daemon=True).start()


def build_edited_pdf(reader: PdfReader, pages: str) -> bytes:
    try:
        layout = json.loads(pages)
        if not isinstance(layout, list) or not layout:
            raise ValueError
        layout = [(int(item["page"]), int(item.get("rotation", 0))) for item in layout]
    except (ValueError, TypeError, KeyError) as error:
        raise HTTPException(
            status_code=400,
            detail="La lista de páginas no es válida.",
        ) from error

    page_count = len(reader.pages)
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
        if rotation % 360:
            page.rotate(rotation % 360)

    output = BytesIO()
    writer.write(output)
    writer.close()
    return output.getvalue()


@app.post("/api/documents")
def upload_document(file: UploadFile = File(...)):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="El archivo debe ser un PDF.")

    remove_expired_documents()
    document_id = uuid.uuid4().hex
    path = DOCUMENTS_DIR / f"{document_id}.pdf"
    try:
        with path.open("wb") as destination:
            shutil.copyfileobj(file.file, destination)
        with PDFIUM_LOCK:
            document = open_pdfium_document(path)
            page_count = len(document)
            document.close()
    except Exception as error:
        path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=400,
            detail=f"No se pudo leer el PDF: {error}",
        ) from error
    finally:
        file.file.close()

    return {"id": document_id, "pages": page_count}


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
            bitmap = pdf_page.render(scale=min(width / pdf_page.get_width(), 4))
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
        content = build_edited_pdf(PdfReader(path), pages)
    except FileNotFoundError:
        raise document_gone() from None
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(
            status_code=400,
            detail=f"No se pudo editar el PDF: {error}",
        ) from error

    # The result is about to be downloaded, so the uploaded copy is no longer
    # needed. If the user keeps editing, the page uploads the file again.
    path.unlink(missing_ok=True)

    return Response(
        content=content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{clean_output_name(output_name)}"',
        },
    )


@app.delete("/api/documents/{document_id}", status_code=204)
def delete_document(document_id: str):
    if DOCUMENT_ID_PATTERN.fullmatch(document_id):
        (DOCUMENTS_DIR / f"{document_id}.pdf").unlink(missing_ok=True)
    return Response(status_code=204)


@app.post("/api/edit-pages")
def edit_pages(
    file: UploadFile = File(...),
    pages: str = Form(...),
    output_name: str = Form("edited.pdf"),
):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="El archivo debe ser un PDF.")

    try:
        content = build_edited_pdf(PdfReader(file.file), pages)
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(
            status_code=400,
            detail=f"No se pudo editar el PDF: {error}",
        ) from error
    finally:
        file.file.close()

    return Response(
        content=content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{clean_output_name(output_name)}"',
        },
    )


A4_SHORT_INCHES = 210 / 25.4
A4_LONG_INCHES = 297 / 25.4
MIN_IMAGE_DPI = 72


def a4_resolution(width: int, height: int) -> float:
    """Return the dpi at which an image fits an A4 page in its own orientation.

    Small images keep 72 dpi (their natural size) instead of being blown up.
    """
    short_side, long_side = sorted((width, height))
    return max(short_side / A4_SHORT_INCHES, long_side / A4_LONG_INCHES, MIN_IMAGE_DPI)


@app.post("/api/images-to-pdf")
def images_to_pdf(
    images: list[UploadFile] = File(...),
    output_name: str = Form("images.pdf"),
):
    if not images:
        raise HTTPException(status_code=400, detail="Selecciona al menos una imagen.")

    converted_images = []
    try:
        for image_file in images:
            try:
                with Image.open(image_file.file) as image:
                    converted_images.append(image.convert("RGB"))
            except (UnidentifiedImageError, OSError) as error:
                raise HTTPException(
                    status_code=400,
                    detail=f"El archivo {image_file.filename or 'seleccionado'} no es una imagen válida.",
                ) from error

        # Pillow applies one resolution to every page, so each image becomes
        # its own one-page PDF at its own dpi and the pages are merged after.
        writer = PdfWriter()
        for image in converted_images:
            page = BytesIO()
            image.save(page, format="PDF", resolution=a4_resolution(*image.size))
            writer.append(PdfReader(page))
        output = BytesIO()
        writer.write(output)
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(
            status_code=400,
            detail=f"No se pudo convertir las imágenes: {error}",
        ) from error
    finally:
        for image in converted_images:
            image.close()
        for image_file in images:
            image_file.file.close()

    return Response(
        content=output.getvalue(),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{clean_output_name(output_name)}"',
        },
    )


IMAGE_FORMATS = {
    "png": ("PNG", "image/png"),
    "jpg": ("JPEG", "image/jpeg"),
}
IMAGE_DPI_CHOICES = {72, 150, 300}
# A big page at 300 dpi (A0 is about 140 million pixels) would need gigabytes of
# memory, so such pages are rendered at a lower resolution instead.
MAX_IMAGE_PIXELS = 40_000_000


@app.post("/api/pdf-to-images")
def pdf_to_images(
    file: UploadFile = File(...),
    image_format: str = Form("png"),
    dpi: int = Form(150),
    base_name: str = Form(""),
):
    """Render every page of a PDF as a PNG or JPG image.

    The images are named <base_name>-page-NNN.<ext> and returned as a ZIP; a
    one-page PDF is returned as a plain image.
    """
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="El archivo debe ser un PDF.")
    if image_format not in IMAGE_FORMATS:
        raise HTTPException(status_code=400, detail="El formato debe ser png o jpg.")
    if dpi not in IMAGE_DPI_CHOICES:
        raise HTTPException(status_code=400, detail="La resolución debe ser 72, 150 o 300 ppp.")

    pil_format, media_type = IMAGE_FORMATS[image_format]
    base = clean_base_name(base_name or Path(file.filename).stem, "pdf-images")
    if base.lower().endswith(".pdf"):
        base = base[:-4]

    # The upload and the ZIP live in the documents folder, so the regular sweep
    # removes them if the server stops halfway; normally they are deleted once
    # the response is sent.
    DOCUMENTS_DIR.mkdir(exist_ok=True)
    job_id = uuid.uuid4().hex
    input_path = DOCUMENTS_DIR / f"{job_id}.in.pdf"
    zip_path = DOCUMENTS_DIR / f"{job_id}.zip"

    def remove_job_files():
        input_path.unlink(missing_ok=True)
        zip_path.unlink(missing_ok=True)

    def render_page_image(document: pypdfium2.PdfDocument, index: int) -> bytes:
        pdf_page = document[index]
        try:
            scale = dpi / 72
            pixels = pdf_page.get_width() * pdf_page.get_height() * scale * scale
            if pixels > MAX_IMAGE_PIXELS:
                scale *= (MAX_IMAGE_PIXELS / pixels) ** 0.5
            bitmap = pdf_page.render(scale=scale)
            image = bitmap.to_pil().convert("RGB")
            output = BytesIO()
            if pil_format == "JPEG":
                image.save(output, format="JPEG", quality=90, dpi=(dpi, dpi))
            else:
                image.save(output, format="PNG", dpi=(dpi, dpi))
            image.close()
            bitmap.close()
            return output.getvalue()
        finally:
            pdf_page.close()

    try:
        with input_path.open("wb") as destination:
            shutil.copyfileobj(file.file, destination)

        # pdfium is not thread-safe, so the whole render holds the lock.
        with PDFIUM_LOCK:
            document = pypdfium2.PdfDocument(input_path)
            try:
                page_count = len(document)
                digits = max(3, len(str(page_count)))
                names = [f"{base}-page-{page:0{digits}d}.{image_format}" for page in range(1, page_count + 1)]

                if page_count == 1:
                    single_image = render_page_image(document, 0)
                else:
                    single_image = None
                    # PNG and JPG are already compressed, so they are stored as is.
                    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_STORED) as archive:
                        for index, name in enumerate(names):
                            archive.writestr(name, render_page_image(document, index))
            finally:
                document.close()
    except Exception as error:
        remove_job_files()
        raise HTTPException(
            status_code=400,
            detail=f"No se pudo convertir el PDF en imágenes: {error}",
        ) from error
    finally:
        file.file.close()

    if single_image is not None:
        remove_job_files()
        return Response(
            content=single_image,
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{names[0]}"'},
        )

    input_path.unlink(missing_ok=True)
    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename=f"{base}-{image_format}.zip",
        background=BackgroundTask(remove_job_files),
    )


@app.post("/api/word-to-pdf")
def word_to_pdf(
    file: UploadFile = File(...),
    output_name: str = Form("document.pdf"),
):
    return convert_office_to_pdf(file, output_name, {".doc", ".docx"}, "DOC o DOCX")


@app.post("/api/excel-to-pdf")
def excel_to_pdf(
    file: UploadFile = File(...),
    output_name: str = Form("spreadsheet.pdf"),
):
    return convert_office_to_pdf(file, output_name, {".xls", ".xlsx"}, "XLS o XLSX")


@app.post("/api/powerpoint-to-pdf")
def powerpoint_to_pdf(
    file: UploadFile = File(...),
    output_name: str = Form("presentation.pdf"),
):
    return convert_office_to_pdf(file, output_name, {".ppt", ".pptx"}, "PPT o PPTX")


def convert_office_to_pdf(
    file: UploadFile,
    output_name: str,
    extensions: set[str],
    formats_label: str,
) -> Response:
    # LibreOffice picks Writer, Calc or Impress from the file extension, so the
    # upload keeps its own extension on disk.
    file_extension = Path(file.filename or "").suffix.lower()
    if file_extension not in extensions:
        file.file.close()
        raise HTTPException(status_code=400, detail=f"El archivo debe ser {formats_label}.")

    soffice = find_soffice()
    if not soffice:
        file.file.close()
        raise HTTPException(
            status_code=503,
            detail="LibreOffice no está instalado o no está disponible en el PATH.",
        )

    try:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_path = Path(temporary_directory)
            input_path = temporary_path / f"source{file_extension}"
            input_path.write_bytes(file.file.read())
            profile_path = temporary_path / "profile"
            result = subprocess.run(
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
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            output_path = temporary_path / "source.pdf"
            if result.returncode != 0 or not output_path.exists():
                detail = result.stderr.strip() or result.stdout.strip()
                raise RuntimeError(detail or "LibreOffice no pudo convertir el documento.")
            output = output_path.read_bytes()
    except subprocess.TimeoutExpired as error:
        raise HTTPException(status_code=504, detail="La conversión tardó demasiado.") from error
    except Exception as error:
        raise HTTPException(
            status_code=400,
            detail=f"No se pudo convertir el documento: {error}",
        ) from error
    finally:
        file.file.close()

    return Response(
        content=output,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{clean_output_name(output_name)}"',
        },
    )



# Ghostscript presets: image resolution is what shrinks scanned documents.
COMPRESSION_LEVELS = {
    "strong": "/screen",  # 72 dpi images, smallest file
    "recommended": "/ebook",  # 150 dpi images, good for reading on screen
    "high": "/printer",  # 300 dpi images, good for printing
}
COMPRESSION_TIMEOUT_SECONDS = 600


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

    ghostscript = find_ghostscript()
    if not ghostscript:
        file.file.close()
        raise HTTPException(
            status_code=503,
            detail="Ghostscript no está instalado o no está disponible en el PATH.",
        )

    # Both files live in the documents folder, so the regular sweep removes
    # them if the server stops halfway; normally they are deleted once sent.
    DOCUMENTS_DIR.mkdir(exist_ok=True)
    job_id = uuid.uuid4().hex
    input_path = DOCUMENTS_DIR / f"{job_id}.in.pdf"
    output_path = DOCUMENTS_DIR / f"{job_id}.out.pdf"

    def remove_job_files():
        input_path.unlink(missing_ok=True)
        output_path.unlink(missing_ok=True)

    try:
        with input_path.open("wb") as destination:
            shutil.copyfileobj(file.file, destination)
        # Ghostscript "repairs" almost anything without failing, so make sure
        # this really is a readable PDF first.
        with PDFIUM_LOCK:
            pypdfium2.PdfDocument(input_path).close()
        result = subprocess.run(
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
            capture_output=True,
            text=True,
            timeout=COMPRESSION_TIMEOUT_SECONDS,
            check=False,
        )
        if result.returncode != 0 or not output_path.exists() or output_path.stat().st_size == 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise RuntimeError(detail or "Ghostscript no pudo comprimir el documento.")
    except subprocess.TimeoutExpired as error:
        remove_job_files()
        raise HTTPException(status_code=504, detail="La compresión tardó demasiado.") from error
    except Exception as error:
        remove_job_files()
        raise HTTPException(
            status_code=400,
            detail=f"No se pudo comprimir el PDF: {error}",
        ) from error
    finally:
        file.file.close()

    original_size = input_path.stat().st_size
    compressed_size = output_path.stat().st_size
    # Already optimized PDFs can come out bigger; then the original is better.
    compressed = compressed_size < original_size
    return FileResponse(
        output_path if compressed else input_path,
        media_type="application/pdf",
        filename=clean_output_name(output_name),
        headers={
            "X-Original-Size": str(original_size),
            "X-Compressed-Size": str(compressed_size if compressed else original_size),
            "X-Compression-Applied": "true" if compressed else "false",
        },
        background=BackgroundTask(remove_job_files),
    )
