"""Read and edit document properties (/Info plus a fresh XMP packet)."""
from pathlib import Path
import re
from xml.sax.saxutils import escape as xml_escape

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject, TextStringObject

from app import heavy_jobs
from app.errors import processing_error
from app.heavy_jobs import job_estimate_mb
from app.pdf_helpers import open_pdf_reader
from app.uploads import (
    check_upload_sizes,
    clean_base_name,
    clean_output_name,
    file_download,
    upload_size,
    write_pdf_to_file,
)


router = APIRouter()


# /Info entries kept on an edit besides the editable fields. Anything else
# (custom keys written by other programs) is removed, since it can carry
# private data the form does not show.
METADATA_KEPT_KEYS = {"/Creator", "/Producer", "/CreationDate", "/ModDate", "/Trapped"}
# Editable document properties: form field -> /Info key.
METADATA_FIELDS = {
    "title": "/Title",
    "author": "/Author",
    "subject": "/Subject",
    "keywords": "/Keywords",
}
# Spanish labels for error messages (the form shows the same words).
METADATA_LABELS = {"title": "Título", "author": "Autor", "subject": "Asunto", "keywords": "Palabras clave"}
MAX_METADATA_LENGTH = 500
PDF_DATE_PATTERN = re.compile(
    r"D:(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?(?:(Z)|([+-])(\d{2})'?(\d{2})?'?)?"
)


def pdf_date_to_xmp(value) -> str | None:
    """D:YYYYMMDDHHmmSS+HH'mm' (PDF) -> ISO 8601 (XMP); None if unreadable."""
    match = PDF_DATE_PATTERN.match(str(value))
    if not match:
        return None
    year, month, day, hour, minute, second, utc, sign, tz_hour, tz_minute = match.groups()
    text = f"{year}-{month or '01'}-{day or '01'}T{hour or '00'}:{minute or '00'}:{second or '00'}"
    if utc:
        return text + "Z"
    if sign:
        return f"{text}{sign}{tz_hour}:{tz_minute or '00'}"
    return text


def build_xmp(info) -> bytes:
    """An XMP packet with the same properties as the /Info dictionary.

    Many viewers (Adobe, the Windows property handler) show XMP rather than
    /Info, so after an edit both must say the same thing.
    """

    def text(key):
        value = info.get(key)
        return xml_escape(str(value)) if value is not None and str(value).strip() else None

    def alt(value):
        return f'<rdf:Alt><rdf:li xml:lang="x-default">{value}</rdf:li></rdf:Alt>'

    parts = []
    if text("/Title"):
        parts.append(f"<dc:title>{alt(text('/Title'))}</dc:title>")
    if text("/Author"):
        parts.append(f"<dc:creator><rdf:Seq><rdf:li>{text('/Author')}</rdf:li></rdf:Seq></dc:creator>")
    if text("/Subject"):
        parts.append(f"<dc:description>{alt(text('/Subject'))}</dc:description>")
    if text("/Keywords"):
        parts.append(f"<pdf:Keywords>{text('/Keywords')}</pdf:Keywords>")
    if text("/Producer"):
        parts.append(f"<pdf:Producer>{text('/Producer')}</pdf:Producer>")
    if text("/Creator"):
        parts.append(f"<xmp:CreatorTool>{text('/Creator')}</xmp:CreatorTool>")
    for key, tag in (("/CreationDate", "xmp:CreateDate"), ("/ModDate", "xmp:ModifyDate")):
        date = pdf_date_to_xmp(info[key]) if key in info else None
        if date:
            parts.append(f"<{tag}>{date}</{tag}>")
    return (
        '<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:pdf="http://ns.adobe.com/pdf/1.3/" xmlns:xmp="http://ns.adobe.com/xap/1.0/">'
        + "".join(parts)
        + '</rdf:Description></rdf:RDF></x:xmpmeta><?xpacket end="w"?>'
    ).encode("utf-8")


def open_metadata_reader(file: UploadFile) -> PdfReader:
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="El archivo debe ser un PDF.")
    check_upload_sizes([file])
    try:
        reader = open_pdf_reader(file.file)
        len(reader.pages)
    except HTTPException:
        raise
    except Exception as error:
        raise processing_error("No se pudo leer el PDF.", error) from error
    return reader


def metadata_text(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def metadata_date(info, key: str) -> str | None:
    if info.get(key) is None:
        return None
    try:
        date = info.creation_date if key == "/CreationDate" else info.modification_date
        return date.isoformat()
    except Exception:
        return metadata_text(info.get(key))


@router.post("/api/metadata/read")
def read_metadata(file: UploadFile = File(...)):
    try:
        reader = open_metadata_reader(file)
        try:
            info = reader.metadata
        except Exception:
            info = None
        result = {field: None for field in (*METADATA_FIELDS, "creator", "producer")}
        result.update(creation_date=None, modification_date=None)
        if info:
            for field, key in METADATA_FIELDS.items():
                result[field] = metadata_text(info.get(key))
            result["creator"] = metadata_text(info.get("/Creator"))
            result["producer"] = metadata_text(info.get("/Producer"))
            result["creation_date"] = metadata_date(info, "/CreationDate")
            result["modification_date"] = metadata_date(info, "/ModDate")
        result["pages"] = len(reader.pages)
        return result
    finally:
        file.file.close()


@router.post("/api/metadata")
def edit_metadata(
    file: UploadFile = File(...),
    title: str = Form(""),
    author: str = Form(""),
    subject: str = Form(""),
    keywords: str = Form(""),
    remove_all: bool = Form(False),
    output_name: str = Form(""),
):
    # The four fields replace the current values: an empty or missing field is
    # removed. Creator, producer and dates are kept unless remove_all is set.
    values = {"title": title, "author": author, "subject": subject, "keywords": keywords}
    writer = None
    try:
        for field, value in values.items():
            if len(value.strip()) > MAX_METADATA_LENGTH:
                raise HTTPException(
                    status_code=400,
                    detail=f"El campo «{METADATA_LABELS[field]}» no puede superar {MAX_METADATA_LENGTH} caracteres.",
                )
        reader = open_metadata_reader(file)
        with heavy_jobs.heavy_job(job_estimate_mb("metadata", upload_size(file))):
            writer = PdfWriter(clone_from=reader)
            # The old XMP stream repeats the old properties and many viewers
            # prefer it, so it is always dropped; an edit writes a fresh one below.
            if "/Metadata" in writer.root_object:
                del writer.root_object["/Metadata"]
            if remove_all:
                writer.metadata = None
                # Pages (and so their XMP) can carry metadata streams too.
                for page in writer.pages:
                    if "/Metadata" in page:
                        del page[NameObject("/Metadata")]
            else:
                if writer._info is None:
                    writer.metadata = {}
                info = writer._info.get_object()
                editable = set(METADATA_FIELDS.values())
                for key in list(info.keys()):
                    if key not in editable and key not in METADATA_KEPT_KEYS:
                        del info[key]
                for field, value in values.items():
                    key = METADATA_FIELDS[field]
                    if value.strip():
                        info[NameObject(key)] = TextStringObject(value.strip())
                    elif key in info:
                        del info[key]
                xmp = DecodedStreamObject()
                xmp.set_data(build_xmp(info))
                xmp.update({NameObject("/Type"): NameObject("/Metadata"), NameObject("/Subtype"): NameObject("/XML")})
                writer.root_object[NameObject("/Metadata")] = writer._add_object(xmp)
            # The clone copied every object, including the old /Info and XMP
            # stream; drop whatever is no longer referenced so they are not written.
            writer.compress_identical_objects(remove_duplicates=False, remove_unreferenced=True)
            output_path = write_pdf_to_file(writer)
    except HTTPException:
        raise
    except Exception as error:
        raise processing_error("No se pudieron editar los metadatos.", error) from error
    finally:
        if writer is not None:
            writer.close()
        file.file.close()

    if output_name.strip():
        filename = clean_output_name(output_name)
    else:
        filename = clean_output_name(clean_base_name(Path(file.filename).stem, "documento") + "-metadatos")
    return file_download(output_path, filename)
