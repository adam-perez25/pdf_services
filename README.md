# Unir PDF

Servicio web para trabajar con archivos PDF desde el navegador: unir varios documentos o dividir uno por páginas y rangos personalizados.

El fichero `main.py` original se conserva como versión de escritorio. La aplicación web vive en `app/` y usa FastAPI y `pypdf`.

La interfaz web ofrece cuatro servicios:

- **Unir PDF:** selecciona varios archivos, cambia su orden y descarga un único PDF.
- **Dividir PDF:** selecciona un archivo y descarga los resultados como PDFs independientes, sin ZIP. Puedes crear un PDF por página o indicar rangos como `1-3, 4-6`, con un nombre diferente para cada rango.
- **Imágenes a PDF:** selecciona varias imágenes, cambia su orden y descarga un único PDF con una página por imagen.
- **Word a PDF:** convierte documentos `.doc` y `.docx` a PDF usando LibreOffice en modo headless.

## Uso actual: aplicación de escritorio

Mientras preparas el servidor, utiliza la aplicación original con su interfaz gráfica:

```powershell
cd C:\Users\Usuario\Desktop\proyectos\unirPDF
c:\python314\python.exe main.py
```

Esta versión no necesita Uvicorn ni Docker.

## Ejecutar localmente en Windows

Con las dependencias ya instaladas en tu equipo, abre PowerShell y ejecuta exactamente:

```powershell
cd C:\Users\Usuario\Desktop\proyectos\unirPDF
c:\python314\python.exe -m uvicorn app.main:app
```

Deja esa terminal abierta mientras uses la aplicación. Cuando aparezca este mensaje:

```text
Uvicorn running on http://127.0.0.1:8000
```

abre esta dirección en el navegador:

```text
http://127.0.0.1:8000
```

Para detener el servidor, vuelve a la terminal y pulsa `Ctrl + C`.

### Si todavía no has instalado las dependencias

Ejecuta una vez este comando desde la carpeta del proyecto:

```powershell
c:\python314\python.exe -m pip install -r requirements.txt
```

Usamos `python.exe -m uvicorn` en lugar de `uvicorn` porque el ejecutable de Uvicorn no está añadido al `PATH` de PowerShell.

### Modo desarrollo

Si quieres que el servidor se reinicie automáticamente al modificar archivos:

```powershell
c:\python314\python.exe -m uvicorn app.main:app --reload
```

## Ejecutar con Docker

Después de instalar e iniciar Docker Desktop, comprueba que está disponible:

```powershell
docker --version
docker compose version
```

### Opción recomendada: Docker Compose

```powershell
cd C:\Users\Usuario\Desktop\proyectos\unirPDF
docker compose up --build
```

Abre `http://localhost:8000`.

Para detener el contenedor:

```powershell
docker compose down
```

### Construir y ejecutar la imagen manualmente

Si quieres crear la imagen directamente:

```powershell
cd C:\Users\Usuario\Desktop\proyectos\unirPDF
docker build -t unir-pdf .
docker run --rm --name unir-pdf-web -p 8000:8000 unir-pdf
```

Abre `http://localhost:8000` en el navegador. Para detener el contenedor, pulsa `Ctrl + C`.

Los PDFs se procesan en memoria o en archivos temporales gestionados por FastAPI y se devuelven como descargas. No se guardan permanentemente en la aplicación después de responder.

Endpoints disponibles:

- `POST /api/merge`: une varios PDFs en el orden recibido.
- `POST /api/pdf-info`: obtiene el número de páginas de un PDF.
- `POST /api/split`: devuelve un PDF correspondiente al rango solicitado.
- `POST /api/images-to-pdf`: convierte imágenes ordenadas en páginas de un PDF.
- `POST /api/word-to-pdf`: convierte un documento Word en PDF.

En el modo de división por páginas se realizan varias descargas individuales. El navegador puede pedir permiso para permitir varias descargas del mismo sitio.
