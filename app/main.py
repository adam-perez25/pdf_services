from io import BytesIO
from pathlib import Path
import re

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pypdf import PdfWriter


BASE_DIR = Path(__file__).resolve().parent.parent
app = FastAPI(title="Unir PDF")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


@app.get("/", response_class=HTMLResponse)
async def home():
    html = (BASE_DIR / "templates" / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(html)


def clean_output_name(output_name: str) -> str:
    name = Path(output_name.strip()).name
    name = re.sub(r"[^A-Za-z0-9._ -]", "_", name).strip(" .")
    if not name:
        name = "merged.pdf"
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


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
