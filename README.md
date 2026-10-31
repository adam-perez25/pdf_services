# PDF Toolkit

Aplicación web para trabajar con documentos desde el navegador. Permite unir y
dividir archivos PDF, organizar sus páginas, comprimirlos, convertir imágenes
a PDF, exportar las páginas de un PDF como imágenes, hacer buscables los PDF
escaneados (OCR) y transformar documentos
Word, hojas de cálculo Excel y presentaciones PowerPoint en PDF.

La aplicación está construida con [FastAPI](https://fastapi.tiangolo.com/),
[pypdf](https://pypi.org/project/pypdf/),
[pypdfium2](https://pypi.org/project/pypdfium2/) y
[Pillow](https://pypi.org/project/Pillow/). La interfaz se sirve desde el
mismo servidor y no requiere un frontend independiente.

## Funcionalidades

- **Unir PDF:** combina varios archivos PDF en el orden seleccionado.
- **Dividir PDF:** separa un PDF en una página por archivo o en rangos
  personalizados con nombre propio. Todo se genera en una sola petición y se
  descarga como un único ZIP (o como PDF si solo resulta un archivo).
- **Imágenes a PDF:** convierte varias imágenes ordenadas en un único PDF,
  con una página por imagen. Cada página se ajusta al tamaño A4 en la
  orientación de la imagen; las imágenes pequeñas no se amplían. Se aceptan
  PNG, JPEG, WEBP, GIF, BMP y TIFF; otros formatos (por ejemplo EPS) se
  rechazan.
- **Word a PDF:** convierte archivos `.doc` y `.docx` mediante LibreOffice en
  modo headless.
- **Excel a PDF:** convierte archivos `.xls` y `.xlsx`; todas las hojas del
  libro pasan al PDF.
- **PowerPoint a PDF:** convierte archivos `.ppt` y `.pptx`, con una página
  por diapositiva.
- **PDF a imágenes:** convierte cada página de un PDF en una imagen PNG o JPG
  a 72, 150 o 300 ppp. Las imágenes se descargan juntas en un ZIP (o como una
  sola imagen si el PDF tiene una página). Las páginas muy grandes se
  renderizan a menor resolución para no superar unos 40 millones de píxeles
  por imagen.
- **Comprimir PDF:** reduce el tamaño bajando la resolución de las imágenes
  mediante Ghostscript, ideal para documentos escaneados. Tres niveles: máxima
  (72 ppp), recomendada (150 ppp) y alta calidad (300 ppp). Si el resultado no
  es más pequeño, se descarga el original.
- **Proteger PDF:** cifra un PDF con AES-256 para que solo se abra con
  contraseña, o quita la contraseña de un PDF protegido si se conoce. La
  contraseña solo se usa durante la petición: no se guarda ni se registra.
- **Numerar páginas y marca de agua:** añade a todas las páginas el número de
  página (formato `1`, `1 / N` o `Página 1 de N`, número inicial, tamaño y
  posición arriba o abajo, a la izquierda, centrado o a la derecha) o un texto
  de marca de agua centrado, horizontal o en diagonal, con opacidad
  configurable. El texto usa la fuente Helvetica estándar (admite tildes y
  eñes) y aparece derecho aunque las páginas estén giradas.
- **Editar metadatos:** muestra y cambia el título, el autor, el asunto y las
  palabras clave de un PDF, o elimina todos sus metadatos (incluido el bloque
  XMP) antes de compartirlo, para no revelar quién lo creó, con qué programa ni
  cuándo.
- **OCR (hacer buscable un PDF escaneado):** reconoce el texto de las páginas
  escaneadas con [OCRmyPDF](https://ocrmypdf.readthedocs.io/) y Tesseract y lo
  añade como una capa invisible, para poder buscar, seleccionar y copiar el
  texto sin cambiar el aspecto del documento. Idiomas: español (por defecto),
  inglés o ambos. Las páginas que ya tienen texto se dejan como están. Tarda
  varios segundos por página.
- **Organizar páginas:** muestra una vista previa de cada página y permite
  girarlas, eliminarlas y reordenarlas (con los botones o arrastrándolas, también
  en pantallas táctiles manteniendo pulsada la página) antes de descargar el PDF
  resultante.
  Al pulsar una página se abre una vista ampliada en alta resolución.

Los archivos se procesan durante la solicitud y se devuelven como descargas.
La aplicación no incluye almacenamiento permanente de documentos.

## Requisitos

Para ejecutar el proyecto directamente:

- Python 3.12 o una versión compatible con las dependencias del proyecto.
- LibreOffice (Writer, Calc e Impress) instalado y disponible en el `PATH`
  para usar las conversiones de Word, Excel y PowerPoint.
- Ghostscript instalado y disponible en el `PATH` (`gs`, o `gswin64c` en
  Windows) para usar la compresión y el OCR.
- OCRmyPDF (incluido en `requirements.txt`) y Tesseract con los idiomas
  español e inglés (`tesseract` en el `PATH`) para usar el OCR.

Para ejecutarlo con Docker:

- Docker Engine o Docker Desktop.
- Docker Compose, normalmente incluido en Docker Desktop y en las
  instalaciones actuales de Docker.

## Ejecución con Docker

Docker Compose es la forma recomendada de ejecutar la aplicación porque
instala también LibreOffice, Ghostscript, Tesseract y evita configurar las dependencias del sistema
manualmente.

1. Clona el repositorio y entra en su directorio:

   ```bash
   git clone <URL_DEL_REPOSITORIO>
   cd <DIRECTORIO_DEL_REPOSITORIO>
   ```

2. Construye la imagen e inicia el servicio:

   ```bash
   docker compose up --build
   ```

3. Abre [http://localhost:8000](http://localhost:8000) en el navegador.

Para detener el servicio:

```bash
docker compose down
```

La imagen se basa en `python:3.12-slim`, instala LibreOffice (Writer, Calc e
Impress), las fuentes Liberation, Ghostscript y Tesseract (con los idiomas
español e inglés, para el OCR), y ejecuta el servicio con un usuario sin
privilegios (`app`, UID `10001`) en lugar de `root`. Tesseract y OCRmyPDF
añaden unos 170 MB a la imagen (de unos 1,1 GB a unos 1,27 GB en disco).

El contenedor expone el puerto `8000`. Si ese puerto ya está ocupado, cambia
el mapeo en `docker-compose.yml`; por ejemplo, `8080:8000` permite acceder a
la aplicación desde `http://localhost:8080`.

También es posible construir y ejecutar la imagen sin Compose:

```bash
docker build -t pdf-toolkit .
docker run --rm --name pdf-toolkit-web -p 8000:8000 pdf-toolkit
```

La imagen incluye un `HEALTHCHECK` que consulta `/health` cada 30 segundos.
`docker compose ps` muestra el contenedor como `healthy` cuando el servicio
responde y puede guardar archivos temporales, y como `unhealthy` si deja de
hacerlo.

## Ejecución local con Python

1. Crea y activa un entorno virtual:

   ```bash
   python -m venv .venv
   ```

   En Windows:

   ```powershell
   .venv\Scripts\Activate.ps1
   ```

   En macOS y Linux:

   ```bash
   source .venv/bin/activate
   ```

2. Instala las dependencias:

   ```bash
   python -m pip install -r requirements.txt
   ```

3. Inicia el servidor:

   ```bash
   python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
   ```

4. Abre [http://127.0.0.1:8000](http://127.0.0.1:8000).

Para desarrollo, `--reload` reinicia el servidor al detectar cambios:

```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Cuando se ejecute fuera de Docker, LibreOffice debe estar instalado por
separado para que funcionen **Word a PDF**, **Excel a PDF** y **PowerPoint a
PDF** (se busca el ejecutable `soffice`), y Ghostscript para **Comprimir PDF**
(`gs`, `gswin64c` o `gswin32c`), y Tesseract para el **OCR** (`tesseract`,
además del paquete `ocrmypdf` de `requirements.txt`). Si alguno no está disponible, esas
operaciones responderán con un error y las demás herramientas seguirán
disponibles. `GET /health` indica cuáles se han detectado.

## Configuración

La aplicación no usa variables de entorno ni archivo `.env`: no necesita
ninguna configuración para arrancar. Los valores principales están definidos
como constantes en `app/main.py`:

| Constante | Valor | Descripción |
| --- | --- | --- |
| `DOCUMENT_TTL_SECONDS` | 30 min | Tiempo sin uso tras el que se borra un documento de **Organizar páginas**. |
| `DOCUMENT_SWEEP_SECONDS` | 5 min | Frecuencia de la limpieza de documentos caducados. |
| `OFFICE_TIMEOUT_SECONDS` | 120 s | Tiempo máximo de una conversión con LibreOffice; si se supera responde `504`. |
| `COMPRESSION_TIMEOUT_SECONDS` | 600 s | Tiempo máximo de la compresión con Ghostscript; si se supera responde `504`. |
| `MAX_IMAGE_PIXELS` | 40 000 000 | Límite de píxeles por imagen en **PDF a imágenes**. |
| `MAX_UPLOAD_MB` | 50 MB | Tamaño máximo de cada archivo subido; si se supera responde `413`. |
| `MAX_TOTAL_UPLOAD_MB` | 200 MB | Tamaño máximo de todos los archivos de una petición (**Unir PDF**, **Imágenes a PDF**); si se supera responde `413`. |
| `MULTIPART_OVERHEAD_MB` | 1 MB | Margen para los separadores y campos del formulario. Una petición mayor que `MAX_TOTAL_UPLOAD_MB` + este margen se rechaza con `413` antes de guardarse en disco (por su `Content-Length`, o en cuanto lo supera si no lo indica). |
| `STAMP_NUMBER_SIZE_RANGE` | 6-72 pt | Tamaños de letra admitidos para los números de página. |
| `STAMP_WATERMARK_SIZE_RANGE` | 8-200 pt | Tamaños de letra admitidos para la marca de agua (se reduce si el texto no cabe). |
| `STAMP_WATERMARK_MAX_LENGTH` | 100 | Longitud máxima del texto de la marca de agua. |
| `STAMP_MAX_START_NUMBER` | 100 000 | Número inicial máximo de la numeración. |
| `STAMP_MARGIN` | 28 pt | Distancia de los números de página al borde. |
| `MAX_METADATA_LENGTH` | 500 | Longitud máxima de cada campo en **Editar metadatos**. |
| `OCR_LANGUAGES` | `spa`, `eng`, `spa+eng` | Idiomas admitidos por el OCR. |
| `OCR_JOBS` | 2 | Páginas que Tesseract procesa a la vez en cada petición de OCR. |
| `OCR_MAX_PAGES` | 200 | Número máximo de páginas de un PDF para el OCR; si se supera responde `400`. |
| `OCR_TIMEOUT_BASE_SECONDS` / `OCR_TIMEOUT_PER_PAGE_SECONDS` / `OCR_TIMEOUT_MAX_SECONDS` | 60 s / 20 s / 1800 s | Tiempo máximo del OCR: 60 s más 20 s por página, hasta 30 minutos; si se supera responde `504`. |

Si una conversión, compresión u OCR supera su tiempo máximo, se termina el proceso
externo junto con todos los procesos que haya iniciado.

El puerto se elige al arrancar `uvicorn` (`--port`) o en el mapeo de puertos
de Docker.

## Interfaz web

Cada herramienta tiene su propia página: `/` (inicio), `/merge`, `/split`,
`/pages`, `/compress`, `/metadata`, `/protect`, `/images`, `/pdf-to-images`, `/stamp`, `/ocr`, `/word`,
`/excel` y `/powerpoint`.

La interfaz tiene modo oscuro automático: sigue la preferencia de tema claro u
oscuro del sistema operativo, sin necesidad de configurar nada.

## API

Además de la interfaz web, el servidor ofrece estos endpoints:

| Método | Ruta | Descripción |
| --- | --- | --- |
| `GET` | `/health` | Estado del servicio: `storage` (puede guardar archivos temporales), `libreoffice` (conversiones de Office disponibles) y `ghostscript` (compresión disponible) y `ocr` (OCRmyPDF y Tesseract disponibles). Responde `503` si no puede guardar archivos. |
| `POST` | `/api/merge` | Une varios PDF recibidos como `files`. |
| `POST` | `/api/pdf-info` | Devuelve el número de páginas de un PDF. |
| `POST` | `/api/split` | Extrae un rango mediante `start_page` y `end_page`. |
| `POST` | `/api/split-zip` | Divide un PDF en una sola petición y devuelve un ZIP. `mode=pages` crea un PDF por página (`base_name`); `mode=ranges` usa `ranges` (`1-3, 4-6`) y la lista JSON `names`. Si solo resulta un archivo, devuelve el PDF directamente. |
| `POST` | `/api/images-to-pdf` | Convierte las imágenes recibidas como `images`. |
| `POST` | `/api/pdf-to-images` | Convierte cada página de un PDF en una imagen; `image_format` es `png` (por defecto) o `jpg`, `dpi` es `72`, `150` (por defecto) o `300` y `base_name` da nombre a las imágenes (`<base_name>-page-001.png`). Devuelve un ZIP, o la imagen directamente si el PDF tiene una página. |
| `POST` | `/api/word-to-pdf` | Convierte un archivo `.doc` o `.docx`. |
| `POST` | `/api/excel-to-pdf` | Convierte un archivo `.xls` o `.xlsx`. |
| `POST` | `/api/powerpoint-to-pdf` | Convierte un archivo `.ppt` o `.pptx`. |
| `POST` | `/api/compress` | Comprime un PDF; `level` es `strong`, `recommended` (por defecto) o `high`. Las cabeceras `X-Original-Size`, `X-Compressed-Size` y `X-Compression-Applied` indican el resultado. |
| `POST` | `/api/protect` | Cifra un PDF con AES-256 usando `password` (obligatoria, máximo 127 bytes). Responde `400` si el PDF ya estaba protegido. |
| `POST` | `/api/unlock` | Quita la contraseña de un PDF protegido usando `password` (puede ir vacía si el PDF solo tiene restricciones de permisos). Responde `400` con `Contraseña incorrecta` si no coincide, o si el PDF no estaba protegido. |
| `POST` | `/api/stamp` | Numera las páginas (`mode=numbers`, por defecto) o añade una marca de agua (`mode=watermark`). Números: `position` (`bottom-center` por defecto, `bottom-left`, `bottom-right`, `top-center`, `top-left`, `top-right`), `number_format` (`n` → `1`, `n-of-total` → `1 / N`, `page-n-of-total` → `Página 1 de N`; N es el último número), `start_number` (0-100000, por defecto 1) y `font_size` (6-72, por defecto 12). Marca de agua: `text` (obligatorio, hasta 100 caracteres), `font_size` (8-200, por defecto 60), `opacity` (0.1-1, por defecto 0.3) y `rotation` (`45` por defecto o `0`). `output_name` es opcional (`<nombre>-numerado.pdf` / `<nombre>-marca-de-agua.pdf`). |
| `POST` | `/api/metadata/read` | Devuelve en JSON los metadatos de un PDF (`title`, `author`, `subject`, `keywords`, `creator`, `producer`, `creation_date`, `modification_date`; `null` si faltan) y su número de páginas (`pages`). |
| `POST` | `/api/metadata` | Escribe `title`, `author`, `subject` y `keywords` (máximo 500 caracteres cada uno; un campo vacío o ausente se elimina), quita el bloque XMP y borra las entradas no estándar que añaden otros programas (se conservan creador, productor, fechas y `/Trapped`). Con `remove_all=true` elimina todos los metadatos. La descarga se llama `<nombre>-metadatos.pdf` salvo que se indique `output_name`. |
| `POST` | `/api/ocr` | Hace buscable un PDF escaneado con OCRmyPDF y Tesseract; `language` es `spa` (por defecto), `eng` o `spa+eng`. Las páginas que ya tienen texto se conservan. La descarga se llama `<nombre>-ocr.pdf` salvo que se indique `output_name`. Máximo 200 páginas. |
| `POST` | `/api/documents` | Sube un PDF una sola vez y devuelve su `id` y número de páginas. |
| `GET` | `/api/documents/{id}/pages/{n}` | Devuelve la página `n` como JPEG; `width` controla el tamaño (100-2000 px). |
| `POST` | `/api/documents/{id}/edit` | Genera el PDF editado a partir de `pages` sin volver a subir el archivo. |
| `DELETE` | `/api/documents/{id}` | Elimina el documento subido. |
| `POST` | `/api/edit-pages` | Genera un PDF a partir de `pages`, una lista JSON como `[{"page": 3, "rotation": 90}]`; las páginas omitidas se eliminan. |

Los documentos subidos a `/api/documents` se guardan en una carpeta temporal
del contenedor y se eliminan en cuanto se descarga el PDF editado, al cerrar
la página, tras 30 minutos sin uso (se comprueba cada 5 minutos) y al iniciar
el servidor. Si se sigue editando después de descargar, la página vuelve a
subir el archivo automáticamente.

Los endpoints que generan archivos aceptan el campo opcional `output_name`
para indicar el nombre de la descarga. Las peticiones de archivos usan
`multipart/form-data`.

Los errores devuelven un JSON con `detail` en español:

- `400`: archivo o parámetros no válidos, o PDF protegido con contraseña (hay
  que desbloquearlo antes; los PDF que solo tienen contraseña de propietario
  se procesan con normalidad). El detalle técnico del error no se envía al
  cliente: queda en el registro del servidor (`docker compose logs`).
- `413`: un archivo supera `MAX_UPLOAD_MB` o el conjunto supera
  `MAX_TOTAL_UPLOAD_MB`.
- `503`: falta LibreOffice, Ghostscript, OCRmyPDF o Tesseract (o un idioma
  de Tesseract). `504`: la conversión, la compresión o el OCR tardó demasiado.
- `410`: el documento de **Organizar páginas** ha caducado.

La documentación interactiva generada por FastAPI está disponible en
[`/docs`](http://localhost:8000/docs) cuando el servidor está en ejecución.
También se puede consultar el esquema OpenAPI en
[`/openapi.json`](http://localhost:8000/openapi.json).

## Pruebas

Las pruebas usan [pytest](https://docs.pytest.org/) y llaman a la API sin
levantar el servidor. Con el entorno virtual activado:

```bash
python -m pip install -r requirements-dev.txt
python -m pytest
```

`requirements-dev.txt` añade `pytest`, `httpx` (cliente de pruebas de
FastAPI) y `openpyxl` y `python-pptx`, que se usan para generar los archivos
de Excel y PowerPoint de las pruebas. La configuración de pytest está en
`pytest.ini`.

Las pruebas de conversión de Office, de compresión y de OCR se omiten
automáticamente si LibreOffice, Ghostscript o Tesseract no están instalados. Las pruebas no forman parte de la imagen de Docker.

## Estructura del proyecto

```text
app/
  main.py             # Aplicación FastAPI y endpoints
static/               # JavaScript (uno por herramienta, más common.js) y estilos
  fonts/              # Fuentes Manrope y DM Mono incluidas (licencia OFL)
templates/            # Páginas HTML de cada herramienta
tests/                # Pruebas con pytest (conftest.py y una por herramienta)
Dockerfile            # Imagen de producción
docker-compose.yml    # Servicio para Docker Compose
.dockerignore         # Archivos excluidos de la imagen
pytest.ini            # Configuración de pytest
requirements.txt      # Dependencias de Python
requirements-dev.txt  # Dependencias para ejecutar las pruebas
```

## Consideraciones de despliegue

El servidor está configurado para uso local y para ejecutarse detrás de un
contenedor. Antes de exponerlo públicamente conviene añadir, según el entorno,
autenticación, límites de frecuencia de subida, HTTPS y una política
de gestión de errores y registros. Los documentos subidos pueden contener
información sensible: utiliza el servicio únicamente en un entorno de
confianza y revisa la configuración de red del despliegue.

Ya incluido: el contenedor se ejecuta sin privilegios de `root`, y todas las
respuestas llevan cabeceras de seguridad (`Content-Security-Policy` que solo
permite recursos del propio servidor, `X-Content-Type-Options: nosniff`,
`X-Frame-Options: DENY` y `Referrer-Policy: no-referrer`). Los archivos de
`/static` se sirven con `Cache-Control: no-cache` para que el navegador
compruebe si hay una versión nueva tras reconstruir la imagen. Las subidas
tienen un tamaño máximo (`MAX_UPLOAD_MB` y `MAX_TOTAL_UPLOAD_MB`) y
`docker-compose.yml` limita la memoria del contenedor a 1 GB (`mem_limit`).

La interfaz no carga recursos externos: las fuentes Manrope y DM Mono se
sirven desde `static/fonts/`, distribuidas bajo la SIL Open Font License, por
lo que el navegador no realiza peticiones a servicios de terceros.
