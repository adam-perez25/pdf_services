"""Split a PDF: one range, or several parts in a ZIP; plus /api/pdf-info."""
from pathlib import Path
import zipfile

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pypdf import PdfWriter

from app import heavy_jobs
from app.documents import protect_from_sweeper
from app.errors import processing_error
from app.heavy_jobs import job_estimate_mb
from app.pdf_helpers import check_page_list_total, open_pdf_reader, parse_page_ranges, parse_user_json
from app.uploads import (
    check_upload_sizes,
    clean_base_name,
    clean_output_name,
    file_download,
    new_job_paths,
    unique_pdf_names,
    upload_size,
    write_pdf_to_file,
)


router = APIRouter()


@router.post("/api/pdf-info")
def pdf_info(file: UploadFile = File(...)):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="El archivo debe ser un PDF.")
    check_upload_sizes([file])

    try:
        reader = open_pdf_reader(file.file)
        return {"pages": len(reader.pages)}
    except HTTPException:
        raise
    except Exception as error:
        raise processing_error("No se pudo leer el PDF.", error) from error
    finally:
        file.file.close()


@router.post("/api/split")
def split_pdf(
    file: UploadFile = File(...),
    output_name: str = Form("split-pdf"),
    start_page: int = Form(1),
    end_page: int | None = Form(None),
):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="El archivo debe ser un PDF.")
    check_upload_sizes([file])

    try:
        with heavy_jobs.heavy_job(job_estimate_mb("split", upload_size(file))):
            reader = open_pdf_reader(file.file)
            page_count = len(reader.pages)
            # Only a missing end_page means "to the last page"; 0 is an error.
            if end_page is None:
                end_page = page_count
            if start_page < 1 or end_page < 1 or end_page < start_page or end_page > page_count:
                raise HTTPException(
                    status_code=400,
                    detail=f"El rango debe estar entre 1 y {page_count}.",
                )

            writer = PdfWriter()
            for page_index in range(start_page - 1, end_page):
                writer.add_page(reader.pages[page_index])

            output_path = write_pdf_to_file(writer)
            writer.close()
    except Exception as error:
        if isinstance(error, HTTPException):
            raise
        raise processing_error("No se pudo dividir el PDF.", error) from error
    finally:
        file.file.close()

    return file_download(output_path, clean_output_name(output_name))


SPLIT_MAX_RANGES = 500


def parse_part_names(value: str, count: int) -> list[str | None]:
    """split-zip names: null/empty for all defaults, or a JSON list with one
    string (or null, for the default) per range."""
    message = "Los nombres no son válidos: deben ser una lista con un nombre por rango."
    if not value.strip():
        return [None] * count
    names = parse_user_json(value, message)
    if names is None:
        return [None] * count
    if (
        not isinstance(names, list)
        or len(names) != count
        or not all(name is None or isinstance(name, str) for name in names)
    ):
        raise HTTPException(status_code=400, detail=message)
    return names


@router.post("/api/split-zip")
def split_pdf_zip(
    file: UploadFile = File(...),
    mode: str = Form("pages"),
    ranges: str = Form(""),
    names: str = Form(""),
    base_name: str = Form("split-pdf"),
):
    """Split a PDF into several files in one request and return them as a ZIP.

    mode "pages" makes one PDF per page named <base_name>-page-NNN.pdf;
    mode "ranges" makes one PDF per range in `ranges` ("1-3, 4-6"), named by the
    JSON list `names`. A single resulting part is returned as a plain PDF.
    """
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="El archivo debe ser un PDF.")
    if mode not in {"pages", "ranges"}:
        raise HTTPException(status_code=400, detail="El modo debe ser pages o ranges.")
    check_upload_sizes([file])

    try:
        with heavy_jobs.heavy_job(job_estimate_mb("split", upload_size(file))):
            reader = open_pdf_reader(file.file)
            page_count = len(reader.pages)
            base = clean_base_name(base_name, "split-pdf")
            if base.lower().endswith(".pdf"):
                base = base[:-4]

            if mode == "pages":
                parts = [(page, page) for page in range(1, page_count + 1)]
                digits = max(3, len(str(page_count)))
                part_names = [f"{base}-page-{page:0{digits}d}.pdf" for page, _ in parts]
                zip_name = f"{base}.zip"
            else:
                parts = parse_page_ranges(ranges, page_count)
                if len(parts) > SPLIT_MAX_RANGES:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Puedes indicar como máximo {SPLIT_MAX_RANGES} rangos.",
                    )
                check_page_list_total(sum(end - start + 1 for start, end in parts), page_count)
                defaults = [f"split-{start}-{end}" for start, end in parts]
                given_names = parse_part_names(names, len(parts))
                part_names = unique_pdf_names(
                    [name or default for name, default in zip(given_names, defaults)], defaults
                )
                zip_name = f"{Path(file.filename).stem}-split.zip"

            def build_part(start: int, end: int) -> Path:
                # Each part goes through a work file, so a big range is never held
                # in memory as a whole.
                writer = PdfWriter()
                try:
                    for page_index in range(start - 1, end):
                        writer.add_page(reader.pages[page_index])
                    return write_pdf_to_file(writer)
                finally:
                    writer.close()

            if len(parts) == 1:
                return file_download(build_part(*parts[0]), part_names[0])

            # The ZIP goes to the documents folder, so it is removed once sent and,
            # if the download is interrupted, by the regular sweep. PDF content is
            # already compressed, so the parts are stored as they are: compressing
            # them again costs a lot of time and saves almost nothing.
            (zip_path,) = new_job_paths(".zip")
            try:
                with protect_from_sweeper(zip_path), zipfile.ZipFile(
                    zip_path, "w", compression=zipfile.ZIP_STORED
                ) as archive:
                    for (start, end), part_name in zip(parts, part_names):
                        part_path = build_part(start, end)
                        try:
                            archive.write(part_path, part_name)
                        finally:
                            part_path.unlink(missing_ok=True)
            except BaseException:
                zip_path.unlink(missing_ok=True)
                raise
    except HTTPException:
        raise
    except Exception as error:
        raise processing_error("No se pudo dividir el PDF.", error) from error
    finally:
        file.file.close()

    return file_download(zip_path, clean_base_name(zip_name, "split-pdf.zip"), "application/zip")
