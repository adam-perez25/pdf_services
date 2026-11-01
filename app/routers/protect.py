"""Protect (AES-256) and unlock PDFs. Passwords are never logged or echoed."""
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pypdf import PdfReader, PdfWriter

from app import heavy_jobs
from app.errors import processing_error
from app.heavy_jobs import job_estimate_mb
from app.uploads import check_upload_sizes, clean_output_name, file_download, upload_size, write_pdf_to_file


router = APIRouter()


# Protect / unlock. Passwords only live in memory for the request: they are
# never logged, stored or echoed back in error messages.
# AES-256 (PDF 2.0, revision 6) passwords are limited to 127 UTF-8 bytes.
MAX_PASSWORD_BYTES = 127


def check_password(password: str) -> None:
    if not password:
        raise HTTPException(status_code=400, detail="Escribe una contraseña.")
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"La contraseña no puede superar {MAX_PASSWORD_BYTES} bytes.",
        )


@router.post("/api/protect")
def protect_pdf(
    file: UploadFile = File(...),
    password: str = Form(""),
    output_name: str = Form("protegido.pdf"),
):
    try:
        if not file.filename or not file.filename.lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail="El archivo debe ser un PDF.")
        check_upload_sizes([file])
        check_password(password)
        try:
            with heavy_jobs.heavy_job(job_estimate_mb("protect", upload_size(file))):
                reader = PdfReader(file.file)
                if reader.is_encrypted:
                    raise HTTPException(
                        status_code=400,
                        detail="Este PDF ya está protegido con contraseña. Desbloquéalo primero.",
                    )
                writer = PdfWriter(clone_from=reader)
                writer.encrypt(user_password=password, owner_password=password, algorithm="AES-256")
                output_path = write_pdf_to_file(writer)
                writer.close()
        except HTTPException:
            raise
        except Exception as error:
            raise processing_error("No se pudo proteger el PDF.", error) from error
    finally:
        file.file.close()

    return file_download(output_path, clean_output_name(output_name))


@router.post("/api/unlock")
def unlock_pdf(
    file: UploadFile = File(...),
    password: str = Form(""),
    output_name: str = Form("desbloqueado.pdf"),
):
    try:
        if not file.filename or not file.filename.lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail="El archivo debe ser un PDF.")
        check_upload_sizes([file])
        # An empty password is allowed here: PDFs that only have an owner
        # password (restrictions, no password to open) decrypt with "".
        if password:
            check_password(password)
        try:
            with heavy_jobs.heavy_job(job_estimate_mb("unlock", upload_size(file))):
                reader = PdfReader(file.file)
                if not reader.is_encrypted:
                    raise HTTPException(
                        status_code=400,
                        detail="Este PDF no está protegido con contraseña.",
                    )
                if not reader.decrypt(password):
                    if not password:
                        raise HTTPException(status_code=400, detail="Escribe la contraseña del PDF.")
                    raise HTTPException(status_code=400, detail="Contraseña incorrecta")
                writer = PdfWriter(clone_from=reader)
                output_path = write_pdf_to_file(writer)
                writer.close()
        except HTTPException:
            raise
        except Exception as error:
            raise processing_error("No se pudo desbloquear el PDF.", error) from error
    finally:
        file.file.close()

    return file_download(output_path, clean_output_name(output_name))
