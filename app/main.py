from contextlib import asynccontextmanager
from io import BytesIO
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, UnidentifiedImageError
import pypdfium2
from pypdf import PdfReader, PdfWriter


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


@app.get("/pages", response_class=HTMLResponse)
async def pages_page():
    return render_page("pages.html")


@app.get("/health")
def health():
    # Used by the Docker HEALTHCHECK. The service is unhealthy only if it cannot
    # store uploads; a missing LibreOffice is reported but only disables Word.
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
        },
    )


def find_soffice() -> str | None:
    return shutil.which("soffice") or shutil.which("soffice.exe")


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

        output = BytesIO()
        converted_images[0].save(
            output,
            format="PDF",
            save_all=True,
            append_images=converted_images[1:],
        )
        output.seek(0)
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


@app.post("/api/word-to-pdf")
def word_to_pdf(
    file: UploadFile = File(...),
    output_name: str = Form("document.pdf"),
):
    file_extension = Path(file.filename or "").suffix.lower()
    if file_extension not in {".doc", ".docx"}:
        raise HTTPException(status_code=400, detail="El archivo debe ser DOC o DOCX.")

    soffice = find_soffice()
    if not soffice:
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

