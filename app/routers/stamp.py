"""Page numbers and text watermarks, drawn with Helvetica on an overlay."""
import math
from pathlib import Path
import unicodedata

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pypdf import PageObject, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, FloatObject, NameObject

from app import heavy_jobs
from app.errors import processing_error
from app.heavy_jobs import job_estimate_mb
from app.pdf_helpers import open_pdf_reader, pdf_number, visible_page_box
from app.uploads import check_upload_sizes, clean_output_name, file_download, upload_size, write_pdf_to_file


router = APIRouter()


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


@router.post("/api/stamp")
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
