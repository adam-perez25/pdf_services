"""Uploaded images: allowed formats and the pixel cap, checked from the
header before anything is decoded. Importing this module also sets Pillow's
own decompression-bomb limit.
"""
import warnings

from fastapi import HTTPException, UploadFile
from PIL import Image, UnidentifiedImageError


# Pillow also opens formats such as EPS, which it renders by running
# Ghostscript on the upload; only plain raster formats are accepted.
ALLOWED_IMAGE_FORMATS = ["PNG", "JPEG", "WEBP", "GIF", "BMP", "TIFF"]
# A 47 KB PNG can declare 13000 x 13000 pixels and decode to ~700 MB, so the
# size of every uploaded image is read from its header (no decoding) and
# images over this many pixels are rejected. Pillow's own decompression-bomb
# limit is set to the same value and its warning is turned into an error, so
# no Image.open in this service decodes a bigger image either.
IMAGE_MAX_PIXELS = 100_000_000
Image.MAX_IMAGE_PIXELS = IMAGE_MAX_PIXELS
warnings.simplefilter("error", Image.DecompressionBombWarning)
IMAGE_TOO_BIG_MESSAGE = "La imagen {name} es demasiado grande (máximo {megapixels} millones de píxeles)."


def image_pixels(file: UploadFile, formats: list[str], invalid: HTTPException) -> int:
    """Pixels of an uploaded image read from its header, without decoding it.

    Raises 400 if it is not an image of `formats` (`invalid`) or if it is
    bigger than IMAGE_MAX_PIXELS. The file is left at position 0.
    """
    too_big = HTTPException(
        status_code=400,
        detail=IMAGE_TOO_BIG_MESSAGE.format(
            name=file.filename or "seleccionada", megapixels=IMAGE_MAX_PIXELS // 1_000_000
        ),
    )
    try:
        file.file.seek(0)
        with Image.open(file.file, formats=formats) as image:
            pixels = image.width * image.height
    except (Image.DecompressionBombWarning, Image.DecompressionBombError) as error:
        raise too_big from error
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as error:
        raise invalid from error
    finally:
        file.file.seek(0)
    if pixels > IMAGE_MAX_PIXELS:
        raise too_big
    return pixels
