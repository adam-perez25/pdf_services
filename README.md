# PDF Toolkit

Aplicación web para trabajar con documentos desde el navegador. Permite unir y
dividir archivos PDF, organizar sus páginas, comprimirlos, convertir imágenes
a PDF, exportar las páginas de un PDF como imágenes y transformar documentos
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
  orientación de la imagen; las imágenes pequeñas no se amplían.
- **Word a PDF:** convierte archivos `.doc` y `.docx` mediante LibreOffice en
  modo headless.
- **Excel a PDF:** convierte archivos `.xls` y `.xlsx`; todas las hojas del
  libro pasan al PDF.
- **PowerPoint a PDF:** convierte archivos `.ppt` y `.pptx`, con una página
  por diapositiva.
- **PDF a imágenes:** convierte cada página de un PDF en una imagen PNG o JPG
  a 72, 150 o 300 ppp. Las imágenes se descargan juntas en un ZIP (o como una
  sola imagen si el PDF tiene una página).
- **Comprimir PDF:** reduce el tamaño bajando la resolución de las imágenes
  mediante Ghostscript, ideal para documentos escaneados. Tres niveles: máxima
  (72 ppp), recomendada (150 ppp) y alta calidad (300 ppp). Si el resultado no
  es más pequeño, se descarga el original.
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
  Windows) para usar la compresión.

Para ejecutarlo con Docker:

- Docker Engine o Docker Desktop.
- Docker Compose, normalmente incluido en Docker Desktop y en las
  instalaciones actuales de Docker.

## Ejecución con Docker

Docker Compose es la forma recomendada de ejecutar la aplicación porque
instala también LibreOffice, Ghostscript y evita configurar las dependencias del sistema
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
PDF**. Si no está disponible, esas operaciones responderán con un error y las demás herramientas seguirán
disponibles.

## API

Además de la interfaz web, el servidor ofrece estos endpoints:

| Método | Ruta | Descripción |
| --- | --- | --- |
| `GET` | `/health` | Estado del servicio: `storage` (puede guardar archivos temporales), `libreoffice` (conversiones de Office disponibles) y `ghostscript` (compresión disponible). Responde `503` si no puede guardar archivos. |
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

Las pruebas de conversión de Office y de compresión se omiten automáticamente si
LibreOffice o Ghostscript no están instalados. Las pruebas no forman parte de la imagen de Docker.

## Estructura del proyecto

```text
app/
  main.py             # Aplicación FastAPI y endpoints
static/               # JavaScript y estilos de la interfaz
templates/            # Páginas HTML
Dockerfile            # Imagen de producción
docker-compose.yml    # Servicio para Docker Compose
tests/                # Pruebas con pytest
requirements.txt      # Dependencias de Python
requirements-dev.txt  # Dependencias para ejecutar las pruebas
```

## Consideraciones de despliegue

El servidor está configurado para uso local y para ejecutarse detrás de un
contenedor. Antes de exponerlo públicamente conviene añadir, según el entorno,
autenticación, límites de tamaño y frecuencia de subida, HTTPS y una política
de gestión de errores y registros. Los documentos subidos pueden contener
información sensible: utiliza el servicio únicamente en un entorno de
confianza y revisa la configuración de red del despliegue.
