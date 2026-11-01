"""PDF/A conversion and PDF repair with Ghostscript (timeouts in
config.COMPRESSION_*, same pattern as Compress).
"""
from pathlib import Path
import shutil
import subprocess
import tempfile

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from starlette.background import BackgroundTask

from app import config, external_tools, heavy_jobs
from app.documents import protect_from_sweeper
from app.errors import processing_error
from app.heavy_jobs import job_estimate_mb
from app.pdf_helpers import PDFIUM_LOCK, open_pdfium_checked, strip_before_pdf_header
from app.uploads import check_upload_sizes, clean_base_name, clean_output_name, file_download, new_job_paths, upload_size


router = APIRouter()


# PDF/A parts Ghostscript can write. Part 2 is the usual choice for archives;
# 1 is the strictest (older viewers), 3 also allows attachments.
PDFA_PARTS = {"1", "2", "3"}
PDFA_DEFAULT_PART = "2"
PDFA_MISSING_PROFILE_MESSAGE = "PDF/A no está disponible: Ghostscript no tiene su perfil de color sRGB."
NO_PAGES_MARKER = "No pages will be processed"
REPAIR_FAILED_MESSAGE = "No se pudo reparar el PDF: está demasiado dañado."

# Registers the sRGB profile as the PDF/A output intent (the recipe from
# Ghostscript's own PDFA_def.ps). Read through --permit-file-read, because
# -dSAFER blocks every other file access.
PDFA_DEFINITION = """\
/ICCProfile ({profile}) def
[ /_objdef {{icc_PDFA}} /type /stream /OBJ pdfmark
[ {{icc_PDFA}} <</N 3>> /PUT pdfmark
[ {{icc_PDFA}} ICCProfile (r) file /PUT pdfmark
[ /_objdef {{OutputIntent_PDFA}} /type /dict /OBJ pdfmark
[ {{OutputIntent_PDFA}} <<
  /Type /OutputIntent
  /S /GTS_PDFA1
  /DestOutputProfile {{icc_PDFA}}
  /OutputConditionIdentifier (sRGB)
>> /PUT pdfmark
[ {{Catalog}} <</OutputIntents [ {{OutputIntent_PDFA}} ]>> /PUT pdfmark
"""


def rewrite_timeout(page_count: int | None) -> float:
    """Like compression_timeout; a damaged PDF whose pages cannot be counted
    gets the longest one."""
    if page_count is None:
        return config.COMPRESSION_MAX_TIMEOUT_SECONDS
    scaled = config.COMPRESSION_TIMEOUT_SECONDS + config.COMPRESSION_SECONDS_PER_PAGE * page_count
    return min(scaled, max(config.COMPRESSION_MAX_TIMEOUT_SECONDS, config.COMPRESSION_TIMEOUT_SECONDS))


def postscript_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def count_pages(path: Path, damaged_ok: bool) -> int | None:
    """Pages according to pdfium. A password-protected PDF is always refused;
    any other failure is a 400, unless damaged_ok (repair), which returns None."""
    try:
        with PDFIUM_LOCK:
            document = open_pdfium_checked(path)
            try:
                return len(document)
            finally:
                document.close()
    except HTTPException:
        raise
    except Exception:
        if damaged_ok:
            return None
        raise HTTPException(status_code=400, detail="El archivo no es un PDF válido.") from None


def ghostscript_rewrite(
    file: UploadFile, output_name: str, kind: str, suffix: str, options: list[str], timeout_message: str,
    failure_message: str, damaged_ok: bool, pdfa_part: str | None = None, profile: Path | None = None,
):
    """Run `file` through Ghostscript's pdfwrite and stream the new PDF."""
    ghostscript = external_tools.find_ghostscript()
    input_path, output_path = new_job_paths(".in.pdf", ".out.pdf")

    def remove_job_files():
        input_path.unlink(missing_ok=True)
        output_path.unlink(missing_ok=True)

    try:
        with heavy_jobs.heavy_job(job_estimate_mb(kind, upload_size(file)), [file]), \
                protect_from_sweeper(input_path, output_path), \
                tempfile.TemporaryDirectory(prefix="pdf-toolkit-gs-") as scratch:
            with input_path.open("wb") as destination:
                shutil.copyfileobj(file.file, destination)
            # Ghostscript would run a %!PS prefix instead of reading a PDF.
            strip_before_pdf_header(input_path)
            page_count = count_pages(input_path, damaged_ok)

            leading_files = []
            if profile is not None:
                definition = Path(scratch) / "pdfa-definition.ps"
                definition.write_text(PDFA_DEFINITION.format(profile=postscript_string(profile.as_posix())), encoding="utf-8")
                options = [*options, f"--permit-file-read={profile.as_posix()}"]
                leading_files = [str(definition)]
            result = external_tools.run_tool(
                [
                    ghostscript,
                    "-dSAFER",
                    "-dBATCH",
                    "-dNOPAUSE",
                    "-dQUIET",
                    "-sDEVICE=pdfwrite",
                    *options,
                    f"-sOutputFile={output_path}",
                    *leading_files,
                    str(input_path),
                ],
                timeout=rewrite_timeout(page_count),
            )
            if result.returncode != 0 or not output_path.exists() or output_path.stat().st_size == 0:
                detail = result.stderr.strip() or result.stdout.strip()
                raise RuntimeError(detail or "Ghostscript no pudo escribir el documento.")
            # From a hopeless input Ghostscript still exits 0 and writes a blank
            # document, but says it had no pages to process.
            if NO_PAGES_MARKER in result.stdout + result.stderr or not count_pages(output_path, damaged_ok=True):
                raise HTTPException(status_code=400, detail=failure_message)
    except subprocess.TimeoutExpired as error:
        remove_job_files()
        raise HTTPException(status_code=504, detail=timeout_message) from error
    except HTTPException:
        remove_job_files()
        raise
    except Exception as error:
        remove_job_files()
        raise processing_error(failure_message, error) from error
    finally:
        file.file.close()

    if output_name.strip():
        filename = clean_output_name(output_name)
    else:
        filename = clean_output_name(clean_base_name(Path(file.filename).stem, "documento") + suffix)
    headers = {"X-PDFA-Part": pdfa_part} if pdfa_part else None
    return file_download(output_path, filename, headers=headers, background=BackgroundTask(remove_job_files))


def check_pdf_upload(file: UploadFile) -> None:
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        file.file.close()
        raise HTTPException(status_code=400, detail="El archivo debe ser un PDF.")


@router.post("/api/pdfa")
def convert_to_pdfa(
    file: UploadFile = File(...),
    part: str = Form(PDFA_DEFAULT_PART),
    output_name: str = Form(""),
):
    check_pdf_upload(file)
    if part not in PDFA_PARTS:
        file.file.close()
        raise HTTPException(status_code=400, detail="La versión de PDF/A no es válida.")
    check_upload_sizes([file])
    if not external_tools.find_ghostscript():
        file.file.close()
        raise HTTPException(status_code=503, detail="Ghostscript no está instalado o no está disponible en el PATH.")
    profile = external_tools.find_srgb_profile()
    if profile is None:
        file.file.close()
        raise HTTPException(status_code=503, detail=PDFA_MISSING_PROFILE_MESSAGE)
    return ghostscript_rewrite(
        file, output_name, "pdfa", "-pdfa",
        [
            f"-dPDFA={part}",
            # Drop what PDF/A forbids (transparency in part 1, JavaScript...) instead of failing.
            "-dPDFACompatibilityPolicy=1",
            "-sColorConversionStrategy=RGB",
        ],
        timeout_message="La conversión a PDF/A tardó demasiado.",
        failure_message="No se pudo convertir el PDF a PDF/A.",
        damaged_ok=False,
        pdfa_part=part,
        profile=profile,
    )


@router.post("/api/repair")
def repair_pdf(
    file: UploadFile = File(...),
    output_name: str = Form(""),
):
    check_pdf_upload(file)
    check_upload_sizes([file])
    if not external_tools.find_ghostscript():
        file.file.close()
        raise HTTPException(status_code=503, detail="Ghostscript no está instalado o no está disponible en el PATH.")
    return ghostscript_rewrite(
        file, output_name, "repair", "-reparado",
        [],
        timeout_message="La reparación tardó demasiado.",
        failure_message=REPAIR_FAILED_MESSAGE,
        damaged_ok=True,
    )
