# Unir PDF

Servicio web para seleccionar, reordenar y unir cualquier cantidad de archivos PDF.

El fichero `main.py` original se conserva como versión de escritorio. La aplicación web vive en `app/` y usa FastAPI y `pypdf`.

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

También puedes arrancarlo directamente con:

```powershell
c:\python314\python.exe app\main.py
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

Los PDFs se envían al endpoint `/api/merge`, se procesan en una carpeta temporal gestionada por FastAPI y el resultado se devuelve como descarga. No se guarda ningún PDF en la aplicación después de responder.
