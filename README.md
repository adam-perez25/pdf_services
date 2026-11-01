[![Tests](https://github.com/adam-perez25/pdf_services/actions/workflows/tests.yml/badge.svg)](https://github.com/adam-perez25/pdf_services/actions/workflows/tests.yml)

# PDF Toolkit

Aplicación web para trabajar con documentos PDF desde el navegador: unir,
dividir, organizar páginas, comprimir, proteger con contraseña, numerar
páginas, añadir marcas de agua, editar metadatos, hacer buscables los PDF
escaneados (OCR), extraer texto, firmar, convertir imágenes y documentos de
Word, Excel y PowerPoint a PDF, exportar páginas como imágenes y encadenar
varias de estas herramientas en un solo paso.

Está construida con [FastAPI](https://fastapi.tiangolo.com/),
[pypdf](https://pypi.org/project/pypdf/),
[pypdfium2](https://pypi.org/project/pypdfium2/),
[Pillow](https://pypi.org/project/Pillow/) y
[OCRmyPDF](https://ocrmypdf.readthedocs.io/), y usa LibreOffice, Ghostscript
y Tesseract como programas externos. La interfaz se sirve desde el mismo
servidor y no requiere un frontend independiente. Los archivos se procesan
durante la petición y se devuelven como descargas: la aplicación no guarda
documentos de forma permanente.

## Funcionalidades

En el mismo orden que en la página de inicio:

1. **Unir PDF:** combina varios archivos PDF en el orden seleccionado.
2. **Dividir PDF:** separa un PDF en una página por archivo o en rangos
   personalizados con nombre propio. Todo se genera en una sola petición y se
   descarga como un único ZIP (o como PDF si solo resulta un archivo).
3. **Imágenes a PDF:** convierte varias imágenes ordenadas en un único PDF.
   Cada página se ajusta al tamaño A4 en la orientación de la imagen; las
   imágenes pequeñas no se amplían. Se aceptan PNG, JPEG, WEBP, GIF, BMP y
   TIFF (otros formatos, como EPS, se rechazan), de hasta 100 millones de
   píxeles cada una. Las zonas transparentes quedan en blanco, las fotos del
   móvil se giran según su orientación EXIF, un TIFF de varias páginas da una
   página por cada una y de un GIF o WEBP animado se usa el primer fotograma.
4. **Word a PDF:** convierte archivos `.doc` y `.docx` con LibreOffice.
5. **Organizar páginas:** muestra una vista previa de cada página y permite
   girarlas, eliminarlas y reordenarlas (con los botones o arrastrándolas,
   también en pantallas táctiles manteniendo pulsada la página) antes de
   descargar el PDF resultante. Al pulsar una página se abre una vista
   ampliada en alta resolución.
6. **Comprimir PDF:** reduce el tamaño bajando la resolución de las imágenes
   con Ghostscript, ideal para documentos escaneados. Tres niveles: máxima
   (72 ppp), recomendada (150 ppp) y alta calidad (300 ppp). Si el resultado
   no es más pequeño, se descarga el original.
7. **Excel a PDF:** convierte archivos `.xls` y `.xlsx`; todas las hojas del
   libro pasan al PDF.
8. **PowerPoint a PDF:** convierte archivos `.ppt` y `.pptx`, con una página
   por diapositiva.

   En las tres conversiones de Office el PDF se llama como el archivo subido
   (se puede cambiar). Se rechazan los archivos vacíos y los
   `.docx`/`.xlsx`/`.pptx` que no son un documento de Office (ni ZIP ni el
   formato binario antiguo); los `.doc`/`.xls`/`.ppt` se aceptan tal cual,
   porque a menudo son RTF, HTML o CSV guardados con esa extensión.
9. **PDF a imágenes:** convierte las páginas de un PDF (todas, o solo las
   indicadas, por ejemplo `1-3, 5`) en imágenes PNG o JPG a 72, 150 o
   300 ppp. Las imágenes conservan el número real de página en el nombre y se
   descargan juntas en un ZIP (o como una sola imagen si se convierte una
   página). Antes de empezar comprueba que el resultado no sea demasiado
   grande para el servidor (ver [PDF a imágenes](#pdf-a-imágenes-límites)).
10. **Proteger PDF:** cifra un PDF con AES-256 para que solo se abra con
    contraseña, o quita la contraseña de un PDF protegido si se conoce. La
    contraseña solo se usa durante la petición: no se guarda ni se registra.
11. **Numerar y marca de agua:** añade a todas las páginas el número de
    página (formato `1`, `1 / N` o `Página 1 de N`, número inicial, tamaño y
    posición arriba o abajo, a la izquierda, centrado o a la derecha) o un
    texto de marca de agua centrado, horizontal o en diagonal, con opacidad
    configurable. El texto usa la fuente Helvetica estándar (admite tildes y
    eñes) y aparece derecho aunque las páginas estén giradas. Los números
    dejan sitio a los trazos descendentes (g, p) en páginas muy bajas, y la
    marca de agua se reduce para caber a lo ancho y a lo alto de la página.
12. **Editar metadatos:** muestra y cambia el título, el autor, el asunto y
    las palabras clave de un PDF, o elimina todos sus metadatos antes de
    compartirlo, para no revelar quién lo creó, con qué programa ni cuándo.
    Los cambios se escriben en la información clásica del documento y en un
    bloque XMP nuevo, porque muchos visores (Adobe, las propiedades de
    Windows) solo leen el XMP. «Eliminar todos» quita también los bloques XMP
    de cada página.
13. **OCR (hacer buscable un PDF escaneado):** reconoce el texto de las
    páginas escaneadas con OCRmyPDF y Tesseract y lo añade como una capa
    invisible, para poder buscar, seleccionar y copiar el texto sin cambiar el
    aspecto del documento. Idiomas: español (por defecto), inglés o ambos.
    Solo se procesan las páginas que contienen imágenes; las de texto digital
    se copian sin cambios. Una página escaneada se reconoce aunque ya tenga
    algo de texto (por ejemplo un número de página añadido), que se conserva,
    y una capa OCR anterior se rehace. Las páginas escaneadas de lado o boca
    abajo se enderezan. Tarda varios segundos por página; admite hasta
    `OCR_MAX_PAGES` páginas (2000 por defecto).
14. **Extraer texto:** descarga el texto de un PDF como archivo `.txt`
    (UTF-8), de todas las páginas o de un rango (`1-3, 5`), con un encabezado
    `--- Página N ---`, un salto de página o nada entre páginas, y muestra una
    vista previa con el número de caracteres y páginas. Los PDF escaneados no
    tienen texto: hay que pasarlos antes por OCR.
15. **Firmar PDF:** dibuja la firma con el ratón o el dedo (tinta negra o
    azul) o sube una imagen PNG (admite fondo transparente) o JPEG, y colócala
    sobre la vista previa de las páginas que quieras: se arrastra, se cambia
    de tamaño sin deformarla y también se mueve con las flechas del teclado.
    Es una firma **visual** (una imagen sobre la página), no una firma digital
    certificada: no acredita la identidad de quien firma ni protege el
    documento frente a cambios. Funciona aunque las páginas estén giradas.
16. **Flujo de trabajo:** encadena varias herramientas de una sola vez (OCR,
    numerar páginas, marca de agua, comprimir, metadatos y proteger) y
    descarga solo el resultado final. Algunos pasos tienen un sitio fijo y
    solo se pueden reordenar los demás:
    - si se eligen varios PDF, el primer paso es siempre unirlos;
    - el OCR va el primero de los pasos (así el texto que añaden la
      numeración o la marca de agua no tapa el escaneo ni se reconoce);
    - los metadatos van después de los pasos normales (comprimir y el OCR
      escriben su propio productor, fechas y XMP, que así también se borran);
    - «Proteger con contraseña» va siempre al final.

    El navegador llama uno tras otro a los mismos endpoints de la API, así que
    cada paso aplica sus propias validaciones y límites; si un paso falla, se
    muestra su error y se puede descargar el resultado del último paso
    correcto.

La interfaz tiene modo oscuro automático: sigue la preferencia de tema claro
u oscuro del sistema operativo.

## Requisitos

Con Docker (recomendado):

- Docker Desktop, o Docker Engine con el plugin Docker Compose.

Para ejecutarlo directamente con Python:

- Python 3.12 (la versión de la imagen de Docker).
- LibreOffice (Writer, Calc e Impress), con `soffice` en el `PATH`, para
  **Word**, **Excel** y **PowerPoint a PDF**.
- Ghostscript (`gs`, o `gswin64c` / `gswin32c` en Windows) en el `PATH` para
  **Comprimir PDF**; OCRmyPDF también lo usa.
- Tesseract con los idiomas español e inglés (`tesseract` en el `PATH`) para
  el **OCR**; el paquete `ocrmypdf` ya está en `requirements.txt`.

Si falta alguno de estos programas, solo dejan de funcionar las herramientas
que lo necesitan (responden `503`); las demás siguen disponibles.
`GET /health` indica cuáles se han detectado.

## Ejecución con Docker

Docker Compose instala también LibreOffice, Ghostscript y Tesseract, así que
no hay que configurar nada en el sistema.

```bash
# Construir la imagen y arrancar el servicio en segundo plano
docker compose up --build -d

# Comprobar el estado (aparece como "healthy" cuando responde)
docker compose ps
curl http://localhost:8000/health

# Ver el registro (errores, detalles técnicos de los fallos)
docker compose logs -f

# Detener y eliminar el contenedor
docker compose down
```

Después abre [http://localhost:8000](http://localhost:8000). Tras actualizar
el código, vuelve a ejecutar `docker compose up --build -d` para reconstruir
la imagen. El servicio se reinicia solo si se cae o al reiniciar Docker
(`restart: unless-stopped`).

- **Puerto:** si el `8000` está ocupado, cambia el mapeo en
  `docker-compose.yml`; por ejemplo, `"8080:8000"` sirve la aplicación en
  `http://localhost:8080`.
- **Imagen:** se basa en `python:3.12-slim` e instala LibreOffice (Writer,
  Calc e Impress), las fuentes Liberation, Ghostscript y Tesseract (español e
  inglés). Ocupa unos 1,3 GB, de los que Tesseract y OCRmyPDF suponen unos
  170 MB. El servicio se ejecuta con un usuario sin privilegios (`app`, UID
  `10001`), no como `root`.
- **Healthcheck:** la imagen consulta `/health` cada 30 segundos;
  `docker compose ps` muestra `unhealthy` si el servicio deja de responder o
  no puede guardar archivos temporales.
- **Memoria, límites y endurecimiento:** `docker-compose.yml` limita el
  contenedor a 3 GB (`mem_limit: 3g`), define los límites en `environment:`
  (ver [Configuración](#configuración)) y aplica las opciones de
  [Seguridad y privacidad](#seguridad-y-privacidad) (`init`, `cap_drop`,
  `no-new-privileges`, `pids_limit`).

Sin Compose también funciona; los límites se pasan con `-e` y la memoria con
`--memory` (las opciones de endurecimiento de Compose tienen equivalentes en
`docker run`: `--init`, `--cap-drop ALL`, `--security-opt
no-new-privileges:true`, `--pids-limit 512`):

```bash
docker build -t pdf-toolkit .
docker run --rm --name pdf-toolkit-web -p 8000:8000 --memory=3g pdf-toolkit
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

3. Inicia el servidor (con `--reload` se reinicia al detectar cambios):

   ```bash
   python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
   ```

4. Abre [http://127.0.0.1:8000](http://127.0.0.1:8000).

El puerto se elige con `--port`. Las variables de entorno de
[Configuración](#configuración) también se aplican aquí.

## Configuración

La aplicación no necesita ninguna configuración para arrancar. Los límites y
tiempos máximos se pueden cambiar con variables de entorno. Con Docker están
todas en la sección `environment:` de `docker-compose.yml` con sus valores
por defecto: edita el valor y aplica el cambio con

```bash
docker compose up -d
```

Se leen una sola vez al arrancar. Si una variable no es un número entero o
está fuera de rango, el servicio no arranca y `docker compose logs` indica
qué variable falla.

### Variables de entorno

| Variable | Por defecto | Rango | Descripción |
| --- | --- | --- | --- |
| `MAX_UPLOAD_MB` | 500 MB | 1-100000 | Tamaño máximo de cada archivo subido; si se supera responde `413`. |
| `MAX_TOTAL_UPLOAD_MB` | 1000 MB | 1-100000 | Tamaño máximo de todos los archivos de una petición (**Unir PDF**, **Imágenes a PDF**); si se supera responde `413`. No puede ser menor que `MAX_UPLOAD_MB` (el servicio no arranca); si no se indica, vale 1000 o `MAX_UPLOAD_MB` si este es mayor. |
| `OFFICE_TIMEOUT_SECONDS` | 300 s | 10-86400 | Tiempo máximo de una conversión con LibreOffice. |
| `COMPRESSION_TIMEOUT_SECONDS` | 600 s | 10-86400 | Tiempo base de la compresión con Ghostscript. |
| `COMPRESSION_SECONDS_PER_PAGE` | 2 s | 0-600 | Tiempo extra por página: el límite es base + páginas × este valor (20 páginas: 640 s; 1000 páginas: 2600 s). |
| `COMPRESSION_MAX_TIMEOUT_SECONDS` | 3600 s | 10-604800 | Tope del tiempo de compresión (nunca menor que el tiempo base). |
| `OCR_MAX_PAGES` | 2000 | 1-100000 | Páginas máximas de un PDF para el OCR; si se supera responde `400`. |
| `OCR_TIMEOUT_SECONDS` | 60 s | 10-86400 | Tiempo base del OCR. |
| `OCR_SECONDS_PER_PAGE` | 20 s | 0-3600 | Tiempo extra del OCR por página. |
| `OCR_MAX_TIMEOUT_SECONDS` | 14400 s (4 h) | 10-604800 | Tope del tiempo del OCR. |
| `PDF_TO_IMAGES_MAX_MEGAPIXELS` | 10000 | 1-1000000 | Millones de píxeles en total que puede generar **PDF a imágenes**; si se supera responde `400` antes de empezar. |
| `PDF_TO_IMAGES_MIN_FREE_MB` | 512 MB | 0-1000000 | Espacio que debe quedar libre en el disco temporal tras el tamaño estimado de las imágenes; si no cabe responde `507` antes de empezar. |
| `PDF_TO_IMAGES_TIMEOUT_SECONDS` | 300 s | 10-86400 | Tiempo base de **PDF a imágenes**. |
| `PDF_TO_IMAGES_SECONDS_PER_PAGE` | 3 s | 0-600 | Tiempo extra por página convertida (20 páginas: 360 s; 1000 páginas: 3300 s). |
| `PDF_TO_IMAGES_MAX_TIMEOUT_SECONDS` | 7200 s | 10-604800 | Tope del tiempo de **PDF a imágenes** (nunca menor que el tiempo base). |
| `DOCUMENT_TTL_SECONDS` | 1800 s (30 min) | 60-604800 | Tiempo sin uso tras el que se borran los documentos de **Organizar páginas** y **Firmar PDF** y los archivos temporales que hayan quedado. |
| `DOCUMENT_SWEEP_SECONDS` | 300 s (5 min) | 10-86400 | Frecuencia de esa limpieza. |
| `HEAVY_JOB_BUDGET_MB` | 2000 MB | 100-1000000 | Memoria estimada que pueden ocupar a la vez los trabajos grandes (ver [Memoria](#memoria-y-trabajos-grandes)). |
| `HEAVY_JOB_WAIT_SECONDS` | 300 s | 1-86400 | Tiempo máximo que un trabajo grande espera su turno; después responde `503` con `Retry-After`. |
| `HEAVY_JOB_MAX_WAITING` | 10 | 1-1000 | Trabajos grandes que pueden esperar turno a la vez; con tantos esperando, el siguiente recibe `503` al momento. |

Cuando una conversión, compresión, OCR o **PDF a imágenes** supera su tiempo
máximo responde `504`; se termina el proceso externo junto con todos los
procesos que haya iniciado y se borran sus archivos temporales. La limpieza
periódica nunca borra los archivos de un trabajo que sigue en curso, aunque
tarde más que `DOCUMENT_TTL_SECONDS`.

### Memoria y trabajos grandes

El contenedor tiene un límite de 3 GB (`mem_limit: 3g` en
`docker-compose.yml`). Con los valores por defecto se pueden procesar PDF de
más de 1000 páginas y varios cientos de MB. Como referencia, con un PDF de
200 MB y 1000 páginas (medido en un PC de sobremesa) unir dos copias tarda
unos 9 s y usa unos 500 MB de memoria; dividir, organizar, numerar o editar
metadatos, 3-5 s y unos 300 MB; protegerlo, unos 650 MB; comprimirlo, unos
20 s. Los resultados se escriben en archivos temporales y se envían desde el
disco, así que el disco temporal necesita espacio para la subida y el
resultado.

Para que dos archivos enormes procesados a la vez no agoten la memoria, cada
herramienta estima la memoria que necesitará a partir del tamaño de la subida
(por ejemplo, 1,5 veces el archivo al dividir o numerar, 3,5 veces al
proteger, más una cantidad fija para Ghostscript, Tesseract o LibreOffice; la
tabla está en `HEAVY_JOB_ESTIMATES`) y la reserva de un presupuesto común,
`HEAVY_JOB_BUDGET_MB`:

- Si no cabe, la petición espera su turno por orden de llegada. Un trabajo
  mayor que todo el presupuesto se ejecuta cuando no hay ningún otro.
- Los archivos pequeños nunca esperan, sea cual sea la herramienta: si la
  subida suma menos de 50 MB (`HEAVY_JOB_SMALL_FILES_MB`) o la estimación es
  menor de 100 MB (`HEAVY_JOB_MIN_MB`). La excepción son las imágenes de
  **Imágenes a PDF** y **Firmar PDF**: su estimación cuenta los píxeles
  (8 bytes por píxel al decodificarlas), así que una imagen pequeña en disco
  pero enorme en píxeles espera como un trabajo grande.
- Si el turno no llega en `HEAVY_JOB_WAIT_SECONDS`, o ya hay
  `HEAVY_JOB_MAX_WAITING` peticiones esperando, la respuesta es `503`
  («El servidor está ocupado con otros documentos grandes...») con la
  cabecera `Retry-After: 60`.
- `GET /health` muestra en `heavy_jobs` cuántos trabajos se están ejecutando
  (`running`), la memoria reservada (`reserved_mb`) y cuántos esperan
  (`waiting`).
- Unos segundos después de terminar un trabajo grande, el servidor devuelve
  al sistema la memoria que ha quedado libre (`malloc_trim` de glibc, solo en
  Linux), así que el contenedor en reposo no se queda con el tamaño del
  último trabajo grande.

El presupuesto de 2000 MB deja ~1 GB de los 3 GB para Python y los trabajos
pequeños. Si subes `MAX_UPLOAD_MB`, sube también `mem_limit` (las operaciones
más pesadas usan 2-3 veces el tamaño del archivo) y `HEAVY_JOB_BUDGET_MB` en
la misma proporción, manteniendo ese margen de ~1 GB. Cada petición en
espera ocupa uno de los hilos del servidor (unos 40), lo que sobra para un
uso personal.

### PDF a imágenes: límites

Convertir muchas páginas a 300 ppp puede ocupar decenas de GB y tardar horas,
así que **PDF a imágenes** comprueba antes de empezar:

- **Selección de páginas:** el campo `pages` (`1-3, 5`; vacío = todas, como
  máximo 1000 caracteres) limita la conversión a esas páginas; una página
  repetida se convierte una vez.
- **Píxeles (`400`):** la suma de píxeles de las páginas elegidas a la
  resolución elegida no puede superar `PDF_TO_IMAGES_MAX_MEGAPIXELS`. Una
  página A4 son unos 2,2 millones de píxeles a 150 ppp y 8,7 a 300 ppp: con
  el valor por defecto se admiten 1000 páginas A4 a 300 ppp, pero no 2000.
  Además, cada página se renderiza como máximo a 40 millones de píxeles
  (`MAX_IMAGE_PIXELS`); las páginas muy grandes salen a menor resolución.
- **Disco (`507`):** el tamaño estimado (unos 0,5 bytes por píxel en PNG y
  0,2 en JPG) más `PDF_TO_IMAGES_MIN_FREE_MB` debe caber en el disco
  temporal. Si el disco se llena igualmente durante la conversión, se cancela
  con `507`.
- **Tiempo (`504`):** base + páginas × `PDF_TO_IMAGES_SECONDS_PER_PAGE`, con
  tope `PDF_TO_IMAGES_MAX_TIMEOUT_SECONDS`; se comprueba entre página y
  página.

El mensaje de error indica las páginas, el tamaño estimado y qué cambiar
(menos páginas, menor resolución o JPG).

### Valores fijos

Definidos como constantes en `app/main.py` (no se cambian con variables de
entorno):

| Constante | Valor | Descripción |
| --- | --- | --- |
| `MULTIPART_OVERHEAD_MB` | 1 MB | Margen para los campos del formulario. Una petición mayor que `MAX_TOTAL_UPLOAD_MB` + este margen se rechaza con `413` antes de guardarse en disco (por su `Content-Length`, o en cuanto lo supera si no lo indica). |
| `MAX_OUTPUT_NAME_LENGTH` | 150 | Caracteres máximos del nombre de descarga (`output_name`, `base_name`), sin contar la extensión. |
| `IMAGE_MAX_PIXELS` | 100 000 000 | Píxeles máximos de cada imagen subida (**Imágenes a PDF**, **Firmar PDF**), leídos de la cabecera antes de decodificarla; si se supera responde `400`. También es el límite antibomba de Pillow. |
| `DECODED_BYTES_PER_PIXEL` | 8 | Memoria por píxel con la que se estima la decodificación de una imagen subida. |
| `MAX_IMAGE_PIXELS` | 40 000 000 | Píxeles máximos por imagen generada en **PDF a imágenes**. |
| `IMAGE_BYTES_PER_PIXEL` | PNG 0,5 / JPG 0,2 | Bytes por píxel para estimar el tamaño de **PDF a imágenes**. |
| `PAGE_SELECTION_MAX_LENGTH` | 1000 | Caracteres máximos del campo `pages` de **PDF a imágenes** y **Extraer texto**. |
| `PAGE_LIST_FACTOR` / `PAGE_LIST_MIN_LIMIT` | 10 / 100 | Las listas de páginas que pueden repetir páginas (rangos de **Extraer texto** y de **Dividir** en ZIP, lista de **Organizar páginas**) suman como máximo 10 veces las páginas del PDF, y nunca menos de 100; si se supera responde `400`. |
| `SPLIT_MAX_RANGES` | 500 | Rangos máximos de `/api/split-zip` en modo `ranges`. |
| `THUMBNAIL_MAX_PIXELS` / `THUMBNAIL_MAX_SIDE` | 8 000 000 / 16 000 px | Tope de las miniaturas de **Organizar páginas** y **Firmar PDF**: una página enorme (por ejemplo 500 × 200 000 pt) se dibuja más pequeña. |
| `PDF_HEADER_SEARCH_BYTES` | 1024 | **Comprimir PDF** y **OCR** buscan la cabecera `%PDF-` en estos primeros bytes; lo que haya delante se elimina antes de pasar el archivo a Ghostscript u OCRmyPDF, y sin cabecera responde `400`. |
| `MAX_PASSWORD_BYTES` | 127 | Longitud máxima de la contraseña en **Proteger PDF**. |
| `STAMP_NUMBER_SIZE_RANGE` | 6-72 pt | Tamaños de letra de los números de página. |
| `STAMP_WATERMARK_SIZE_RANGE` | 8-200 pt | Tamaños de letra de la marca de agua (se reduce si el texto no cabe). |
| `STAMP_WATERMARK_MAX_LENGTH` | 100 | Longitud máxima del texto de la marca de agua. |
| `STAMP_MAX_START_NUMBER` | 100 000 | Número inicial máximo de la numeración. |
| `STAMP_MARGIN` | 28 pt | Distancia de los números de página al borde. |
| `MAX_METADATA_LENGTH` | 500 | Longitud máxima de cada campo en **Editar metadatos**. |
| `SIGNATURE_MAX_MB` | 10 MB | Tamaño máximo de la imagen de la firma (`413` si se supera). |
| `SIGNATURE_MAX_SOURCE_PIXELS` | 40 000 000 | Píxeles máximos de la imagen de la firma subida. |
| `SIGNATURE_MAX_SIDE` | 2000 px | La firma se reduce a este lado máximo antes de incrustarla. |
| `SIGNATURE_MAX_PLACEMENTS` | 200 | Firmas colocadas como máximo en una petición. |
| `OCR_LANGUAGES` | `spa`, `eng`, `spa+eng` | Idiomas admitidos por el OCR. |
| `OCR_JOBS` | 2 | Páginas que Tesseract procesa a la vez en cada OCR. |
| `HEAVY_JOB_SMALL_FILES_MB` | 50 MB | Las subidas que suman menos nunca esperan turno (salvo por los píxeles de una imagen). |
| `HEAVY_JOB_MIN_MB` | 100 MB | Los trabajos con una estimación menor no reservan memoria ni esperan. |
| `HEAVY_JOB_RETRY_AFTER_SECONDS` | 60 s | Valor de `Retry-After` cuando el servidor está ocupado. |

## Interfaz web

Cada herramienta tiene su propia página: `/` (inicio), `/merge`, `/split`,
`/images`, `/word`, `/pages`, `/compress`, `/excel`, `/powerpoint`,
`/pdf-to-images`, `/protect`, `/stamp`, `/metadata`, `/ocr`, `/extract-text`,
`/sign` y `/workflow`. El flujo de trabajo no tiene endpoint propio: encadena
los de las demás herramientas desde el navegador.

## API

Las peticiones con archivos usan `multipart/form-data`. Los endpoints que
generan archivos aceptan el campo opcional `output_name` para el nombre de la
descarga. La documentación interactiva de FastAPI (`/docs`, `/redoc` y
`/openapi.json`) está desactivada (la política de seguridad de contenido
estricta la rompería): la API se describe en esta tabla.

| Método | Ruta | Descripción |
| --- | --- | --- |
| `GET` | `/health` | Estado del servicio: `status`, `storage` (puede guardar archivos temporales), `libreoffice`, `ghostscript` y `ocr` (OCRmyPDF y Tesseract) disponibles, y `heavy_jobs` (`running`, `reserved_mb`, `waiting`). Responde `503` solo si no puede guardar archivos. |
| `POST` | `/api/merge` | Une los PDF recibidos como `files`, en ese orden. |
| `POST` | `/api/pdf-info` | Devuelve `{"pages": N}` con el número de páginas de un PDF (`file`). |
| `POST` | `/api/split` | Extrae las páginas de `start_page` (por defecto 1) a `end_page` (sin él, hasta la última). `0`, negativos o un rango fuera del PDF responden `400`. |
| `POST` | `/api/split-zip` | Divide un PDF y devuelve un ZIP. `mode=pages` (por defecto) crea un PDF por página llamado `<base_name>-page-001.pdf` (cuatro cifras con 1000 páginas o más). `mode=ranges` usa `ranges` (`1-3, 4-6`; hasta 500 rangos que sumen como máximo 10 veces las páginas del PDF, mínimo 100) y `names`, una lista JSON con un nombre por rango: un nombre vacío o `null` usa `split-INICIO-FIN`, sin `names` (o `null`) todos usan ese nombre, una lista de otra longitud o con otros tipos responde `400`, y los nombres repetidos reciben `-2`, `-3`... Si solo resulta un archivo, devuelve el PDF directamente. |
| `POST` | `/api/images-to-pdf` | Convierte las imágenes recibidas como `images` en un PDF, en ese orden (transparencia sobre blanco, orientación EXIF, una página por fotograma de un TIFF, primer fotograma de un GIF/WEBP animado). `400` si una imagen supera 100 millones de píxeles. |
| `POST` | `/api/pdf-to-images` | Convierte páginas de un PDF en imágenes: `image_format` `png` (por defecto) o `jpg`; `dpi` `72`, `150` (por defecto) o `300`; `pages` con la sintaxis `1-3, 5` (vacío = todas); `base_name` para los nombres (`<base_name>-page-001.png`, con cuatro cifras si el PDF tiene 1000 páginas o más). Devuelve un ZIP, o la imagen si es una sola página. `400`/`507`/`504` según [los límites](#pdf-a-imágenes-límites). |
| `POST` | `/api/word-to-pdf` | Convierte un `.doc` o `.docx` (`file`). Sin `output_name`, el PDF se llama como el archivo subido. `400` si está vacío o si un `.docx` no es ZIP ni OLE. |
| `POST` | `/api/excel-to-pdf` | Convierte un `.xls` o `.xlsx` (mismas reglas). |
| `POST` | `/api/powerpoint-to-pdf` | Convierte un `.ppt` o `.pptx` (mismas reglas). |
| `POST` | `/api/compress` | Comprime un PDF; `level` es `strong`, `recommended` (por defecto) o `high`. Las cabeceras `X-Original-Size`, `X-Compressed-Size` y `X-Compression-Applied` indican el resultado. |
| `POST` | `/api/protect` | Cifra un PDF con AES-256 usando `password` (obligatoria, máximo 127 bytes). `400` si el PDF ya estaba protegido. |
| `POST` | `/api/unlock` | Quita la contraseña usando `password` (puede ir vacía si el PDF solo tiene restricciones de permisos). `400` con `Contraseña incorrecta` si no coincide, o si el PDF no estaba protegido. |
| `POST` | `/api/stamp` | Numera las páginas (`mode=numbers`, por defecto) o añade una marca de agua (`mode=watermark`). Números: `position` (`bottom-center` por defecto, `bottom-left`, `bottom-right`, `top-center`, `top-left`, `top-right`), `number_format` (`n` → `1`, `n-of-total` → `1 / N`, `page-n-of-total` → `Página 1 de N`; N es el último número), `start_number` (0-100000, por defecto 1) y `font_size` (6-72, por defecto 12). Marca de agua: `text` (obligatorio, hasta 100 caracteres, sin emojis ni otros alfabetos), `font_size` (8-200, por defecto 60), `opacity` (0.1-1, por defecto 0.3) y `rotation` (`45` por defecto o `0`; también `45.0`). La descarga se llama `<nombre>-numerado.pdf` o `<nombre>-marca-de-agua.pdf`. |
| `POST` | `/api/metadata/read` | Devuelve en JSON los metadatos de un PDF (`title`, `author`, `subject`, `keywords`, `creator`, `producer`, `creation_date`, `modification_date`; `null` si faltan) y su número de páginas (`pages`). |
| `POST` | `/api/metadata` | Escribe `title`, `author`, `subject` y `keywords` (máximo 500 caracteres cada uno; un campo vacío o ausente se elimina), sustituye el bloque XMP por uno nuevo con los mismos valores y borra las entradas no estándar (se conservan creador, productor, fechas y `/Trapped`). Con `remove_all=true` elimina todos los metadatos, incluidos el XMP del documento y el de cada página. La descarga se llama `<nombre>-metadatos.pdf`. |
| `POST` | `/api/ocr` | Hace buscable un PDF escaneado; `language` es `spa` (por defecto), `eng` o `spa+eng`. Solo procesa las páginas con imágenes (`--redo-ocr --pages ...`: conserva el texto visible y rehace una capa OCR anterior) y endereza las giradas (`--rotate-pages`); las páginas de texto digital se copian sin cambios. Con formularios rellenables, o si OCRmyPDF rechaza `--redo-ocr`, usa `--skip-text`. Máximo `OCR_MAX_PAGES` páginas. La descarga se llama `<nombre>-ocr.pdf`. |
| `POST` | `/api/extract-text` | Devuelve el texto como `text/plain; charset=utf-8` (`<nombre>.txt`). `pages` admite rangos (`1-3, 5`; vacío = todas): se extraen en el orden indicado y una página repetida se extrae otra vez (hasta 10 veces las páginas del PDF, mínimo 100, y 1000 caracteres). `page_separator` es `header` (por defecto, línea `--- Página N ---`), `formfeed` o `none`. Las cabeceras `X-Page-Count` y `X-Character-Count` indican páginas y caracteres. `400` si el PDF no contiene texto (escaneado: usar OCR antes). |
| `POST` | `/api/documents` | Sube un PDF una sola vez (para **Organizar páginas** y **Firmar PDF**) y devuelve `id` y `pages`. |
| `GET` | `/api/documents/{id}/pages/{n}` | Devuelve la página `n` como JPEG; `width` controla el ancho (100-2000 px, por defecto 300), con el tope de píxeles de las miniaturas. |
| `POST` | `/api/documents/{id}/edit` | Genera el PDF editado a partir de `pages` (mismo formato que `/api/edit-pages`) sin volver a subir el archivo; después borra el documento subido. |
| `POST` | `/api/documents/{id}/sign` | Firma el documento subido: `signature` es la imagen (PNG o JPEG; la transparencia del PNG se conserva) y `placements` una lista JSON como `[{"page": 1, "x": 0.6, "y": 0.8, "width": 0.3, "height": 0.08}]`, con la caja en fracciones (0-1) de la página tal como se ve (origen arriba a la izquierda, ya girada). Hasta 200 cajas, dentro de la página. Devuelve `firmado.pdf` y después borra el documento subido. |
| `DELETE` | `/api/documents/{id}` | Elimina el documento subido (responde `204`). |
| `POST` | `/api/edit-pages` | Genera un PDF a partir de `file` y `pages`, una lista JSON como `[{"page": 3, "rotation": 90}]`; las páginas omitidas se eliminan. `page` y `rotation` deben ser números enteros (`1.7` o `true` responden `400`), la rotación múltiplo de 90 (el giro resultante se guarda entre 0 y 270) y la lista como máximo 10 veces las páginas del PDF (mínimo 100). |

Los documentos subidos a `/api/documents` se borran en cuanto se descarga el
PDF editado o firmado, al cerrar la página, tras `DOCUMENT_TTL_SECONDS` sin
uso (30 minutos por defecto) y al iniciar el servidor. Si se sigue editando
después de descargar, la página vuelve a subir el archivo automáticamente.

**Nombres de descarga.** Conservan tildes, eñes y letras de cualquier
alfabeto; las comillas, los caracteres de control y los que Windows no admite
(`<>:"/\|?*`) se sustituyen por `_`. Si el nombre no es ASCII,
`Content-Disposition` lleva un nombre ASCII de reserva
(`filename="Informe ano.pdf"`) y el nombre real en
`filename*=UTF-8''Informe%20a%C3%B1o.pdf`, que es el que usan los navegadores.

## Códigos de error

Los errores devuelven un JSON con `detail` en español. El detalle técnico no
se envía al navegador: queda en el registro (`docker compose logs`).

| Código | Cuándo |
| --- | --- |
| `400` | Archivo o parámetros no válidos: extensión, archivo que no es un PDF, rango de páginas, opción desconocida, contraseña incorrecta, lista de páginas demasiado larga, imagen de más de 100 millones de píxeles, PDF sin texto en **Extraer texto**, más de `OCR_MAX_PAGES` páginas en el OCR o demasiados píxeles en **PDF a imágenes**. También un campo que falta o con un tipo erróneo (por ejemplo `dpi=abc`): en lugar del `422` en inglés de FastAPI se responde «Falta el campo «dpi» o su valor no es válido.». Y un PDF protegido con contraseña (hay que desbloquearlo antes; los que solo tienen restricciones de permisos se procesan con normalidad). |
| `403` | Petición `POST` o `DELETE` a `/api/` enviada desde una página de otro sitio (ver [Seguridad](#seguridad-y-privacidad)). |
| `404` | Identificador de documento no válido, o página fuera de rango en `/api/documents/{id}/pages/{n}`. |
| `410` | El documento de **Organizar páginas** o **Firmar PDF** ha caducado (o el servidor se reinició); la página lo vuelve a subir. |
| `413` | Un archivo supera `MAX_UPLOAD_MB`, el conjunto supera `MAX_TOTAL_UPLOAD_MB`, o la firma supera 10 MB. |
| `503` | Falta LibreOffice, Ghostscript, OCRmyPDF o Tesseract (o un idioma de Tesseract), o el servidor está ocupado con otros trabajos grandes (con `Retry-After`). En `/health`, no se pueden guardar archivos temporales. |
| `504` | La conversión de Office, la compresión, el OCR o **PDF a imágenes** tardó más que su tiempo máximo. |
| `507` | **PDF a imágenes** no cabe en el disco temporal (según la estimación previa, o porque el disco se llenó durante la conversión). |

## Seguridad y privacidad

El servicio está pensado para uso personal en un equipo o red de confianza:
no tiene autenticación ni HTTPS. Antes de exponerlo a Internet habría que
añadirlos, junto con límites de frecuencia.

- **Nada se guarda:** no hay base de datos. Los archivos se procesan en una
  carpeta temporal y se borran al enviar la descarga; lo que quede (por
  ejemplo, una descarga interrumpida) lo borra la limpieza periódica tras
  `DOCUMENT_TTL_SECONDS`, y la carpeta se vacía al arrancar.
- **Contraseñas:** las de **Proteger PDF** solo se usan durante la petición;
  no se guardan, no se registran ni aparecen en los mensajes de error.
- **Peticiones de otros sitios (`403`):** las peticiones `POST` y `DELETE` a
  `/api/` que el navegador marca como enviadas desde otro sitio
  (`Sec-Fetch-Site: cross-site` o `same-site`, lo que incluye otra aplicación
  en otro puerto de `localhost`), o cuyo `Origin` no coincide con el `Host`,
  se rechazan. Así una página web cualquiera no puede subir archivos al
  servicio ni borrar documentos. Las propias páginas del servicio y las
  peticiones sin esas cabeceras (curl, scripts) funcionan igual. Detrás de un
  proxy inverso, el proxy debe conservar la cabecera `Host` original.
- **LibreOffice sin enlaces externos:** cada conversión usa un perfil nuevo
  con `BlockUntrustedRefererLinks`, sin contenido activo (OLE, DDE), sin
  actualizar vínculos y con un proxy inexistente para HTTP(S). Una imagen
  enlazada a `http://...` no se descarga y una enlazada a `file:///...` no se
  incrusta en el PDF.
- **Ghostscript solo recibe PDF:** **Comprimir PDF** y **OCR** quitan lo que
  haya antes de la cabecera `%PDF-` (un programa PostScript delante de un PDF
  válido se ejecutaría) y rechazan archivos sin cabecera. Ghostscript se
  ejecuta con `-dSAFER`.
- **Archivos no fiables:** las subidas tienen tamaño máximo y se rechazan
  antes de guardarse si lo superan; Pillow solo abre los formatos de imagen
  admitidos (no EPS) y con un tope de píxeles; las listas de páginas, las
  miniaturas y los nombres de descarga tienen límites (ver
  [Valores fijos](#valores-fijos)).
- **Contenedor:** se ejecuta con el usuario `app` (UID `10001`), con el
  código de solo lectura y memoria limitada a 3 GB. `docker-compose.yml`
  añade `init: true` (recoge los procesos zombi de LibreOffice, Ghostscript o
  Tesseract), `cap_drop: [ALL]`, `no-new-privileges:true` y
  `pids_limit: 512`.
- **Cabeceras de seguridad:** todas las respuestas llevan
  `Content-Security-Policy` (solo recursos del propio servidor, sin scripts
  en línea), `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY` y
  `Referrer-Policy: no-referrer`. Los archivos de `/static` se sirven con
  `Cache-Control: no-cache` para que el navegador recoja la versión nueva tras
  reconstruir la imagen.
- **Sin recursos externos:** las fuentes Manrope y DM Mono se sirven desde
  `static/fonts/` (SIL Open Font License), así que el navegador no hace
  peticiones a terceros.

## Pruebas

Las pruebas usan [pytest](https://docs.pytest.org/) y llaman a la API sin
levantar el servidor. Con el entorno virtual activado:

```bash
python -m pip install -r requirements-dev.txt
python -m pytest
```

`requirements-dev.txt` añade `pytest`, `httpx` (cliente de pruebas de
FastAPI), y `openpyxl` y `python-pptx` para generar los archivos de Excel y
PowerPoint de las pruebas. La configuración está en `pytest.ini`.

Se omiten automáticamente las pruebas que necesitan un programa que no está
instalado: las de Office sin LibreOffice; las que comprimen (también en el
flujo de trabajo y en las pruebas de seguridad) sin Ghostscript; las de OCR
sin OCRmyPDF o Tesseract. La prueba de que un tiempo agotado termina también
los procesos hijos solo se ejecuta en Linux, y la de `malloc_trim` solo en
Linux. Las pruebas no forman parte de la imagen de Docker.

## Estructura del proyecto

```text
app/
  main.py             # Aplicación FastAPI: páginas, endpoints y límites
static/               # JavaScript (uno por herramienta, más common.js) y estilos
  fonts/              # Fuentes Manrope y DM Mono incluidas (licencia OFL)
templates/            # Página de inicio y una página HTML por herramienta
tests/                # Pruebas con pytest (conftest.py y una por herramienta)
Dockerfile            # Imagen (python:3.12-slim + LibreOffice, Ghostscript, Tesseract)
docker-compose.yml    # Servicio unir-pdf: puerto, memoria, endurecimiento y variables
.dockerignore         # Archivos excluidos de la imagen
pytest.ini            # Configuración de pytest
requirements.txt      # Dependencias de Python
requirements-dev.txt  # Dependencias para ejecutar las pruebas
ROADMAP.md            # Trabajo hecho y planificado
```
