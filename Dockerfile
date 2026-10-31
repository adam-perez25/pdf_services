FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
	&& apt-get install -y --no-install-recommends libreoffice-writer libreoffice-calc libreoffice-impress fonts-liberation ghostscript \
		tesseract-ocr tesseract-ocr-spa tesseract-ocr-eng \
	&& rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
	&& pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY static ./static
COPY templates ./templates

# LibreOffice, Ghostscript and the PDF/image libraries parse untrusted uploads,
# so the service runs as an unprivileged user instead of root. The code stays
# owned by root and read-only; uploads only go to /tmp.
RUN useradd --system --create-home --uid 10001 app
USER app

EXPOSE 8000

# The slim image has no curl, so the check uses Python; a non-200 answer or no
# answer at all makes urlopen raise and the check fail.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
	CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
