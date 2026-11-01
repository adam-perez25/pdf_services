"""PDF helpers shared by the tools: opening PDFs safely (password-protected
files, the pdfium lock, bytes before the header), parsing page selections and
JSON form fields, and the page geometry used by the stamp and sign overlays.
"""
import json
from pathlib import Path
import re
import threading

from fastapi import HTTPException
from pypdf import PageObject, PdfReader
import pypdfium2

from app.documents import open_pdfium_document
from app.errors import encrypted_pdf, logger


PDFIUM_LOCK = threading.Lock()


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


def parse_page_ranges(value: str, page_count: int) -> list[tuple[int, int]]:
    ranges = []
    for part in filter(None, (part.strip() for part in value.split(","))):
        match = re.fullmatch(r"(\d+)(?:\s*-\s*(\d+))?", part)
        if not match:
            raise HTTPException(status_code=400, detail=f"Rango inválido: {part}")
        start = int(match.group(1))
        end = int(match.group(2) or start)
        if end < start:
            raise HTTPException(
                status_code=400,
                detail=f"Rango inválido: {part} (el inicio no puede ser mayor que el final).",
            )
        if start < 1 or end > page_count:
            raise HTTPException(
                status_code=400,
                detail=f"Los rangos deben estar entre 1 y {page_count}.",
            )
        ranges.append((start, end))
    if not ranges:
        raise HTTPException(status_code=400, detail="Escribe al menos un rango de páginas.")
    return ranges


# Page lists that may repeat pages (extract-text ranges, split-zip parts, the
# organizer's layout) are capped, so "1-2000,1-2000,..." cannot turn a small
# request into millions of pages of work: at most PAGE_LIST_FACTOR times the
# document's pages in total, but never less than PAGE_LIST_MIN_LIMIT (so a
# 1-page document can still be repeated a few dozen times).
PAGE_LIST_FACTOR = 10
PAGE_LIST_MIN_LIMIT = 100


def page_list_limit(page_count: int) -> int:
    return max(PAGE_LIST_FACTOR * page_count, PAGE_LIST_MIN_LIMIT)


def check_page_list_total(total: int, page_count: int) -> None:
    limit = page_list_limit(page_count)
    if total > limit:
        raise HTTPException(
            status_code=400,
            detail=f"La selección suma {total} páginas; el máximo para este PDF es {limit}.",
        )


def parse_user_json(value: str, message: str):
    """json.loads for a form field: bad JSON, and JSON nested so deeply that
    the parser hits the recursion limit, both answer 400 with `message`."""
    try:
        return json.loads(value)
    except (ValueError, RecursionError) as error:
        raise HTTPException(status_code=400, detail=message) from error


PAGE_SELECTION_MAX_LENGTH = 1000


def check_page_selection_length(pages: str) -> None:
    if len(pages) > PAGE_SELECTION_MAX_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"La lista de páginas puede tener como máximo {PAGE_SELECTION_MAX_LENGTH} caracteres.",
        )


def selected_page_numbers(pages: str, page_count: int) -> list[int]:
    """1-based page numbers in the parse_page_ranges syntax; empty = all.

    A page listed twice is converted once (its image name would repeat).
    """
    if not pages.strip():
        return list(range(1, page_count + 1))
    # Repeating huge ranges ("1-2000,1-2000,...") would make the expansion
    # below run for a long time before the duplicates are dropped.
    check_page_selection_length(pages)
    numbers = (
        number
        for start, end in parse_page_ranges(pages, page_count)
        for number in range(start, end + 1)
    )
    return list(dict.fromkeys(numbers))


# pdfium accepts the %PDF- header anywhere in the first 1024 bytes, but
# Ghostscript runs whatever comes before it as PostScript: a "%!PS" program
# followed by a valid PDF would pass the pdfium check and then execute.
PDF_HEADER = b"%PDF-"
PDF_HEADER_SEARCH_BYTES = 1024
NOT_A_PDF_MESSAGE = "El archivo no es un PDF válido."


def strip_before_pdf_header(path: Path) -> None:
    """Make the job file start with %PDF- at byte 0 before an external tool reads it.

    Bytes before the header (within PDF_HEADER_SEARCH_BYTES) are removed in
    place; a file with no header there is rejected with 400.
    """
    with path.open("r+b") as handle:
        offset = handle.read(PDF_HEADER_SEARCH_BYTES).find(PDF_HEADER)
        if offset < 0:
            raise HTTPException(status_code=400, detail=NOT_A_PDF_MESSAGE)
        if offset == 0:
            return
        logger.warning("Removed %d bytes before the PDF header of an upload.", offset)
        read_position, write_position = offset, 0
        while True:
            handle.seek(read_position)
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            handle.seek(write_position)
            handle.write(chunk)
            read_position += len(chunk)
            write_position += len(chunk)
        handle.truncate(write_position)


def pdf_number(value: float) -> str:
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


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
