from contextlib import asynccontextmanager
from io import BytesIO
import json
import logging
import math
import mimetypes
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import unicodedata
import uuid
import zipfile

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, UnidentifiedImageError
import pypdfium2
from pypdf import PageObject, PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, FloatObject, NameObject
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


# Everything the pages load comes from this server; blob: covers the local
# previews of selected images.
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' blob:; object-src 'none'; "
        "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}


@app.middleware("http")
async def add_security_headers(request, call_next):
    response = await call_next(request)
    response.headers.update(SECURITY_HEADERS)
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


def run_tool(command: list[str], timeout: float) -> subprocess.CompletedProcess:
    """Run an external tool, and on timeout kill it with every process it started.

    soffice is only a launcher: the conversion runs in a soffice.bin child, which
    subprocess.run's timeout would leave running after killing the launcher.
    On POSIX the tool gets its own process group, so the whole group is killed.
    """
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=os.name == "posix",
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except BaseException:
        kill_process_tree(process)
        process.communicate()
        raise
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def kill_process_tree(process: subprocess.Popen) -> None:
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    else:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(process.pid)],
            capture_output=True,
            check=False,
        )
        process.kill()


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
# Pillow also opens formats such as EPS, which it renders by running
# Ghostscript on the upload; only plain raster formats are accepted.
ALLOWED_IMAGE_FORMATS = ["PNG", "JPEG", "WEBP", "GIF", "BMP", "TIFF"]


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
                with Image.open(image_file.file, formats=ALLOWED_IMAGE_FORMATS) as image:
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


OFFICE_TIMEOUT_SECONDS = 120


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
            result = run_tool(
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
                timeout=OFFICE_TIMEOUT_SECONDS,
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
        result = run_tool(
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
            timeout=COMPRESSION_TIMEOUT_SECONDS,
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


def pdf_number(value: float) -> str:
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


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


def visible_page_box(page: PageObject) -> tuple[list[str], float, float]:
    """Return the cm matrix that maps upright, as-displayed coordinates to the
    page's own coordinates, and the displayed width and height."""
    box = page.cropbox
    x0, y0 = float(box.left), float(box.bottom)
    width, height = float(box.width), float(box.height)
    rotation = page.rotation % 360
    if rotation == 90:
        matrix, shown = (0, 1, -1, 0, x0 + width, y0), (height, width)
    elif rotation == 180:
        matrix, shown = (-1, 0, 0, -1, x0 + width, y0 + height), (width, height)
    elif rotation == 270:
        matrix, shown = (0, -1, 1, 0, x0, y0 + height), (height, width)
    else:
        matrix, shown = (1, 0, 0, 1, x0, y0), (width, height)
    return [pdf_number(value) for value in matrix], *shown


def number_stream(text: str, size: float, position: str, width: float, height: float) -> str:
    text_width = helvetica_width(text, size)
    margin = min(STAMP_MARGIN, min(width, height) * 0.08)
    vertical, horizontal = position.split("-")
    if horizontal == "left":
        x = margin
    elif horizontal == "right":
        x = width - margin - text_width
    else:
        x = (width - text_width) / 2
    # 0.718 em is Helvetica's cap height, so top numbers stay inside the margin.
    y = margin if vertical == "bottom" else height - margin - size * 0.718
    return (
        f"BT /StampFont {pdf_number(size)} Tf 0 g "
        f"{pdf_number(x)} {pdf_number(y)} Td {stamp_text_operator(text)} ET"
    )


def watermark_stream(text: str, size: float, rotation: int, width: float, height: float) -> str:
    # Shrink text that would not fit across the page (or along the diagonal).
    angle = math.radians(rotation)
    cos, sin = math.cos(angle), math.sin(angle)
    room = 0.9 * (min(width / cos, height / sin) if rotation else width)
    text_width = helvetica_width(text, size)
    if text_width > room:
        size *= room / text_width
        text_width = room
    return (
        "/StampAlpha gs 0.5 g "
        f"1 0 0 1 {pdf_number(width / 2)} {pdf_number(height / 2)} cm "
        f"{pdf_number(cos)} {pdf_number(sin)} {pdf_number(-sin)} {pdf_number(cos)} 0 0 cm "
        f"BT /StampFont {pdf_number(size)} Tf "
        f"{pdf_number(-text_width / 2)} {pdf_number(-size * 0.359)} Td {stamp_text_operator(text)} ET"
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


@app.get("/stamp", response_class=HTMLResponse)
async def stamp_page():
    return render_page("stamp.html")


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
            angle = int(parse_stamp_number(rotation, "La rotación", 0, 45))
            if angle not in STAMP_ROTATIONS:
                raise HTTPException(status_code=400, detail="La rotación debe ser 0 o 45 grados.")
            suffix = "marca-de-agua"

        try:
            writer = PdfWriter(clone_from=PdfReader(file.file))
            last = first + len(writer.pages) - 1 if mode == "numbers" else 0
            for index, page in enumerate(writer.pages):
                _, width, height = visible_page_box(page)
                if mode == "numbers":
                    label = STAMP_NUMBER_FORMATS[number_format].format(n=first + index, total=last)
                    overlay = stamp_overlay(page, number_stream(label, size, position, width, height))
                else:
                    overlay = stamp_overlay(page, watermark_stream(text, size, angle, width, height), alpha)
                page.merge_page(overlay)
            output = BytesIO()
            writer.write(output)
            writer.close()
        except Exception as error:
            raise HTTPException(
                status_code=400,
                detail=f"No se pudo modificar el PDF: {error}",
            ) from error
    finally:
        file.file.close()

    default_name = f"{Path(file.filename).stem}-{suffix}.pdf"
    return Response(
        content=output.getvalue(),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{clean_output_name(output_name or default_name)}"',
        },
    )
