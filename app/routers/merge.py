"""Merge PDFs."""
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pypdf import PdfWriter

from app import heavy_jobs
from app.errors import processing_error
from app.heavy_jobs import job_estimate_mb
from app.pdf_helpers import open_pdf_reader
from app.uploads import check_upload_sizes, clean_output_name, file_download, upload_size, write_pdf_to_file


router = APIRouter()


@router.post("/api/merge")
def merge_pdfs(
    files: list[UploadFile] = File(...),
    output_name: str = Form("merged.pdf"),
):
    if not files:
        raise HTTPException(status_code=400, detail="Selecciona al menos un PDF.")
    check_upload_sizes(files)

    writer = PdfWriter()
    try:
        for file in files:
            if not file.filename or not file.filename.lower().endswith(".pdf"):
                raise HTTPException(
                    status_code=400,
                    detail="Todos los archivos deben tener extensión PDF.",
                )
        with heavy_jobs.heavy_job(job_estimate_mb("merge", *map(upload_size, files))):
            for file in files:
                writer.append(open_pdf_reader(file.file))

            output_path = write_pdf_to_file(writer)
        writer.close()
    except HTTPException:
        writer.close()
        raise
    except Exception as error:
        writer.close()
        raise processing_error("No se pudieron unir los PDFs.", error) from error
    finally:
        for file in files:
            file.file.close()

    return file_download(output_path, clean_output_name(output_name))
