"""Uploads in, downloads out: size checks, download names, job files and
the FileResponse helpers every tool returns its result with.
"""
import os
from pathlib import Path
import re
import unicodedata
from urllib.parse import quote
import uuid

from fastapi import HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from pypdf import PdfWriter
from starlette.background import BackgroundTask

from app import config
from app.documents import protect_from_sweeper


# Longest download name kept, not counting the extension, so a huge
# output_name cannot make a huge Content-Disposition header.
MAX_OUTPUT_NAME_LENGTH = 150


def clean_base_name(output_name: str, default: str) -> str:
    name = Path(output_name.strip()).name
    # Letters and digits of any alphabet (accents, ñ) are kept; anything else
    # (quotes, control characters, <>:"/\|?* that Windows rejects) becomes "_".
    name = re.sub(r"[^\w. -]", "_", unicodedata.normalize("NFC", name)).strip(" .")
    if not name:
        name = default
    stem, extension = os.path.splitext(name)
    if len(extension) > 10:  # not a real extension, part of the name
        stem, extension = name, ""
    if len(stem) > MAX_OUTPUT_NAME_LENGTH:
        name = (stem[:MAX_OUTPUT_NAME_LENGTH].strip(" .") or default) + extension
    return name


def clean_output_name(output_name: str) -> str:
    name = clean_base_name(output_name, "merged")
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    return name


def upload_size(file: UploadFile) -> int:
    # The upload is already spooled (to disk when big), so seeking to the end
    # measures it without reading it into memory.
    position = file.file.tell()
    file.file.seek(0, os.SEEK_END)
    size = file.file.tell()
    file.file.seek(position)
    return size


def check_upload_sizes(files: list[UploadFile]) -> None:
    """Reject the request with 413 if a file or all of them together are too big."""
    megabyte = 1024 * 1024
    sizes = [upload_size(file) for file in files]
    if any(size > config.MAX_UPLOAD_MB * megabyte for size in sizes):
        detail = f"Cada archivo puede ocupar como máximo {config.MAX_UPLOAD_MB} MB."
    elif sum(sizes) > config.MAX_TOTAL_UPLOAD_MB * megabyte:
        detail = f"Los archivos no pueden ocupar más de {config.MAX_TOTAL_UPLOAD_MB} MB en total."
    else:
        return
    for file in files:
        file.file.close()
    raise HTTPException(status_code=413, detail=detail)


def new_job_paths(*suffixes: str) -> list[Path]:
    config.DOCUMENTS_DIR.mkdir(exist_ok=True)
    job_id = uuid.uuid4().hex
    return [config.DOCUMENTS_DIR / f"{job_id}{suffix}" for suffix in suffixes]


def write_pdf_to_file(writer: PdfWriter) -> Path:
    """Write a result PDF to a work file instead of memory.

    Kept in memory, a big result would exist two or three times at once (the
    buffer, its copy and the response body); on disk it is streamed out.
    """
    (path,) = new_job_paths(".out.pdf")
    try:
        with protect_from_sweeper(path), path.open("wb") as destination:
            writer.write(destination)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path


def content_disposition(filename: str) -> str:
    """Content-Disposition for a download name (already cleaned by clean_base_name).

    Plain ASCII names go as filename="..."; others also get an ASCII fallback
    plus filename*=UTF-8''... (RFC 6266), which browsers prefer, so accents
    and ñ survive. Quotes, backslashes and control characters never reach the
    header, so it cannot be broken or extended.
    """
    # "año" -> "ano": accents are dropped, other non-ASCII letters become "_".
    fallback = "".join(
        char for char in unicodedata.normalize("NFKD", filename) if unicodedata.category(char) != "Mn"
    )
    fallback = re.sub(r'[^\x20-\x7e]|["\\]', "_", fallback).strip() or "descarga"
    if fallback == filename:
        return f'attachment; filename="{filename}"'
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename, safe='')}"


def file_download(path: Path, filename: str, media_type: str = "application/pdf", headers: dict | None = None,
                  background: BackgroundTask | None = None) -> FileResponse:
    """Send a work file as an attachment and delete it once it has been sent
    (or run `background` instead, when other files must go too)."""
    return FileResponse(
        path,
        media_type=media_type,
        headers={**(headers or {}), "Content-Disposition": content_disposition(filename)},
        background=background or BackgroundTask(path.unlink, missing_ok=True),
    )


def unique_pdf_names(names: list[str], defaults: list[str] | None = None) -> list[str]:
    # Two parts with the same name would overwrite each other inside the ZIP.
    # An empty (or dots-only) name falls back to its entry in defaults.
    seen: dict[str, int] = {}
    unique = []
    for index, name in enumerate(names):
        default = defaults[index] if defaults else "merged"
        stem = clean_base_name(name, default)
        if stem.lower().endswith(".pdf"):
            stem = stem[:-4].strip(" .") or default
        count = seen.get(stem.lower(), 0) + 1
        seen[stem.lower()] = count
        unique.append(f"{stem}.pdf" if count == 1 else f"{stem}-{count}.pdf")
    return unique


def pdf_attachment(content: bytes, filename: str) -> Response:
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": content_disposition(filename)},
    )
