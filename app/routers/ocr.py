"""OCR: make a scanned PDF searchable with ocrmypdf + Tesseract (limits in
config.OCR_*).
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import uuid

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pypdf import PdfReader
import pypdfium2
from starlette.background import BackgroundTask

from app import cancellation, config, external_tools, heavy_jobs
from app.documents import protect_from_sweeper
from app.errors import encrypted_pdf, logger, processing_error
from app.heavy_jobs import job_estimate_mb
from app.pdf_helpers import PDFIUM_LOCK, open_pdfium_checked, strip_before_pdf_header
from app.uploads import check_upload_sizes, clean_base_name, clean_output_name, file_download, upload_size


router = APIRouter()


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


@router.post("/api/ocr")
def ocr_pdf(
    request: Request,
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
    with cancellation.cancellable_job(request), \
            heavy_jobs.heavy_job(job_estimate_mb("ocr", upload_size(file)), [file]), \
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
