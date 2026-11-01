"""Images to PDF: one A4-sized page per image (or per TIFF frame)."""
from io import BytesIO

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from PIL import Image, ImageOps, ImageSequence, UnidentifiedImageError
from pypdf import PdfReader, PdfWriter

from app import heavy_jobs
from app.errors import processing_error
from app.heavy_jobs import decoded_image_mb, job_estimate_mb
from app.image_helpers import ALLOWED_IMAGE_FORMATS, IMAGE_MAX_PIXELS, IMAGE_TOO_BIG_MESSAGE, image_pixels
from app.uploads import check_upload_sizes, clean_output_name, file_download, upload_size, write_pdf_to_file


router = APIRouter()


A4_SHORT_INCHES = 210 / 25.4
A4_LONG_INCHES = 297 / 25.4
MIN_IMAGE_DPI = 72


def a4_resolution(width: int, height: int) -> float:
    """Return the dpi at which an image fits an A4 page in its own orientation.

    Small images keep 72 dpi (their natural size) instead of being blown up.
    """
    short_side, long_side = sorted((width, height))
    return max(short_side / A4_SHORT_INCHES, long_side / A4_LONG_INCHES, MIN_IMAGE_DPI)


def image_page(frame: Image.Image) -> Image.Image:
    """An RGB copy of one image frame, ready to become a PDF page.

    Phone photos are turned upright from their EXIF orientation, and
    transparent areas (PNG, GIF, WebP) are painted white instead of the black
    a plain convert("RGB") gives.
    """
    # exif_transpose always copies, so it only runs when there is something to turn.
    orientation = frame.getexif().get(0x0112, 1)
    upright = (ImageOps.exif_transpose(frame) or frame) if orientation not in (None, 1) else frame
    try:
        if upright.mode == "P" and "transparency" in upright.info:
            upright = upright.convert("RGBA")
        if upright.mode in ("RGBA", "LA", "PA", "La", "RGBa") or "transparency" in upright.info:
            rgba = upright.convert("RGBA")
            background = Image.new("RGBA", rgba.size, "white")
            background.alpha_composite(rgba)
            rgba.close()
            converted = background.convert("RGB")
            background.close()
            return converted
        return upright.convert("RGB")
    finally:
        if upright is not frame:
            upright.close()


@router.post("/api/images-to-pdf")
def images_to_pdf(
    images: list[UploadFile] = File(...),
    output_name: str = Form("images.pdf"),
):
    if not images:
        raise HTTPException(status_code=400, detail="Selecciona al menos una imagen.")
    check_upload_sizes(images)

    def invalid_image(image_file: UploadFile) -> HTTPException:
        return HTTPException(
            status_code=400,
            detail=f"El archivo {image_file.filename or 'seleccionado'} no es una imagen válida.",
        )

    writer = PdfWriter()
    try:
        # Sizes come from the headers, before anything is decoded. Images are
        # decoded one at a time, so the biggest one sets the memory needed;
        # it counts even for small files, so a pixel bomb waits its turn.
        largest = max(
            image_pixels(image_file, ALLOWED_IMAGE_FORMATS, invalid_image(image_file))
            for image_file in images
        )
        estimate = job_estimate_mb(
            "images-to-pdf", *map(upload_size, images), minimum_mb=decoded_image_mb(largest)
        )
        with heavy_jobs.heavy_job(estimate):
            # Pillow applies one resolution to every page, so each image becomes
            # its own one-page PDF at its own dpi and the pages are merged after.
            # Images are decoded one at a time: a decoded photo takes many times
            # the memory of its file, so holding them all could exhaust memory.
            for image_file in images:
                try:
                    with Image.open(image_file.file, formats=ALLOWED_IMAGE_FORMATS) as image:
                        # A multi-page TIFF gives one page per frame; animated
                        # GIF/WebP only their first frame. Frames are decoded
                        # one at a time.
                        frame_count = getattr(image, "n_frames", 1) if image.format == "TIFF" else 1
                        for frame in ImageSequence.Iterator(image):
                            # Pillow checks the size of the first frame only.
                            if frame.width * frame.height > IMAGE_MAX_PIXELS:
                                raise HTTPException(
                                    status_code=400,
                                    detail=IMAGE_TOO_BIG_MESSAGE.format(
                                        name=image_file.filename or "seleccionada",
                                        megapixels=IMAGE_MAX_PIXELS // 1_000_000,
                                    ),
                                )
                            converted = image_page(frame)
                            try:
                                page = BytesIO()
                                converted.save(page, format="PDF", resolution=a4_resolution(*converted.size))
                            finally:
                                converted.close()
                            writer.append(PdfReader(page))
                            frame_count -= 1
                            if frame_count <= 0:
                                break
                except (UnidentifiedImageError, OSError, SyntaxError, ValueError,
                        Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
                    raise invalid_image(image_file) from error
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
