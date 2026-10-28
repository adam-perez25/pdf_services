import base64
from io import BytesIO
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, UnidentifiedImageError
import pypdfium2
from pypdf import PdfReader, PdfWriter


BASE_DIR = Path(__file__).resolve().parent.parent
THUMBNAIL_WIDTH = 220
PREVIEW_WIDTH = 1400
app = FastAPI(title="Unir PDF")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


@app.get("/", response_class=HTMLResponse)
async def home():
    return render_page("index.html")


@app.get("/merge", response_class=HTMLResponse)
async def merge_page():
    return render_page("merge.html")


@app.get("/split", response_class=HTMLResponse)
async def split_page():
    return render_page("split.html")


@app.get("/images", response_class=HTMLResponse)
async def images_page():
    return render_page("images.html")


@app.get("/word", response_class=HTMLResponse)
async def word_page():
    return render_page("word.html")


@app.get("/pages", response_class=HTMLResponse)
async def pages_page():
    return render_page("pages.html")


def render_page(template_name: str) -> HTMLResponse:
    html = (BASE_DIR / "templates" / template_name).read_text(encoding="utf-8")
    return HTMLResponse(html)


def clean_base_name(output_name: str, default: str) -> str:
    name = Path(output_name.strip()).name
    name = re.sub(r"[^A-Za-z0-9._ -]", "_", name).strip(" .")
    if not name:
        name = default
    return name


def clean_output_name(output_name: str) -> str:
    name = clean_base_name(output_name, "merged")
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    return name


@app.post("/api/merge")
async def merge_pdfs(
    files: list[UploadFile] = File(...),
    output_name: str = Form("merged.pdf"),
):
    if not files:
        raise HTTPException(status_code=400, detail="Selecciona al menos un PDF.")

    writer = PdfWriter()
    try:
        for file in files:
            if not file.filename or not file.filename.lower().endswith(".pdf"):
                raise HTTPException(
                    status_code=400,
                    detail="Todos los archivos deben tener extension PDF.",
                )
            writer.append(file.file)

        output = BytesIO()
        writer.write(output)
        writer.close()
        output.seek(0)
    except HTTPException:
        writer.close()
        raise
    except Exception as error:
        writer.close()
        raise HTTPException(
            status_code=400,
            detail=f"No se pudieron unir los PDFs: {error}",
        ) from error
    finally:
        for file in files:
            await file.close()

    filename = clean_output_name(output_name)
    return Response(
        content=output.getvalue(),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@app.post("/api/pdf-info")
async def pdf_info(file: UploadFile = File(...)):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="El archivo debe ser un PDF.")

    try:
        reader = PdfReader(file.file)
        return {"pages": len(reader.pages)}
    except Exception as error:
        raise HTTPException(
            status_code=400,
            detail=f"No se pudo leer el PDF: {error}",
        ) from error
    finally:
        await file.close()


@app.post("/api/split")
async def split_pdf(
    file: UploadFile = File(...),
    output_name: str = Form("split-pdf"),
    start_page: int = Form(1),
    end_page: int | None = Form(None),
):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="El archivo debe ser un PDF.")

    try:
        reader = PdfReader(file.file)
        page_count = len(reader.pages)
        end_page = end_page or page_count
        if start_page < 1 or end_page < start_page or end_page > page_count:
            raise HTTPException(
                status_code=400,
                detail=f"El rango debe estar entre 1 y {page_count}.",
            )

        writer = PdfWriter()
        for page_index in range(start_page - 1, end_page):
            writer.add_page(reader.pages[page_index])

        output = BytesIO()
        writer.write(output)
        writer.close()
        output.seek(0)
    except Exception as error:
        if isinstance(error, HTTPException):
            raise
        raise HTTPException(
            status_code=400,
            detail=f"No se pudo dividir el PDF: {error}",
        ) from error
    finally:
        await file.close()

    return Response(
        content=output.getvalue(),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{clean_output_name(output_name)}"',
        },
    )


# Rendering and rewriting pages is CPU-bound, so these endpoints are plain
# functions: FastAPI runs them in a worker thread and the server stays responsive.
@app.post("/api/pdf-thumbnails")
def pdf_thumbnails(file: UploadFile = File(...)):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="El archivo debe ser un PDF.")

    try:
        document = pypdfium2.PdfDocument(file.file.read())
        try:
            thumbnails = []
            for page in document:
                bitmap = page.render(scale=THUMBNAIL_WIDTH / page.get_width())
                image = bitmap.to_pil()
                output = BytesIO()
                image.convert("RGB").save(output, format="JPEG", quality=70)
                thumbnails.append(
                    "data:image/jpeg;base64," + base64.b64encode(output.getvalue()).decode("ascii")
                )
                image.close()
                bitmap.close()
                page.close()
        finally:
            document.close()
    except Exception as error:
        raise HTTPException(
            status_code=400,
            detail=f"No se pudo leer el PDF: {error}",
        ) from error
    finally:
        file.file.close()

    return {"pages": len(thumbnails), "thumbnails": thumbnails}


@app.post("/api/pdf-page-preview")
def pdf_page_preview(file: UploadFile = File(...), page: int = Form(...)):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="El archivo debe ser un PDF.")

    try:
        document = pypdfium2.PdfDocument(file.file.read())
        try:
            if page < 1 or page > len(document):
                raise HTTPException(
                    status_code=400,
                    detail=f"La página debe estar entre 1 y {len(document)}.",
                )
            pdf_page = document[page - 1]
            scale = min(PREVIEW_WIDTH / pdf_page.get_width(), 4)
            bitmap = pdf_page.render(scale=scale)
            image = bitmap.to_pil()
            output = BytesIO()
            image.convert("RGB").save(output, format="JPEG", quality=85)
            image.close()
            bitmap.close()
            pdf_page.close()
        finally:
            document.close()
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(
            status_code=400,
            detail=f"No se pudo leer el PDF: {error}",
        ) from error
    finally:
        file.file.close()

    return Response(content=output.getvalue(), media_type="image/jpeg")


@app.post("/api/edit-pages")
def edit_pages(
    file: UploadFile = File(...),
    pages: str = Form(...),
    output_name: str = Form("edited.pdf"),
):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="El archivo debe ser un PDF.")

    try:
        layout = json.loads(pages)
        if not isinstance(layout, list) or not layout:
            raise ValueError
        layout = [(int(item["page"]), int(item.get("rotation", 0))) for item in layout]
    except (ValueError, TypeError, KeyError) as error:
        file.file.close()
        raise HTTPException(
            status_code=400,
            detail="La lista de páginas no es válida.",
        ) from error

    try:
        reader = PdfReader(file.file)
        page_count = len(reader.pages)
        writer = PdfWriter()
        for page_number, rotation in layout:
            if page_number < 1 or page_number > page_count:
                raise HTTPException(
                    status_code=400,
                    detail=f"Las páginas deben estar entre 1 y {page_count}.",
                )
            if rotation % 90 != 0:
                raise HTTPException(
                    status_code=400,
                    detail="La rotación debe ser múltiplo de 90 grados.",
                )
            page = writer.add_page(reader.pages[page_number - 1])
            if rotation % 360:
                page.rotate(rotation % 360)

        output = BytesIO()
        writer.write(output)
        writer.close()
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(
            status_code=400,
            detail=f"No se pudo editar el PDF: {error}",
        ) from error
    finally:
        file.file.close()

    return Response(
        content=output.getvalue(),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{clean_output_name(output_name)}"',
        },
    )


@app.post("/api/images-to-pdf")
async def images_to_pdf(
    images: list[UploadFile] = File(...),
    output_name: str = Form("images.pdf"),
):
    if not images:
        raise HTTPException(status_code=400, detail="Selecciona al menos una imagen.")

    converted_images = []
    try:
        for image_file in images:
            try:
                with Image.open(image_file.file) as image:
                    converted_images.append(image.convert("RGB"))
            except (UnidentifiedImageError, OSError) as error:
                raise HTTPException(
                    status_code=400,
                    detail=f"El archivo {image_file.filename or 'seleccionado'} no es una imagen válida.",
                ) from error

        output = BytesIO()
        converted_images[0].save(
            output,
            format="PDF",
            save_all=True,
            append_images=converted_images[1:],
        )
        output.seek(0)
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(
            status_code=400,
            detail=f"No se pudo convertir las imágenes: {error}",
        ) from error
    finally:
        for image in converted_images:
            image.close()
        for image_file in images:
            await image_file.close()

    return Response(
        content=output.getvalue(),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{clean_output_name(output_name)}"',
        },
    )


@app.post("/api/word-to-pdf")
async def word_to_pdf(
    file: UploadFile = File(...),
    output_name: str = Form("document.pdf"),
):
    file_extension = Path(file.filename or "").suffix.lower()
    if file_extension not in {".doc", ".docx"}:
        raise HTTPException(status_code=400, detail="El archivo debe ser DOC o DOCX.")

    soffice = shutil.which("soffice") or shutil.which("soffice.exe")
    if not soffice:
        raise HTTPException(
            status_code=503,
            detail="LibreOffice no está instalado o no está disponible en el PATH.",
        )

    try:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_path = Path(temporary_directory)
            input_path = temporary_path / f"source{file_extension}"
            input_path.write_bytes(await file.read())
            profile_path = temporary_path / "profile"
            result = subprocess.run(
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
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            output_path = temporary_path / "source.pdf"
            if result.returncode != 0 or not output_path.exists():
                detail = result.stderr.strip() or result.stdout.strip()
                raise RuntimeError(detail or "LibreOffice no pudo convertir el documento.")
            output = output_path.read_bytes()
    except subprocess.TimeoutExpired as error:
        raise HTTPException(status_code=504, detail="La conversión tardó demasiado.") from error
    except Exception as error:
        raise HTTPException(
            status_code=400,
            detail=f"No se pudo convertir el documento: {error}",
        ) from error
    finally:
        await file.close()

    return Response(
        content=output,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{clean_output_name(output_name)}"',
        },
    )

