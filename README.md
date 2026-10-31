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
- **PDF a imágenes:** convierte las páginas de un PDF (todas, o solo las
  indicadas, por ejemplo `1-3, 5`) en imágenes PNG o JPG a 72, 150 o 300 ppp.
  Las imágenes conservan el número real de página en el nombre y se descargan
  juntas en un ZIP (o como una sola imagen si se convierte una página). Las
  páginas muy grandes se renderizan a menor resolución para no superar unos
  40 millones de píxeles por imagen. Antes de empezar se estima el tamaño del
  resultado: si supera el máximo de píxeles o no cabe en el disco temporal se
  rechaza indicando las páginas y el tamaño estimado, y una conversión que
  tarda demasiado se cancela (ver `PDF_TO_IMAGES_*` en la configuración).
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
  cuándo. Los cambios se guardan en el PDF descargado (`<nombre>-metadatos.pdf`),
  tanto en la información clásica del documento como en un bloque XMP nuevo,
  porque muchos visores (Adobe, las propiedades de Windows) solo leen el XMP.
- **OCR (hacer buscable un PDF escaneado):** reconoce el texto de las páginas
  escaneadas con [OCRmyPDF](https://ocrmypdf.readthedocs.io/) y Tesseract y lo
  añade como una capa invisible, para poder buscar, seleccionar y copiar el
  texto sin cambiar el aspecto del documento. Idiomas: español (por defecto),
  inglés o ambos. Las páginas que ya tienen texto se dejan como están. Tarda
  varios segundos por página.
- **Extraer texto:** descarga el texto de un PDF como archivo `.txt` (UTF-8), de
  todas las páginas o de un rango (`1-3, 5`), con un encabezado
  `--- Página N ---`, un salto de página o nada entre páginas, y muestra una
  vista previa con el número de caracteres y páginas. Los PDF escaneados no
  tienen texto: hay que pasarlos antes por OCR.
- **Organizar páginas:** muestra una vista previa de cada página y permite
  girarlas, eliminarlas y reordenarlas (con los botones o arrastrándolas, también
  en pantallas táctiles manteniendo pulsada la página) antes de descargar el PDF
  resultante.
  Al pulsar una página se abre una vista ampliada en alta resolución.
- **Flujo de trabajo:** encadena varias herramientas de una sola vez (por
  ejemplo unir, numerar páginas, borrar metadatos, comprimir y proteger) y
  descarga solo el resultado final. Si se eligen varios PDFs, el primer paso es
  siempre unirlos; «Proteger con contraseña» va siempre al final. El navegador
  llama uno tras otro a los mismos endpoints de la API, así que cada paso
  aplica sus propias validaciones y límites; si un paso falla, se muestra su
  error y se puede descargar el resultado del último paso correcto.
- **Firmar PDF:** dibuja tu firma con el ratón o el dedo (tinta negra o azul)
  o sube una imagen PNG (admite fondo transparente) o JPEG, y colócala sobre
  la vista previa de las páginas que quieras: se arrastra, se cambia de tamaño
  sin deformarla y también se mueve con las flechas del teclado. Es una firma
  **visual** (una imagen sobre la página), no una firma digital certificada:
  no acredita la identidad de quien firma ni protege el documento frente a
  cambios. Funciona aunque las páginas estén giradas.

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

Sin Compose, los límites se pasan con `-e` (por ejemplo
`-e MAX_UPLOAD_MB=800 -e MAX_TOTAL_UPLOAD_MB=1500`) y la memoria con
`--memory=3g`; ver [Configuración](#configuración).

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

La aplicación no necesita ninguna configuración para arrancar. Los límites
principales se pueden cambiar con variables de entorno (en Docker, en la
sección `environment:` de `docker-compose.yml`, donde ya aparecen con sus
valores por defecto; tras editarlos basta con `docker compose up -d`). Se leen
una vez al arrancar: si una variable no es un número entero o está fuera de
rango, el servicio no arranca y el registro (`docker compose logs`) indica qué
variable falla.

| Variable de entorno | Por defecto | Rango | Descripción |
| --- | --- | --- | --- |
| `MAX_UPLOAD_MB` | 500 MB | 1-100000 | Tamaño máximo de cada archivo subido; si se supera responde `413`. |
| `MAX_TOTAL_UPLOAD_MB` | 1000 MB | 1-100000 | Tamaño máximo de todos los archivos de una petición (**Unir PDF**, **Imágenes a PDF**); si se supera responde `413`. No puede ser menor que `MAX_UPLOAD_MB`; si no se indica, vale 1000 o `MAX_UPLOAD_MB` si este es mayor. |
| `OFFICE_TIMEOUT_SECONDS` | 300 s | 10-86400 | Tiempo máximo de una conversión con LibreOffice; si se supera responde `504`. |
| `COMPRESSION_TIMEOUT_SECONDS` | 600 s | 10-86400 | Tiempo base de la compresión con Ghostscript. |
| `COMPRESSION_SECONDS_PER_PAGE` | 2 s | 0-600 | Tiempo extra por página: el límite es base + páginas × este valor (20 páginas: 640 s; 1000 páginas: 2600 s). Si se supera responde `504`. |
| `COMPRESSION_MAX_TIMEOUT_SECONDS` | 3600 s | 10-604800 | Tope del tiempo de compresión (nunca menor que el tiempo base). |
| `OCR_MAX_PAGES` | 2000 | 1-100000 | Número máximo de páginas de un PDF para el OCR; si se supera responde `400`. |
| `OCR_TIMEOUT_SECONDS` | 60 s | 10-86400 | Tiempo base del OCR. |
| `OCR_SECONDS_PER_PAGE` | 20 s | 0-3600 | Tiempo extra del OCR por cada página. |
| `OCR_MAX_TIMEOUT_SECONDS` | 14400 s | 10-604800 | Tope del tiempo del OCR (4 horas); si se supera responde `504`. |
| `PDF_TO_IMAGES_MAX_MEGAPIXELS` | 10000 | 1-1000000 | Máximo de millones de píxeles en total de **PDF a imágenes** (suma de las páginas elegidas a la resolución elegida); si se supera responde `400` antes de empezar. Una página A4 son unos 2,2 millones a 150 ppp y 8,7 a 300 ppp: se admiten 1000 páginas A4 a 300 ppp, pero no 2000. |
| `PDF_TO_IMAGES_MIN_FREE_MB` | 512 MB | 0-1000000 | Espacio que debe quedar libre en el disco temporal tras el tamaño estimado de las imágenes (unos 0,5 bytes por píxel en PNG y 0,2 en JPG); si no cabe responde `507` antes de empezar. |
| `PDF_TO_IMAGES_TIMEOUT_SECONDS` | 300 s | 10-86400 | Tiempo base de **PDF a imágenes**. |
| `PDF_TO_IMAGES_SECONDS_PER_PAGE` | 3 s | 0-600 | Tiempo extra por página convertida (20 páginas: 360 s; 1000 páginas: 3300 s). Se comprueba entre página y página; si se supera se cancela, se borran los archivos temporales y responde `504`. |
| `PDF_TO_IMAGES_MAX_TIMEOUT_SECONDS` | 7200 s | 10-604800 | Tope del tiempo de **PDF a imágenes** (nunca menor que el tiempo base). |
| `DOCUMENT_TTL_SECONDS` | 1800 s (30 min) | 60-604800 | Tiempo sin uso tras el que se borran los documentos de **Organizar páginas** y los archivos temporales que hayan quedado. |
| `DOCUMENT_SWEEP_SECONDS` | 300 s (5 min) | 10-86400 | Frecuencia de esa limpieza. |
| `HEAVY_JOB_BUDGET_MB` | 2000 MB | 100-1000000 | Memoria estimada que pueden ocupar a la vez los trabajos grandes (ver más abajo). Pensado para el `mem_limit` de 3 GB: deja ~1 GB para Python y los trabajos pequeños. Súbelo junto con `mem_limit`. |
| `HEAVY_JOB_WAIT_SECONDS` | 300 s | 1-86400 | Tiempo máximo que un trabajo grande espera su turno; si se supera responde `503` con `Retry-After`. |
| `HEAVY_JOB_MAX_WAITING` | 10 | 1-1000 | Trabajos grandes que pueden esperar turno a la vez; si ya hay tantos esperando, el siguiente recibe `503` al momento (cada espera ocupa uno de los hilos del servidor). |

Con los valores por defecto se pueden procesar PDF de más de 1000 páginas y
varios cientos de MB. Como referencia, con un PDF de 200 MB y 1000 páginas
(medido en un PC de sobremesa) unir dos copias tarda unos 9 s y usa unos
500 MB de memoria; dividir, organizar, numerar o editar metadatos, 3-5 s y
unos 300 MB; protegerlo, unos 650 MB; comprimirlo, unos 20 s. Los resultados
se escriben en archivos temporales y se envían desde el disco, así que el
disco temporal del contenedor necesita espacio para la subida y el resultado.
Si subes `MAX_UPLOAD_MB`, sube también `mem_limit` en `docker-compose.yml`
(3 GB por defecto): las operaciones más pesadas usan unas 2-3 veces el tamaño
del archivo en memoria.

**Trabajos grandes a la vez.** Para que dos archivos enormes procesados a la
vez no agoten la memoria del contenedor, cada herramienta estima la memoria
que necesitará a partir del tamaño de la subida (por ejemplo, unas 1,5 veces
el archivo al dividir o numerar, 3,5 veces al proteger, y una cantidad fija
más para Ghostscript, Tesseract o LibreOffice; la tabla está en
`HEAVY_JOB_ESTIMATES`) y la reserva de un presupuesto común
(`HEAVY_JOB_BUDGET_MB`). Si no cabe, la petición espera su turno por orden de
llegada; un trabajo mayor que todo el presupuesto se ejecuta solo. Los
trabajos pequeños (archivos de menos de `HEAVY_JOB_SMALL_FILES_MB` en total,
como los PDF habituales de pocos MB, o con una estimación menor de
`HEAVY_JOB_MIN_MB`) nunca esperan, sea cual sea la herramienta. Si el turno no llega en
`HEAVY_JOB_WAIT_SECONDS`, la respuesta es `503` («El servidor está ocupado con
otros documentos grandes...») con la cabecera `Retry-After`. Las peticiones en
espera ocupan un hilo del servidor (hay 40), lo que sobra para un uso personal.

Otros valores fijos, definidos como constantes en `app/main.py`:

| Constante | Valor | Descripción |
| --- | --- | --- |
| `MULTIPART_OVERHEAD_MB` | 1 MB | Margen para los separadores y campos del formulario. Una petición mayor que `MAX_TOTAL_UPLOAD_MB` + este margen se rechaza con `413` antes de guardarse en disco (por su `Content-Length`, o en cuanto lo supera si no lo indica). |
| `MAX_IMAGE_PIXELS` | 40 000 000 | Límite de píxeles por imagen en **PDF a imágenes**. |
| `IMAGE_BYTES_PER_PIXEL` | PNG 0,5 / JPG 0,2 | Bytes por píxel con los que **PDF a imágenes** estima el tamaño del resultado. Una página de texto ocupa mucho menos en PNG y una escaneada o con fotos bastante más; si el disco se llena durante la conversión se cancela igualmente con `507`. |
| `STAMP_NUMBER_SIZE_RANGE` | 6-72 pt | Tamaños de letra admitidos para los números de página. |
| `STAMP_WATERMARK_SIZE_RANGE` | 8-200 pt | Tamaños de letra admitidos para la marca de agua (se reduce si el texto no cabe). |
| `STAMP_WATERMARK_MAX_LENGTH` | 100 | Longitud máxima del texto de la marca de agua. |
| `STAMP_MAX_START_NUMBER` | 100 000 | Número inicial máximo de la numeración. |
| `STAMP_MARGIN` | 28 pt | Distancia de los números de página al borde. |
| `MAX_METADATA_LENGTH` | 500 | Longitud máxima de cada campo en **Editar metadatos**. |
| `SIGNATURE_MAX_MB` | 10 MB | Tamaño máximo de la imagen de la firma; si se supera responde `413`. |
| `SIGNATURE_MAX_SOURCE_PIXELS` | 40 000 000 | Píxeles máximos de la imagen de la firma subida. |
| `SIGNATURE_MAX_SIDE` | 2000 px | La firma se reduce a este lado máximo antes de incrustarla en el PDF. |
| `SIGNATURE_MAX_PLACEMENTS` | 200 | Número máximo de firmas colocadas en una petición. |
| `OCR_LANGUAGES` | `spa`, `eng`, `spa+eng` | Idiomas admitidos por el OCR. |
| `OCR_JOBS` | 2 | Páginas que Tesseract procesa a la vez en cada petición de OCR. |
| `HEAVY_JOB_MIN_MB` | 100 MB | Los trabajos con una estimación menor no reservan memoria ni esperan turno. |
| `HEAVY_JOB_SMALL_FILES_MB` | 50 MB | Los trabajos sobre archivos que suman menos que esto no esperan turno en ninguna herramienta. |
| `HEAVY_JOB_RETRY_AFTER_SECONDS` | 60 s | Valor de `Retry-After` cuando el servidor está ocupado. |
| `DECODED_BYTES_PER_PIXEL` | 8 | Memoria por píxel con la que se estima la decodificación de una imagen (**Imágenes a PDF**, **Firmar PDF**). Cuenta aunque el archivo sea pequeño, así que una imagen muy comprimida pero enorme espera su turno como un trabajo grande. |
| `IMAGE_MAX_PIXELS` | 100 000 000 | Píxeles máximos de cada imagen subida (**Imágenes a PDF**, **Firmar PDF**). Se leen de la cabecera antes de decodificarla; si se supera responde `400`. También es el límite antibomba de Pillow (su aviso se trata como error). |
| `PAGE_SELECTION_MAX_LENGTH` | 1000 | Longitud máxima del texto de páginas (`pages`) de **PDF a imágenes** y **Extraer texto**. |
| `PAGE_LIST_FACTOR` / `PAGE_LIST_MIN_LIMIT` | 10 / 100 | Las listas de páginas que pueden repetir páginas (rangos de **Extraer texto** y de **Dividir** en ZIP, lista de **Organizar páginas**) suman como máximo 10 veces las páginas del PDF, y nunca menos de 100; si se supera responde `400`. |
| `SPLIT_MAX_RANGES` | 500 | Rangos máximos de `/api/split-zip` en modo `ranges`. |
| `THUMBNAIL_MAX_PIXELS` / `THUMBNAIL_MAX_SIDE` | 8 000 000 / 16 000 px | Tope de las miniaturas de **Organizar páginas** y **Firmar PDF**: una página enorme (por ejemplo 500 × 200 000 pt) se dibuja más pequeña. |
| `PDF_HEADER_SEARCH_BYTES` | 1024 | **Comprimir PDF** y **OCR** exigen la cabecera `%PDF-` en los primeros bytes; lo que haya delante se elimina antes de pasar el archivo a Ghostscript u OCRmyPDF, y sin cabecera responde `400`. |
| `MAX_OUTPUT_NAME_LENGTH` | 150 | Caracteres máximos del nombre de descarga (`output_name`, `base_name`), sin contar la extensión. |

Si una conversión, compresión u OCR supera su tiempo máximo, se termina el proceso
externo junto con todos los procesos que haya iniciado. La limpieza periódica
nunca borra los archivos de un trabajo que sigue en curso, aunque tarde más
que `DOCUMENT_TTL_SECONDS` (por ejemplo, comprimir un PDF enorme).

El puerto se elige al arrancar `uvicorn` (`--port`) o en el mapeo de puertos
de Docker.

## Interfaz web

Cada herramienta tiene su propia página: `/` (inicio), `/merge`, `/split`,
`/pages`, `/compress`, `/metadata`, `/extract-text`, `/protect`, `/images`, `/pdf-to-images`, `/stamp`, `/ocr`, `/sign`, `/word`,
`/excel`, `/powerpoint` y `/workflow` (flujo de trabajo, que no tiene endpoint
propio: encadena los de las demás herramientas desde el navegador).

La interfaz tiene modo oscuro automático: sigue la preferencia de tema claro u
oscuro del sistema operativo, sin necesidad de configurar nada.

## API

Además de la interfaz web, el servidor ofrece estos endpoints:

| Método | Ruta | Descripción |
| --- | --- | --- |
| `GET` | `/health` | Estado del servicio: `storage` (puede guardar archivos temporales), `libreoffice` (conversiones de Office disponibles) y `ghostscript` (compresión disponible) y `ocr` (OCRmyPDF y Tesseract disponibles), y `heavy_jobs` (`running`, `reserved_mb` y `waiting` de los trabajos grandes). Responde `503` si no puede guardar archivos. |
| `POST` | `/api/merge` | Une varios PDF recibidos como `files`. |
| `POST` | `/api/pdf-info` | Devuelve el número de páginas de un PDF. |
| `POST` | `/api/split` | Extrae un rango mediante `start_page` y `end_page`. |
| `POST` | `/api/split-zip` | Divide un PDF en una sola petición y devuelve un ZIP. `mode=pages` crea un PDF por página (`base_name`); `mode=ranges` usa `ranges` (`1-3, 4-6`; hasta 500 rangos que sumen como máximo 10 veces las páginas del PDF, mínimo 100) y la lista JSON `names`. Si solo resulta un archivo, devuelve el PDF directamente. |
| `POST` | `/api/images-to-pdf` | Convierte las imágenes recibidas como `images`. Cada imagen puede tener como máximo `IMAGE_MAX_PIXELS` (100 millones de píxeles). |
| `POST` | `/api/pdf-to-images` | Convierte cada página de un PDF en una imagen; `image_format` es `png` (por defecto) o `jpg`, `dpi` es `72`, `150` (por defecto) o `300`, `pages` elige las páginas con la sintaxis `1-3, 5` (vacío = todas; una página repetida se convierte una vez) y `base_name` da nombre a las imágenes con el número real de página (`<base_name>-page-001.png`; con 1000 páginas o más, el número lleva cuatro cifras: `-page-0001`). Devuelve un ZIP, o la imagen directamente si se convierte una sola página. `400` si supera `PDF_TO_IMAGES_MAX_MEGAPIXELS`, `507` si el resultado estimado no cabe en el disco temporal o este se llena, `504` si tarda demasiado. |
| `POST` | `/api/word-to-pdf` | Convierte un archivo `.doc` o `.docx`. |
| `POST` | `/api/excel-to-pdf` | Convierte un archivo `.xls` o `.xlsx`. |
| `POST` | `/api/powerpoint-to-pdf` | Convierte un archivo `.ppt` o `.pptx`. |
| `POST` | `/api/compress` | Comprime un PDF; `level` es `strong`, `recommended` (por defecto) o `high`. Las cabeceras `X-Original-Size`, `X-Compressed-Size` y `X-Compression-Applied` indican el resultado. |
| `POST` | `/api/protect` | Cifra un PDF con AES-256 usando `password` (obligatoria, máximo 127 bytes). Responde `400` si el PDF ya estaba protegido. |
| `POST` | `/api/unlock` | Quita la contraseña de un PDF protegido usando `password` (puede ir vacía si el PDF solo tiene restricciones de permisos). Responde `400` con `Contraseña incorrecta` si no coincide, o si el PDF no estaba protegido. |
| `POST` | `/api/stamp` | Numera las páginas (`mode=numbers`, por defecto) o añade una marca de agua (`mode=watermark`). Números: `position` (`bottom-center` por defecto, `bottom-left`, `bottom-right`, `top-center`, `top-left`, `top-right`), `number_format` (`n` → `1`, `n-of-total` → `1 / N`, `page-n-of-total` → `Página 1 de N`; N es el último número), `start_number` (0-100000, por defecto 1) y `font_size` (6-72, por defecto 12). Marca de agua: `text` (obligatorio, hasta 100 caracteres), `font_size` (8-200, por defecto 60), `opacity` (0.1-1, por defecto 0.3) y `rotation` (`45` por defecto o `0`). `output_name` es opcional (`<nombre>-numerado.pdf` / `<nombre>-marca-de-agua.pdf`). |
| `POST` | `/api/metadata/read` | Devuelve en JSON los metadatos de un PDF (`title`, `author`, `subject`, `keywords`, `creator`, `producer`, `creation_date`, `modification_date`; `null` si faltan) y su número de páginas (`pages`). |
| `POST` | `/api/metadata` | Escribe `title`, `author`, `subject` y `keywords` (máximo 500 caracteres cada uno; un campo vacío o ausente se elimina), sustituye el bloque XMP por uno nuevo con los mismos valores y borra las entradas no estándar que añaden otros programas (se conservan creador, productor, fechas y `/Trapped`). Con `remove_all=true` elimina todos los metadatos. La descarga se llama `<nombre>-metadatos.pdf` salvo que se indique `output_name`. |
| `POST` | `/api/ocr` | Hace buscable un PDF escaneado con OCRmyPDF y Tesseract; `language` es `spa` (por defecto), `eng` o `spa+eng`. Las páginas que ya tienen texto se conservan. La descarga se llama `<nombre>-ocr.pdf` salvo que se indique `output_name`. Máximo `OCR_MAX_PAGES` páginas (2000 por defecto). |
| `POST` | `/api/extract-text` | Extrae el texto de un PDF y lo devuelve como `text/plain; charset=utf-8` (`<nombre>.txt`, o `output_name` con extensión `.txt`). `pages` admite rangos (`1-3, 5`; vacío = todas), `page_separator` es `header` (por defecto, línea `--- Página N ---`), `formfeed` o `none`. Los rangos se extraen en el orden indicado y una página repetida se extrae otra vez, con un máximo de 10 veces las páginas del PDF en total (mínimo 100) y 1000 caracteres en `pages`. Las cabeceras `X-Page-Count` y `X-Character-Count` indican páginas y caracteres extraídos. Responde `400` si el PDF no contiene texto (probablemente escaneado; usar OCR antes). |
| `POST` | `/api/documents` | Sube un PDF una sola vez y devuelve su `id` y número de páginas. |
| `GET` | `/api/documents/{id}/pages/{n}` | Devuelve la página `n` como JPEG; `width` controla el tamaño (100-2000 px). |
| `POST` | `/api/documents/{id}/edit` | Genera el PDF editado a partir de `pages` sin volver a subir el archivo. |
| `POST` | `/api/documents/{id}/sign` | Firma el documento subido sin volver a enviarlo: `signature` es la imagen de la firma (PNG o JPEG; la transparencia del PNG se conserva) y `placements` una lista JSON como `[{"page": 1, "x": 0.6, "y": 0.8, "width": 0.3, "height": 0.08}]`, con la caja en fracciones (0-1) de la página tal como se ve (origen arriba a la izquierda, ya girada). Admite hasta 200 cajas que deben quedar dentro de la página. Devuelve `firmado.pdf` salvo que se indique `output_name`; después se borra el documento subido, como en `/edit`. |
| `DELETE` | `/api/documents/{id}` | Elimina el documento subido. |
| `POST` | `/api/edit-pages` | Genera un PDF a partir de `pages`, una lista JSON como `[{"page": 3, "rotation": 90}]`; las páginas omitidas se eliminan. La lista admite como máximo 10 veces las páginas del PDF (mínimo 100), igual que `/api/documents/{id}/edit`. |

Los documentos subidos a `/api/documents` se guardan en una carpeta temporal
del contenedor y se eliminan en cuanto se descarga el PDF editado o firmado, al cerrar
la página, tras 30 minutos sin uso (se comprueba cada 5 minutos; ambos
valores son configurables) y al iniciar el servidor. Si se sigue editando después de descargar, la página vuelve a
subir el archivo automáticamente.

Los endpoints que generan archivos aceptan el campo opcional `output_name`
para indicar el nombre de la descarga. Las peticiones de archivos usan
`multipart/form-data`.

Los errores devuelven un JSON con `detail` en español:

- `400`: archivo o parámetros no válidos, o PDF protegido con contraseña (hay
  que desbloquearlo antes; los PDF que solo tienen contraseña de propietario
  se procesan con normalidad). El detalle técnico del error no se envía al
  cliente: queda en el registro del servidor (`docker compose logs`).
- `403`: petición `POST` o `DELETE` a `/api/` enviada desde una página de otro
  sitio (ver «Consideraciones de despliegue»).
- `413`: un archivo supera `MAX_UPLOAD_MB` o el conjunto supera
  `MAX_TOTAL_UPLOAD_MB`.
- `503`: falta LibreOffice, Ghostscript, OCRmyPDF o Tesseract (o un idioma
  de Tesseract), o el servidor está ocupado con otros trabajos grandes y el
  turno no llegó a tiempo (con `Retry-After`). `504`: la conversión, la compresión, el OCR o **PDF a
  imágenes** tardó demasiado.
- `507`: **PDF a imágenes** no cabe en el disco temporal del servidor (según
  la estimación previa, o porque el disco se llenó durante la conversión).
- `410`: el documento de **Organizar páginas** o **Firmar PDF** ha caducado.

La documentación interactiva de FastAPI (`/docs`, `/redoc` y `/openapi.json`)
está desactivada: la API se describe en esta tabla.

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
tienen un tamaño máximo (`MAX_UPLOAD_MB` y `MAX_TOTAL_UPLOAD_MB`, 500 MB y
1 GB por defecto) y `docker-compose.yml` limita la memoria del contenedor a
3 GB (`mem_limit`).

Además:

- **Peticiones de otros sitios.** Las peticiones `POST` y `DELETE` a `/api/`
  que el navegador marca como enviadas desde otro sitio (`Sec-Fetch-Site:
  cross-site` o `same-site`, lo que incluye otra aplicación en otro puerto de
  `localhost`), o cuyo `Origin` no coincide con el `Host`, se rechazan con
  `403`. Así una página web cualquiera no puede subir archivos al servicio ni
  borrar documentos. Las peticiones sin esas cabeceras (curl, scripts) y las
  de las propias páginas del servicio funcionan igual. Detrás de un proxy
  inverso, el proxy debe conservar la cabecera `Host` original.
- **LibreOffice sin enlaces externos.** Cada conversión usa un perfil nuevo
  con `BlockUntrustedRefererLinks`, sin contenido activo (OLE, DDE), sin
  actualizar vínculos y con un proxy inexistente para HTTP(S). Una imagen
  enlazada a `http://...` no se descarga y una enlazada a `file:///...` no se
  incrusta en el PDF.
- **Ghostscript solo recibe PDF.** **Comprimir PDF** y **OCR** quitan lo que
  haya antes de la cabecera `%PDF-` (un programa PostScript delante de un PDF
  válido se ejecutaría) y rechazan archivos sin cabecera.
- **Límites contra trabajos desproporcionados:** listas de páginas,
  miniaturas, píxeles de las imágenes subidas y longitud de los nombres de
  descarga (ver la tabla de constantes).
- **Contenedor.** `docker-compose.yml` usa `init: true` (recoge los procesos
  zombi de LibreOffice, Ghostscript o Tesseract), `cap_drop: [ALL]`,
  `security_opt: no-new-privileges:true` y `pids_limit: 512`.

La interfaz no carga recursos externos: las fuentes Manrope y DM Mono se
sirven desde `static/fonts/`, distribuidas bajo la SIL Open Font License, por
lo que el navegador no realiza peticiones a servicios de terceros.
