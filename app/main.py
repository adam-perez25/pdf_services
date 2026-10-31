from contextlib import asynccontextmanager, contextmanager
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
from pypdf.generic import DecodedStreamObject, DictionaryObject, FloatObject, NameObject, TextStringObject
from starlette.background import BackgroundTask


def env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    """Read a whole-number setting from the environment, or use the default.

    A value that is not a number or is out of range stops the service at
    startup with a clear message, instead of quietly running with a limit
    nobody chose. The result is stored in a module constant and read at call
    time, so tests can still monkeypatch the constant.
    """
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"La variable de entorno {name}={raw!r} no es un número entero.") from None
    if not minimum <= value <= maximum:
        raise ValueError(
            f"La variable de entorno {name}={raw!r} debe estar entre {minimum} y {maximum}."
        )
    return value


BASE_DIR = Path(__file__).resolve().parent.parent
DOCUMENTS_DIR = Path(tempfile.gettempdir()) / "pdf-toolkit-documents"
DOCUMENTS_DIR.mkdir(exist_ok=True)
DOCUMENT_TTL_SECONDS = env_int("DOCUMENT_TTL_SECONDS", 30 * 60, 60, 7 * 24 * 3600)
DOCUMENT_SWEEP_SECONDS = env_int("DOCUMENT_SWEEP_SECONDS", 5 * 60, 10, 24 * 3600)
DOCUMENT_ID_PATTERN = re.compile(r"[0-9a-f]{32}")
PDFIUM_LOCK = threading.Lock()
THUMBNAIL_WIDTH = 300
MIN_RENDER_WIDTH = 100
MAX_RENDER_WIDTH = 2000
# Upload limits: per file, and for all the files of one request (merge, images).
# They stop accidents and abuse, not big documents: a 1000-page scan is
# usually a few hundred MB.
MAX_UPLOAD_MB = env_int("MAX_UPLOAD_MB", 500, 1, 100_000)
# Unset, the total follows a raised per-file limit instead of stopping startup.
MAX_TOTAL_UPLOAD_MB = env_int("MAX_TOTAL_UPLOAD_MB", max(1000, MAX_UPLOAD_MB), 1, 100_000)
if MAX_TOTAL_UPLOAD_MB < MAX_UPLOAD_MB:
    raise ValueError(
        f"MAX_TOTAL_UPLOAD_MB ({MAX_TOTAL_UPLOAD_MB}) no puede ser menor que MAX_UPLOAD_MB ({MAX_UPLOAD_MB})."
    )
# Extra room in a request for the multipart boundaries and the text fields.
MULTIPART_OVERHEAD_MB = 1
logger = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(app: FastAPI):
    start_document_sweeper()
    yield


app = FastAPI(title="Unir PDF", lifespan=lifespan)


def max_request_bytes() -> int:
    return int((MAX_TOTAL_UPLOAD_MB + MULTIPART_OVERHEAD_MB) * 1024 * 1024)


def request_too_big() -> str:
    return f"La petición supera el límite de {MAX_TOTAL_UPLOAD_MB} MB."


class UploadSizeLimit:
    """Stop oversized uploads before they are written to temporary files.

    The per-file checks in the handlers only run once Starlette has received
    and spooled the whole body, so this rejects a too-big Content-Length up
    front and cuts off bodies without one (chunked) as soon as they pass the
    limit, instead of filling the temp disk first.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST" or not scope["path"].startswith("/api/"):
            await self.app(scope, receive, send)
            return
        limit = max_request_bytes()
        length = dict(scope["headers"]).get(b"content-length", b"")
        if length.isdigit() and int(length) > limit:
            response = JSONResponse({"detail": request_too_big()}, status_code=413)
            await response(scope, receive, send)
            return

        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    # FastAPI re-raises HTTPException from body parsing, so
                    # this becomes a normal 413 response.
                    raise HTTPException(status_code=413, detail=request_too_big())
            return message

        await self.app(scope, limited_receive, send)


# Added before the decorator middlewares below, so it runs inside them and
# its 413 still gets the security headers.
app.add_middleware(UploadSizeLimit)
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


@app.get("/protect", response_class=HTMLResponse)
async def protect_page():
    return render_page("protect.html")


@app.get("/metadata", response_class=HTMLResponse)
async def metadata_page():
    return render_page("metadata.html")


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


def upload_size(file: UploadFile) -> int:
    # The upload is already spooled (to disk when big), so seeking to the end
    # measures it without reading it into memory.
    position = file.file.tell()
    file.file.seek(0, os.SEEK_END)
    size = file.file.tell()
    file.file.seek(position)
    return size


def check_upload_sizes(files: list[UploadFile]) -> None:
    """Reject the request with 413 if a file or all of them together are too big."""
    megabyte = 1024 * 1024
    sizes = [upload_size(file) for file in files]
    if any(size > MAX_UPLOAD_MB * megabyte for size in sizes):
        detail = f"Cada archivo puede ocupar como máximo {MAX_UPLOAD_MB} MB."
    elif sum(sizes) > MAX_TOTAL_UPLOAD_MB * megabyte:
        detail = f"Los archivos no pueden ocupar más de {MAX_TOTAL_UPLOAD_MB} MB en total."
    else:
        return
    for file in files:
        file.file.close()
    raise HTTPException(status_code=413, detail=detail)


def processing_error(message: str, error: Exception) -> HTTPException:
    # The raw error can expose internals (paths, library messages), so users
    # only get the Spanish message and the details go to the server log.
    logger.warning(message, exc_info=error)
    return HTTPException(status_code=400, detail=message)


ENCRYPTED_PDF_MESSAGE = (
    "Este PDF está protegido con contraseña. Desbloquéalo antes de usar esta herramienta."
)


def encrypted_pdf() -> HTTPException:
    return HTTPException(status_code=400, detail=ENCRYPTED_PDF_MESSAGE)


def open_pdf_reader(source) -> PdfReader:
    """Open a PDF with pypdf, rejecting it if it needs a password to be read.

    PDFs with only an owner password (empty user password) open normally.
    """
    reader = PdfReader(source)
    if reader.is_encrypted and not reader.decrypt(""):
        raise encrypted_pdf()
    return reader


def open_pdfium_checked(path: Path) -> pypdfium2.PdfDocument:
    """Open a PDF with pdfium, turning a password error into a clear 400."""
    try:
        return open_pdfium_document(path)
    except pypdfium2.PdfiumError as error:
        if getattr(error, "err_code", None) == pypdfium2.raw.FPDF_ERR_PASSWORD:
            raise encrypted_pdf() from error
        raise


# Work files of requests that are still being processed (a long compression,
# a big ZIP). The sweeper skips them, so a job that runs longer than
# DOCUMENT_TTL_SECONDS does not lose its files halfway.
ACTIVE_JOB_FILES: set[Path] = set()
ACTIVE_JOB_LOCK = threading.Lock()


@contextmanager
def protect_from_sweeper(*paths: Path):
    with ACTIVE_JOB_LOCK:
        ACTIVE_JOB_FILES.update(paths)
    try:
        yield
    finally:
        with ACTIVE_JOB_LOCK:
            ACTIVE_JOB_FILES.difference_update(paths)
        # The download starts right after; a fresh mtime gives it a full TTL
        # before the sweeper may remove a file whose cleanup never ran.
        for path in paths:
            try:
                os.utime(path)
            except FileNotFoundError:
                pass


def new_job_paths(*suffixes: str) -> list[Path]:
    DOCUMENTS_DIR.mkdir(exist_ok=True)
    job_id = uuid.uuid4().hex
    return [DOCUMENTS_DIR / f"{job_id}{suffix}" for suffix in suffixes]


def write_pdf_to_file(writer: PdfWriter) -> Path:
    """Write a result PDF to a work file instead of memory.

    Kept in memory, a big result would exist two or three times at once (the
    buffer, its copy and the response body); on disk it is streamed out.
    """
    (path,) = new_job_paths(".out.pdf")
    try:
        with protect_from_sweeper(path), path.open("wb") as destination:
            writer.write(destination)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path


def file_download(path: Path, filename: str, media_type: str = "application/pdf") -> FileResponse:
    """Send a work file as an attachment and delete it once it has been sent."""
    return FileResponse(
        path,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        background=BackgroundTask(path.unlink, missing_ok=True),
    )


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
                    detail="Todos los archivos deben tener extension PDF.",
                )
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
        reader = open_pdf_reader(file.file)
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

        output_path = write_pdf_to_file(writer)
        writer.close()
    except Exception as error:
        if isinstance(error, HTTPException):
            raise
        raise processing_error("No se pudo dividir el PDF.", error) from error
    finally:
        file.file.close()

    return file_download(output_path, clean_output_name(output_name))


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
    check_upload_sizes([file])

    try:
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
            try:
                given_names = [str(name) for name in json.loads(names)]
            except (ValueError, TypeError) as error:
                raise HTTPException(status_code=400, detail="Los nombres no son válidos.") from error
            given_names += [f"split-{start}-{end}" for start, end in parts[len(given_names):]]
            part_names = unique_pdf_names(given_names[:len(parts)])
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


def remove_expired_documents(max_age: float | None = None) -> None:
    # Read at call time, so the configured (or monkeypatched) TTL applies.
    limit = time.time() - (DOCUMENT_TTL_SECONDS if max_age is None else max_age)
    for path in DOCUMENTS_DIR.iterdir():
        # Holding the lock keeps a job from registering a file between the
        # check and the unlink.
        with ACTIVE_JOB_LOCK:
            if path in ACTIVE_JOB_FILES:
                continue
            try:
                if path.stat().st_mtime < limit:
                    path.unlink()
            except OSError:
                # Already gone, or (on Windows) still open by a request.
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
    # In-memory variant for small results; the endpoints use
    # build_edited_writer + write_pdf_to_file so big PDFs stay on disk.
    writer = build_edited_writer(reader, pages)
    output = BytesIO()
    writer.write(output)
    writer.close()
    return output.getvalue()


def build_edited_writer(reader: PdfReader, pages: str) -> PdfWriter:
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
    return writer


@app.post("/api/documents")
def upload_document(file: UploadFile = File(...)):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="El archivo debe ser un PDF.")
    check_upload_sizes([file])

    remove_expired_documents()
    document_id = uuid.uuid4().hex
    path = DOCUMENTS_DIR / f"{document_id}.pdf"
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
        # An open file, not the path: given a path, pypdf reads the whole
        # file into memory first.
        with protect_from_sweeper(path), path.open("rb") as source:
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
    check_upload_sizes([file])

    try:
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
    check_upload_sizes(images)

    writer = PdfWriter()
    try:
        # Pillow applies one resolution to every page, so each image becomes
        # its own one-page PDF at its own dpi and the pages are merged after.
        # Images are decoded one at a time: a decoded photo takes many times
        # the memory of its file, so holding them all could exhaust memory.
        for image_file in images:
            try:
                with Image.open(image_file.file, formats=ALLOWED_IMAGE_FORMATS) as image:
                    converted = image.convert("RGB")
            except (UnidentifiedImageError, OSError) as error:
                raise HTTPException(
                    status_code=400,
                    detail=f"El archivo {image_file.filename or 'seleccionado'} no es una imagen válida.",
                ) from error
            try:
                page = BytesIO()
                converted.save(page, format="PDF", resolution=a4_resolution(*converted.size))
            finally:
                converted.close()
            writer.append(PdfReader(page))
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
                scale = dpi / 72
                pixels = pdf_page.get_width() * pdf_page.get_height() * scale * scale
                if pixels > MAX_IMAGE_PIXELS:
                    scale *= (MAX_IMAGE_PIXELS / pixels) ** 0.5
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
        with protect_from_sweeper(input_path, zip_path):
            with input_path.open("wb") as destination:
                shutil.copyfileobj(file.file, destination)

            with PDFIUM_LOCK:
                document = open_pdfium_checked(input_path)
                page_count = len(document)
            try:
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
                with PDFIUM_LOCK:
                    document.close()
    except HTTPException:
        remove_job_files()
        raise
    except Exception as error:
        remove_job_files()
        raise processing_error("No se pudo convertir el PDF en imágenes.", error) from error
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


OFFICE_TIMEOUT_SECONDS = env_int("OFFICE_TIMEOUT_SECONDS", 300, 10, 24 * 3600)


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
    check_upload_sizes([file])

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
            with input_path.open("wb") as destination:
                shutil.copyfileobj(file.file, destination)
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
            # Moved out before the temporary folder is deleted, and streamed
            # from disk instead of read into memory.
            (result_path,) = new_job_paths(".out.pdf")
            shutil.move(output_path, result_path)
    except subprocess.TimeoutExpired as error:
        raise HTTPException(status_code=504, detail="La conversión tardó demasiado.") from error
    except Exception as error:
        raise processing_error("No se pudo convertir el documento.", error) from error
    finally:
        file.file.close()

    return file_download(result_path, clean_output_name(output_name))



# Ghostscript presets: image resolution is what shrinks scanned documents.
COMPRESSION_LEVELS = {
    "strong": "/screen",  # 72 dpi images, smallest file
    "recommended": "/ebook",  # 150 dpi images, good for reading on screen
    "high": "/printer",  # 300 dpi images, good for printing
}
# Ghostscript's time grows with the page count, so the timeout does too:
# base + per page, capped. 20 pages get 640 s, 1000 pages 2600 s.
COMPRESSION_TIMEOUT_SECONDS = env_int("COMPRESSION_TIMEOUT_SECONDS", 600, 10, 24 * 3600)
COMPRESSION_SECONDS_PER_PAGE = env_int("COMPRESSION_SECONDS_PER_PAGE", 2, 0, 600)
COMPRESSION_MAX_TIMEOUT_SECONDS = env_int("COMPRESSION_MAX_TIMEOUT_SECONDS", 3600, 10, 7 * 24 * 3600)


def compression_timeout(page_count: int) -> float:
    scaled = COMPRESSION_TIMEOUT_SECONDS + COMPRESSION_SECONDS_PER_PAGE * page_count
    # The cap never cuts below the base timeout.
    return min(scaled, max(COMPRESSION_MAX_TIMEOUT_SECONDS, COMPRESSION_TIMEOUT_SECONDS))


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

    ghostscript = find_ghostscript()
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
        with protect_from_sweeper(input_path, output_path):
            with input_path.open("wb") as destination:
                shutil.copyfileobj(file.file, destination)
            # Ghostscript "repairs" almost anything without failing, so make sure
            # this really is a readable PDF first.
            with PDFIUM_LOCK:
                document = open_pdfium_checked(input_path)
                page_count = len(document)
                document.close()
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


def pdf_attachment(content: bytes, filename: str) -> Response:
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
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
    margin = min(STAMP_MARGIN, min(width, height) * 0.08)
    # Shrink the label if it would run off the page (long labels, big font,
    # narrow page): it must fit between the margins, across and up.
    size = min(size, (height - 2 * margin) / 0.718)
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
            angle = int(parse_stamp_number(rotation, "La rotación", 0, 45))
            if angle not in STAMP_ROTATIONS:
                raise HTTPException(status_code=400, detail="La rotación debe ser 0 o 45 grados.")
            suffix = "marca-de-agua"

        try:
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
MAX_METADATA_LENGTH = 500


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
                    detail=f"El campo «{field}» no puede superar {MAX_METADATA_LENGTH} caracteres.",
                )
        reader = open_metadata_reader(file)
        writer = PdfWriter(clone_from=reader)
        # The XMP stream repeats the same properties and viewers prefer it, so
        # it is always dropped: otherwise the old values would still show up.
        if "/Metadata" in writer.root_object:
            del writer.root_object["/Metadata"]
        if remove_all:
            writer.metadata = None
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


# Flujo de trabajo: the chaining runs in the browser (static/workflow.js), which
# calls the existing /api endpoints one after another, so only the page lives here.
@app.get("/workflow", response_class=HTMLResponse)
async def workflow_page():
    return render_page("workflow.html")


# Extract text (PDF to TXT). The text is streamed page by page into a file in
# the documents folder, so a big document never becomes one huge string.
TEXT_SEPARATORS = {"none", "formfeed", "header"}
EMPTY_TEXT_MESSAGE = (
    "No se encontró texto en el PDF. Probablemente es un documento escaneado: "
    "pásalo primero por la herramienta OCR (/ocr) y vuelve a intentarlo."
)


@app.get("/extract-text", response_class=HTMLResponse)
async def extract_text_page():
    return render_page("extract-text.html")


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
    DOCUMENTS_DIR.mkdir(exist_ok=True)
    job_id = uuid.uuid4().hex
    input_path = DOCUMENTS_DIR / f"{job_id}.in.pdf"
    text_path = DOCUMENTS_DIR / f"{job_id}.txt"

    def remove_job_files():
        input_path.unlink(missing_ok=True)
        text_path.unlink(missing_ok=True)

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
                    numbers = [
                        number
                        for start, end in parse_page_ranges(pages, page_count)
                        for number in range(start, end + 1)
                    ]
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
    return FileResponse(
        text_path,
        media_type="text/plain; charset=utf-8",
        filename=filename,
        headers={
            "X-Page-Count": str(len(numbers)),
            "X-Character-Count": str(character_count),
        },
        background=BackgroundTask(remove_job_files),
    )
