"""Extract text (PDF to TXT), streamed page by page into a job file."""
from pathlib import Path
import shutil
import uuid

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
import pypdfium2
from starlette.background import BackgroundTask

from app import config, heavy_jobs
from app.documents import protect_from_sweeper
from app.errors import processing_error
from app.heavy_jobs import job_estimate_mb
from app.pdf_helpers import (
    PDFIUM_LOCK,
    check_page_list_total,
    check_page_selection_length,
    open_pdfium_checked,
    parse_page_ranges,
)
from app.uploads import check_upload_sizes, clean_base_name, file_download, upload_size


router = APIRouter()


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


@router.post("/api/extract-text")
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
