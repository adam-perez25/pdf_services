"""PDF to images: render pages as PNG/JPG, with up-front pixel, disk and
time guards (the limits are config.PDF_TO_IMAGES_*).
"""
import errno
from io import BytesIO
import math
from pathlib import Path
import shutil
import time
import zipfile

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
import pypdfium2
from starlette.background import BackgroundTask

from app import config, heavy_jobs
from app.documents import protect_from_sweeper
from app.errors import logger, processing_error
from app.heavy_jobs import job_estimate_mb
from app.pdf_helpers import PDFIUM_LOCK, open_pdfium_checked, selected_page_numbers
from app.uploads import (
    check_upload_sizes,
    clean_base_name,
    content_disposition,
    file_download,
    new_job_paths,
    upload_size,
)


router = APIRouter()


IMAGE_FORMATS = {
    "png": ("PNG", "image/png"),
    "jpg": ("JPEG", "image/jpeg"),
}
IMAGE_DPI_CHOICES = {72, 150, 300}
# A big page at 300 dpi (A0 is about 140 million pixels) would need gigabytes of
# memory, so such pages are rendered at a lower resolution instead.
MAX_IMAGE_PIXELS = 40_000_000

# Estimated bytes per output pixel, used only for the up-front disk check. A
# text page takes well under 0.1 byte per pixel in PNG and a scanned or photo
# page 1-3; 0.5 covers typical mixed documents without rejecting ordinary text
# ones. JPG at quality 90 is several times smaller. The real write still stops
# cleanly if the disk fills anyway.
IMAGE_BYTES_PER_PIXEL = {"png": 0.5, "jpg": 0.2}


def pdf_to_images_timeout(page_count: int) -> float:
    scaled = config.PDF_TO_IMAGES_TIMEOUT_SECONDS + config.PDF_TO_IMAGES_SECONDS_PER_PAGE * page_count
    # The cap never cuts below the base time.
    return min(scaled, max(config.PDF_TO_IMAGES_MAX_TIMEOUT_SECONDS, config.PDF_TO_IMAGES_TIMEOUT_SECONDS))


def render_scale(width: float, height: float, dpi: int) -> float:
    """Scale for rendering a page of width x height points at dpi.

    Pages over MAX_IMAGE_PIXELS are rendered smaller; the estimate and the
    real render both use this function, so they agree.
    """
    scale = dpi / 72
    pixels = width * height * scale * scale
    if pixels > MAX_IMAGE_PIXELS:
        scale *= (MAX_IMAGE_PIXELS / pixels) ** 0.5
    return scale


def format_size_es(size: float) -> str:
    if size >= 1024 ** 3:
        return f"{size / 1024 ** 3:.1f} GB".replace(".", ",")
    return f"{max(1, round(size / 1024 ** 2))} MB"


def format_megapixels_es(pixels: float) -> str:
    megapixels = pixels / 1_000_000
    if megapixels < 10:
        return f"{megapixels:.1f}".replace(".", ",")
    return f"{round(megapixels):,}".replace(",", ".")


def is_disk_full(error: BaseException | None) -> bool:
    # Closing the ZIP after a failed write can raise a second error, so the
    # original one may be further down the context chain.
    while error is not None:
        if isinstance(error, OSError) and error.errno == errno.ENOSPC:
            return True
        error = error.__context__
    return False


def check_pdf_to_images_budget(total_pixels: float, page_total: int, dpi: int, image_format: str):
    """Reject a conversion that is too big, before rendering anything."""
    if not math.isfinite(total_pixels):
        # A broken page size would make every comparison below false.
        raise HTTPException(status_code=400, detail="El PDF tiene páginas con un tamaño no válido.")
    estimated_bytes = total_pixels * IMAGE_BYTES_PER_PIXEL[image_format]
    request = f"Convertir {'1 página' if page_total == 1 else f'{page_total} páginas'} a {dpi} ppp"
    if total_pixels > config.PDF_TO_IMAGES_MAX_MEGAPIXELS * 1_000_000:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{request} daría unos {format_megapixels_es(total_pixels)} millones de "
                f"píxeles (unos {format_size_es(estimated_bytes)} de imágenes), más del máximo "
                f"de {format_megapixels_es(config.PDF_TO_IMAGES_MAX_MEGAPIXELS * 1_000_000)} millones. "
                "Elige menos páginas o una resolución menor."
            ),
        )
    free = shutil.disk_usage(config.DOCUMENTS_DIR).free
    if estimated_bytes + config.PDF_TO_IMAGES_MIN_FREE_MB * 1024 * 1024 > free:
        raise HTTPException(
            status_code=507,
            detail=(
                f"{request} ocuparía unos {format_size_es(estimated_bytes)} y no hay espacio "
                f"suficiente en el disco temporal del servidor ({format_size_es(free)} libres, "
                f"de los que se reservan {format_size_es(config.PDF_TO_IMAGES_MIN_FREE_MB * 1024 * 1024)}). "
                "Elige menos páginas, una resolución menor o el formato JPG."
            ),
        )


DISK_FULL_IMAGES_MESSAGE = (
    "El disco temporal del servidor se ha llenado al crear las imágenes y se ha cancelado "
    "la conversión. Elige menos páginas, una resolución menor o el formato JPG."
)


@router.post("/api/pdf-to-images")
def pdf_to_images(
    file: UploadFile = File(...),
    image_format: str = Form("png"),
    dpi: int = Form(150),
    base_name: str = Form(""),
    pages: str = Form(""),
):
    """Render the pages of a PDF as PNG or JPG images.

    pages uses the parse_page_ranges syntax ("1-3, 5"); empty means all pages.
    The images are named <base_name>-page-NNN.<ext> with the real page number
    and returned as a ZIP; a single selected page is returned as a plain image.
    Before rendering, the total pixels and the estimated output size are
    checked (400 over PDF_TO_IMAGES_MAX_MEGAPIXELS, 507 if it would not fit in
    the temporary disk); a job over pdf_to_images_timeout answers 504, and a
    disk that fills while writing answers 507.
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
                scale = render_scale(pdf_page.get_width(), pdf_page.get_height(), dpi)
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
        with heavy_jobs.heavy_job(job_estimate_mb("pdf-to-images", upload_size(file)), [file]), \
                protect_from_sweeper(input_path, zip_path):
            with input_path.open("wb") as destination:
                shutil.copyfileobj(file.file, destination)

            with PDFIUM_LOCK:
                document = open_pdfium_checked(input_path)
                page_count = len(document)
            try:
                numbers = selected_page_numbers(pages, page_count)
                # Estimate from the page sizes (no page is loaded or rendered).
                with PDFIUM_LOCK:
                    total_pixels = 0.0
                    for number in numbers:
                        width, height = document.get_page_size(number - 1)
                        scale = render_scale(width, height, dpi)
                        total_pixels += width * height * scale * scale
                check_pdf_to_images_budget(total_pixels, len(numbers), dpi, image_format)

                digits = max(3, len(str(page_count)))
                names = [f"{base}-page-{number:0{digits}d}.{image_format}" for number in numbers]
                deadline = time.monotonic() + pdf_to_images_timeout(len(numbers))

                if len(numbers) == 1:
                    single_image = render_page_image(document, numbers[0] - 1)
                else:
                    single_image = None
                    # PNG and JPG are already compressed, so they are stored as is.
                    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_STORED) as archive:
                        for done, (number, name) in enumerate(zip(numbers, names), start=1):
                            archive.writestr(name, render_page_image(document, number - 1))
                            # Rendering runs in-process (no subprocess to
                            # kill), so the time limit is checked between pages.
                            if done < len(numbers) and time.monotonic() > deadline:
                                raise HTTPException(
                                    status_code=504,
                                    detail=(
                                        f"La conversión tardó demasiado y se ha cancelado tras "
                                        f"{done} de {len(numbers)} páginas. Elige menos páginas, "
                                        "una resolución menor o el formato JPG."
                                    ),
                                )
            finally:
                with PDFIUM_LOCK:
                    document.close()
    except HTTPException:
        remove_job_files()
        raise
    except Exception as error:
        remove_job_files()
        if is_disk_full(error):
            logger.warning("Disk full while converting a PDF to images: %s", error)
            raise HTTPException(status_code=507, detail=DISK_FULL_IMAGES_MESSAGE) from error
        raise processing_error("No se pudo convertir el PDF en imágenes.", error) from error
    finally:
        file.file.close()

    if single_image is not None:
        remove_job_files()
        return Response(
            content=single_image,
            media_type=media_type,
            headers={"Content-Disposition": content_disposition(names[0])},
        )

    input_path.unlink(missing_ok=True)
    return file_download(
        zip_path, f"{base}-{image_format}.zip", "application/zip", background=BackgroundTask(remove_job_files)
    )
