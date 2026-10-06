# PDF Toolkit

Aplicación web para trabajar con documentos desde el navegador. Permite unir y
dividir archivos PDF, convertir imágenes a PDF y transformar documentos Word
en PDF.

La aplicación está construida con [FastAPI](https://fastapi.tiangolo.com/),
[pypdf](https://pypi.org/project/pypdf/) y
[Pillow](https://pypi.org/project/Pillow/). La interfaz se sirve desde el
mismo servidor y no requiere un frontend independiente.

## Funcionalidades

- **Unir PDF:** combina varios archivos PDF en el orden seleccionado.
- **Dividir PDF:** extrae un rango de páginas de un PDF y lo descarga como un
  archivo independiente.
- **Imágenes a PDF:** convierte varias imágenes ordenadas en un único PDF,
  con una página por imagen.
- **Word a PDF:** convierte archivos `.doc` y `.docx` mediante LibreOffice en
  modo headless.

Los archivos se procesan durante la solicitud y se devuelven como descargas.
La aplicación no incluye almacenamiento permanente de documentos.

## Requisitos

Para ejecutar el proyecto directamente:

- Python 3.12 o una versión compatible con las dependencias del proyecto.
- LibreOffice instalado y disponible en el `PATH` para usar la conversión de
  Word.

Para ejecutarlo con Docker:

- Docker Engine o Docker Desktop.
- Docker Compose, normalmente incluido en Docker Desktop y en las
  instalaciones actuales de Docker.

## Ejecución con Docker

Docker Compose es la forma recomendada de ejecutar la aplicación porque
instala también LibreOffice y evita configurar las dependencias del sistema
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
separado para que funcione **Word a PDF**. Si no está disponible, esa
operación responderá con un error y las demás herramientas seguirán
disponibles.

## API

Además de la interfaz web, el servidor ofrece estos endpoints:

| Método | Ruta | Descripción |
| --- | --- | --- |
| `POST` | `/api/merge` | Une varios PDF recibidos como `files`. |
| `POST` | `/api/pdf-info` | Devuelve el número de páginas de un PDF. |
| `POST` | `/api/split` | Extrae un rango mediante `start_page` y `end_page`. |
| `POST` | `/api/images-to-pdf` | Convierte las imágenes recibidas como `images`. |
| `POST` | `/api/word-to-pdf` | Convierte un archivo `.doc` o `.docx`. |

Los endpoints que generan archivos aceptan el campo opcional `output_name`
para indicar el nombre de la descarga. Las peticiones de archivos usan
`multipart/form-data`.

La documentación interactiva generada por FastAPI está disponible en
[`/docs`](http://localhost:8000/docs) cuando el servidor está en ejecución.
También se puede consultar el esquema OpenAPI en
[`/openapi.json`](http://localhost:8000/openapi.json).

## Estructura del proyecto

```text
app/
  main.py             # Aplicación FastAPI y endpoints
static/               # JavaScript y estilos de la interfaz
templates/            # Páginas HTML
Dockerfile            # Imagen de producción
docker-compose.yml    # Servicio para Docker Compose
requirements.txt      # Dependencias de Python
```

## Consideraciones de despliegue

El servidor está configurado para uso local y para ejecutarse detrás de un
contenedor. Antes de exponerlo públicamente conviene añadir, según el entorno,
autenticación, límites de tamaño y frecuencia de subida, HTTPS y una política
de gestión de errores y registros. Los documentos subidos pueden contener
información sensible: utiliza el servicio únicamente en un entorno de
confianza y revisa la configuración de red del despliegue.
