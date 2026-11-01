"""Sign a PDF with a picture of a signature (visual, not cryptographic)."""
import json
import math
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError
from pypdf import PageObject, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NumberObject

from app import heavy_jobs
from app.documents import document_gone, get_document_path, protect_from_sweeper
from app.errors import processing_error
from app.heavy_jobs import decoded_image_mb, job_estimate_mb
from app.image_helpers import image_pixels
from app.pdf_helpers import open_pdf_reader, pdf_number, visible_page_box
from app.uploads import check_upload_sizes, clean_output_name, file_download, upload_size, write_pdf_to_file


router = APIRouter()


# Places a drawn or uploaded signature image on pages of a document already in
# the organizer's store. It is only a picture on the page, not a cryptographic
# (digital) signature.
SIGNATURE_IMAGE_FORMATS = ["PNG", "JPEG"]
SIGNATURE_MAX_MB = 10
SIGNATURE_MAX_SOURCE_PIXELS = 40_000_000
SIGNATURE_MAX_SIDE = 2000
SIGNATURE_MAX_PLACEMENTS = 200
# Boxes may overshoot the page edge by this much (rounding in the browser).
SIGNATURE_TOLERANCE = 0.001


SIGNATURE_INVALID_MESSAGE = "La firma debe ser una imagen PNG o JPEG."


def check_signature_upload(file: UploadFile) -> int:
    """Check the signature's file size and pixel size (read from its header,
    nothing is decoded yet) and return its pixel count."""
    check_upload_sizes([file])
    if upload_size(file) > SIGNATURE_MAX_MB * 1024 * 1024:
        raise HTTPException(
            status_code=413,
            detail=f"La imagen de la firma puede ocupar como máximo {SIGNATURE_MAX_MB} MB.",
        )
    # formats= keeps Pillow away from EPS (which would run Ghostscript) and
    # every other format a signature does not need.
    invalid = HTTPException(status_code=400, detail=SIGNATURE_INVALID_MESSAGE)
    too_big = HTTPException(status_code=400, detail="La imagen de la firma es demasiado grande.")
    try:
        pixels = image_pixels(file, SIGNATURE_IMAGE_FORMATS, invalid)
    except HTTPException as error:
        if error is invalid:
            raise
        raise too_big from error  # over IMAGE_MAX_PIXELS
    if pixels > SIGNATURE_MAX_SOURCE_PIXELS:
        raise too_big
    return pixels


def load_signature_image(file: UploadFile) -> Image.Image:
    """Open the uploaded PNG/JPEG signature as an RGBA image of bounded size."""
    check_signature_upload(file)
    invalid = HTTPException(status_code=400, detail=SIGNATURE_INVALID_MESSAGE)
    try:
        with Image.open(file.file, formats=SIGNATURE_IMAGE_FORMATS) as image:
            image.verify()
        file.file.seek(0)
        with Image.open(file.file, formats=SIGNATURE_IMAGE_FORMATS) as image:
            signature = ImageOps.exif_transpose(image).convert("RGBA")
    except HTTPException:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError,
            Image.DecompressionBombWarning) as error:
        raise invalid from error
    signature.thumbnail((SIGNATURE_MAX_SIDE, SIGNATURE_MAX_SIDE))
    # A fully transparent or plain white image would "sign" with nothing
    # visible and still use up the uploaded document.
    on_white = Image.alpha_composite(Image.new("RGBA", signature.size, "white"), signature)
    if on_white.convert("L").getextrema()[0] > 245:
        raise HTTPException(status_code=400, detail="La imagen de la firma está vacía.")
    return signature


def parse_signature_placements(value: str) -> list[dict]:
    """Validate the placements JSON: a list of {page, x, y, width, height}
    with the box given as fractions of the page as displayed."""
    invalid = HTTPException(status_code=400, detail="La lista de posiciones de la firma no es válida.")
    outside = HTTPException(status_code=400, detail="La firma debe quedar dentro de la página.")
    try:
        items = json.loads(value)
    except (ValueError, RecursionError) as error:
        # RecursionError: "[[[[...]]]]" nested deeper than the parser allows.
        raise invalid from error
    if not isinstance(items, list) or not items:
        raise HTTPException(status_code=400, detail="Coloca la firma en al menos una página.")
    if len(items) > SIGNATURE_MAX_PLACEMENTS:
        raise HTTPException(
            status_code=400,
            detail=f"Puedes colocar la firma como máximo {SIGNATURE_MAX_PLACEMENTS} veces.",
        )

    placements = []
    for item in items:
        if not isinstance(item, dict):
            raise invalid
        page = item.get("page")
        if isinstance(page, bool) or not isinstance(page, int):
            raise invalid
        box = {}
        for key in ("x", "y", "width", "height"):
            number = item.get(key)
            if isinstance(number, bool) or not isinstance(number, (int, float)):
                raise invalid
            try:
                # float() of a JSON integer with hundreds of digits overflows.
                number = float(number)
            except OverflowError:
                raise invalid from None
            if not math.isfinite(number):
                raise invalid
            box[key] = number
        x, y, width, height = box["x"], box["y"], box["width"], box["height"]
        if (
            width <= 0 or height <= 0
            or x < -SIGNATURE_TOLERANCE or y < -SIGNATURE_TOLERANCE
            or x + width > 1 + SIGNATURE_TOLERANCE or y + height > 1 + SIGNATURE_TOLERANCE
        ):
            raise outside
        # Pull a box that overshoots only by the tolerance back onto the page.
        x, y = max(x, 0.0), max(y, 0.0)
        width, height = min(width, 1.0 - x), min(height, 1.0 - y)
        if width <= 0 or height <= 0:
            raise outside
        placements.append({"page": page, "x": x, "y": y, "width": width, "height": height})
    return placements


def signature_xobject(writer: PdfWriter, image: Image.Image):
    """Add an RGBA image to the writer as an image XObject and return its
    reference. The alpha channel becomes a soft mask, so the transparent parts
    of the signature leave the page visible."""

    def image_stream(data: bytes, color_space: str):
        stream = DecodedStreamObject()
        stream.set_data(data)
        stream = stream.flate_encode()
        stream.update({
            NameObject("/Type"): NameObject("/XObject"),
            NameObject("/Subtype"): NameObject("/Image"),
            NameObject("/Width"): NumberObject(image.width),
            NameObject("/Height"): NumberObject(image.height),
            NameObject("/ColorSpace"): NameObject(color_space),
            NameObject("/BitsPerComponent"): NumberObject(8),
        })
        return stream

    picture = image_stream(image.convert("RGB").tobytes(), "/DeviceRGB")
    alpha = image.getchannel("A")
    if alpha.getextrema() != (255, 255):
        # Streams must be indirect objects (pypdf would otherwise inline them
        # in the dictionary), so both are registered with the writer.
        picture[NameObject("/SMask")] = writer._add_object(image_stream(alpha.tobytes(), "/DeviceGray"))
    return writer._add_object(picture)


def signature_overlay(page: PageObject, xobject, boxes: list[dict]) -> PageObject:
    """Build an overlay page that draws the signature in each box. Boxes are
    fractions of the page as displayed (top-left origin); visible_page_box maps
    them onto the page's own coordinates whatever its /Rotate and cropbox."""
    matrix, width, height = visible_page_box(page)
    drawing = []
    for box in boxes:
        left = box["x"] * width
        bottom = (1 - box["y"] - box["height"]) * height
        drawing.append(
            f"q {pdf_number(box['width'] * width)} 0 0 {pdf_number(box['height'] * height)} "
            f"{pdf_number(left)} {pdf_number(bottom)} cm /Signature Do Q"
        )
    content = DecodedStreamObject()
    content.set_data(f"q {' '.join(matrix)} cm {' '.join(drawing)} Q".encode("ascii"))
    overlay = PageObject.create_blank_page(
        width=float(page.mediabox.width), height=float(page.mediabox.height)
    )
    overlay[NameObject("/Resources")] = DictionaryObject({
        NameObject("/XObject"): DictionaryObject({NameObject("/Signature"): xobject}),
    })
    overlay[NameObject("/Contents")] = content
    return overlay


@router.post("/api/documents/{document_id}/sign")
def sign_document(
    document_id: str,
    signature: UploadFile = File(...),
    placements: str = Form(...),
    output_name: str = Form(""),
):
    try:
        path = get_document_path(document_id)
        boxes = parse_signature_placements(placements)
        # The decoded signature counts towards the job's memory, so the
        # pixel size is checked (and the image decoded) inside heavy_job.
        decoded_mb = decoded_image_mb(check_signature_upload(signature))
        image = None
        try:
            # An open file, not the path: pypdf reads a path fully into memory.
            with protect_from_sweeper(path), \
                    heavy_jobs.heavy_job(job_estimate_mb("sign", path.stat().st_size, minimum_mb=decoded_mb)), \
                    path.open("rb") as source:
                image = load_signature_image(signature)
                output_path = sign_to_file(source, boxes, image)
        except FileNotFoundError:
            raise document_gone() from None
        except HTTPException:
            raise
        except Exception as error:
            raise processing_error("No se pudo firmar el PDF.", error) from error
        finally:
            if image is not None:
                image.close()
    finally:
        signature.file.close()

    # Like the organizer's save: the result is about to be downloaded, so the
    # stored copy goes. If the user keeps working, the page uploads it again.
    path.unlink(missing_ok=True)
    return file_download(output_path, clean_output_name(output_name or "firmado.pdf"))


def sign_to_file(source, boxes: list[dict], image: Image.Image) -> Path:
    reader = open_pdf_reader(source)
    page_count = len(reader.pages)
    by_page: dict[int, list[dict]] = {}
    for box in boxes:
        if box["page"] < 1 or box["page"] > page_count:
            raise HTTPException(
                status_code=400,
                detail=f"Las páginas deben estar entre 1 y {page_count}.",
            )
        by_page.setdefault(box["page"], []).append(box)

    writer = PdfWriter(clone_from=reader)
    try:
        xobject = signature_xobject(writer, image)
        for number, page_boxes in by_page.items():
            page = writer.pages[number - 1]
            page.merge_page(signature_overlay(page, xobject, page_boxes))
        return write_pdf_to_file(writer)
    finally:
        writer.close()
