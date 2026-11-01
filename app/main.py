from contextlib import asynccontextmanager
import mimetypes
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import uuid

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pypdf import PdfReader
import pypdfium2
from starlette.background import BackgroundTask

from app import config, external_tools, heavy_jobs
from app.documents import protect_from_sweeper, start_document_sweeper
from app.errors import encrypted_pdf, logger, processing_error
from app.heavy_jobs import job_estimate_mb
from app.middleware import (
    CrossSiteRequestGuard,
    UploadSizeLimit,
    add_security_headers,
    revalidate_static_files,
)
from app.pdf_helpers import PDFIUM_LOCK, open_pdfium_checked, strip_before_pdf_header
from app.routers import (
    extract_text,
    health,
    html_pages,
    images,
    merge,
    metadata,
    pages,
    pdf_to_images,
    protect,
    sign,
    split,
    stamp,
)
from app.uploads import (
    check_upload_sizes,
    clean_base_name,
    clean_output_name,
    file_download,
    new_job_paths,
    upload_size,
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

# Routers (one per tool), included in this order.
app.include_router(html_pages.router)
app.include_router(health.router)
app.include_router(merge.router)
app.include_router(split.router)
app.include_router(pages.router)
app.include_router(images.router)
app.include_router(pdf_to_images.router)
app.include_router(protect.router)
app.include_router(stamp.router)
app.include_router(metadata.router)
app.include_router(extract_text.router)
app.include_router(sign.router)


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
