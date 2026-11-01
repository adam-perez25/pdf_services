"""Compress PDF with Ghostscript (timeouts in config.COMPRESSION_*)."""
import shutil
import subprocess

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from starlette.background import BackgroundTask

from app import cancellation, config, external_tools, heavy_jobs
from app.documents import protect_from_sweeper
from app.errors import processing_error
from app.heavy_jobs import job_estimate_mb
from app.pdf_helpers import PDFIUM_LOCK, open_pdfium_checked, strip_before_pdf_header
from app.uploads import check_upload_sizes, clean_output_name, file_download, new_job_paths, upload_size


router = APIRouter()


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


@router.post("/api/compress")
def compress_pdf(
    request: Request,
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
        with cancellation.cancellable_job(request), \
                heavy_jobs.heavy_job(job_estimate_mb("compress", upload_size(file))), \
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
