"""Word, Excel and PowerPoint to PDF with LibreOffice, always started with a
seeded throwaway profile that blocks linked resources.
"""
from pathlib import Path
import shutil
import subprocess
import tempfile

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from app import config, external_tools, heavy_jobs
from app.errors import processing_error
from app.heavy_jobs import job_estimate_mb
from app.uploads import (
    check_upload_sizes,
    clean_base_name,
    clean_output_name,
    file_download,
    new_job_paths,
    upload_size,
)


router = APIRouter()


@router.post("/api/word-to-pdf")
def word_to_pdf(
    file: UploadFile = File(...),
    output_name: str = Form(""),
):
    return convert_office_to_pdf(file, output_name, {".doc", ".docx"}, "DOC o DOCX", "Word")


@router.post("/api/excel-to-pdf")
def excel_to_pdf(
    file: UploadFile = File(...),
    output_name: str = Form(""),
):
    return convert_office_to_pdf(file, output_name, {".xls", ".xlsx"}, "XLS o XLSX", "Excel")


@router.post("/api/powerpoint-to-pdf")
def powerpoint_to_pdf(
    file: UploadFile = File(...),
    output_name: str = Form(""),
):
    return convert_office_to_pdf(file, output_name, {".ppt", ".pptx"}, "PPT o PPTX", "PowerPoint")


# The English defaults the pages used to send; they now mean "name it after
# the uploaded file", like an empty name.
OFFICE_OLD_DEFAULT_NAMES = {"document.pdf", "spreadsheet.pdf", "presentation.pdf"}
# .docx/.xlsx/.pptx are ZIP packages; Office also saves old binary (OLE)
# files with the new extension. The legacy .doc/.xls/.ppt are often RTF,
# HTML or CSV exports, which LibreOffice reads too, so they are not checked.
OFFICE_SIGNATURES = (b"PK\x03\x04", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")
OFFICE_PACKAGE_EXTENSIONS = {".docx", ".xlsx", ".pptx"}


def check_office_content(file: UploadFile, extension: str, kind: str) -> None:
    """Reject empty files, and .docx/.xlsx/.pptx that are neither ZIP nor OLE,
    before LibreOffice is started for nothing."""
    if upload_size(file) == 0:
        file.file.close()
        raise HTTPException(status_code=400, detail="El archivo está vacío.")
    if extension not in OFFICE_PACKAGE_EXTENSIONS:
        return
    position = file.file.tell()
    file.file.seek(0)
    header = file.file.read(8)
    file.file.seek(position)
    if not header.startswith(OFFICE_SIGNATURES):
        file.file.close()
        raise HTTPException(
            status_code=400,
            detail=f"El archivo no parece un documento de {kind} válido.",
        )


def office_output_name(output_name: str, upload_name: str) -> str:
    """The requested name, or the uploaded file's own name with .pdf."""
    name = output_name.strip()
    if not name or name.lower() in OFFICE_OLD_DEFAULT_NAMES:
        name = clean_base_name(Path(upload_name).stem, "documento")
    return clean_output_name(name)

# Settings written into each throwaway LibreOffice profile before converting.
# Without them a document can make the server fetch linked resources: an image
# linked to http://... is downloaded during the conversion (SSRF, tracking)
# and one linked to file:///... is embedded in the PDF. Keys and values come
# from LibreOffice's officecfg schema (Common.xcs, Writer.xcs, Calc.xcs,
# Inet.xcs); the first one alone stops both cases (checked in Docker), the
# rest are defense in depth.
LIBREOFFICE_PROFILE_SETTINGS = [
    # Block every link from a document outside the trusted locations (there
    # are none), which covers linked graphics in ODF and OOXML files.
    ("/org.openoffice.Office.Common/Security/Scripting", "BlockUntrustedRefererLinks", "true"),
    # No embedded active content (OLE objects) and no DDE links.
    ("/org.openoffice.Office.Common/Security/Scripting", "DisableActiveContent", "true"),
    # Never update links on load (Writer: 2 = never; Calc: 1 = never).
    ("/org.openoffice.Office.Writer/Content/Update", "Link", "2"),
    ("/org.openoffice.Office.Calc/Content/Update", "Link", "1"),
    # Anything that still tries HTTP(S) goes to a proxy on the discard port of
    # localhost, where the connection is refused (2 = manual proxy).
    ("/org.openoffice.Inet/Settings", "ooInetProxyType", "2"),
    ("/org.openoffice.Inet/Settings", "ooInetHTTPProxyName", "127.0.0.1"),
    ("/org.openoffice.Inet/Settings", "ooInetHTTPProxyPort", "9"),
    ("/org.openoffice.Inet/Settings", "ooInetHTTPSProxyName", "127.0.0.1"),
    ("/org.openoffice.Inet/Settings", "ooInetHTTPSProxyPort", "9"),
    ("/org.openoffice.Inet/Settings", "ooInetNoProxy", ""),
]


def write_libreoffice_profile(profile_path: Path) -> None:
    """Seed a new LibreOffice user profile with LIBREOFFICE_PROFILE_SETTINGS."""
    items = "".join(
        f'<item oor:path="{path}"><prop oor:name="{name}" oor:op="fuse">'
        f"<value>{value}</value></prop></item>\n"
        for path, name, value in LIBREOFFICE_PROFILE_SETTINGS
    )
    user_path = profile_path / "user"
    user_path.mkdir(parents=True, exist_ok=True)
    (user_path / "registrymodifications.xcu").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<oor:items xmlns:oor="http://openoffice.org/2001/registry" '
        'xmlns:xs="http://www.w3.org/2001/XMLSchema" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">\n'
        f"{items}</oor:items>\n",
        encoding="utf-8",
    )


def convert_office_to_pdf(
    file: UploadFile,
    output_name: str,
    extensions: set[str],
    formats_label: str,
    kind: str,
) -> Response:
    # LibreOffice picks Writer, Calc or Impress from the file extension, so the
    # upload keeps its own extension on disk.
    file_extension = Path(file.filename or "").suffix.lower()
    if file_extension not in extensions:
        file.file.close()
        raise HTTPException(status_code=400, detail=f"El archivo debe ser {formats_label}.")
    check_upload_sizes([file])
    check_office_content(file, file_extension, kind)

    soffice = external_tools.find_soffice()
    if not soffice:
        file.file.close()
        raise HTTPException(
            status_code=503,
            detail="LibreOffice no está instalado o no está disponible en el PATH.",
        )

    try:
        with heavy_jobs.heavy_job(job_estimate_mb("office", upload_size(file))), \
                tempfile.TemporaryDirectory() as temporary_directory:
            temporary_path = Path(temporary_directory)
            input_path = temporary_path / f"source{file_extension}"
            with input_path.open("wb") as destination:
                shutil.copyfileobj(file.file, destination)
            profile_path = temporary_path / "profile"
            write_libreoffice_profile(profile_path)
            result = external_tools.run_tool(
                [
                    soffice,
                    "--headless",
                    f"-env:UserInstallation={profile_path.as_uri()}",
                    "--convert-to",
                    "pdf",
                    "--outdir",
                    str(temporary_path),
                    str(input_path),
                ],
                timeout=config.OFFICE_TIMEOUT_SECONDS,
            )
            output_path = temporary_path / "source.pdf"
            if result.returncode != 0 or not output_path.exists():
                detail = result.stderr.strip() or result.stdout.strip()
                raise RuntimeError(detail or "LibreOffice no pudo convertir el documento.")
            # Moved out before the temporary folder is deleted, and streamed
            # from disk instead of read into memory.
            (result_path,) = new_job_paths(".out.pdf")
            shutil.move(output_path, result_path)
    except subprocess.TimeoutExpired as error:
        raise HTTPException(status_code=504, detail="La conversión tardó demasiado.") from error
    except HTTPException:
        raise
    except Exception as error:
        raise processing_error("No se pudo convertir el documento.", error) from error
    finally:
        file.file.close()

    return file_download(result_path, office_output_name(output_name, file.filename))
