# Recetas

Web de recetas con FastAPI + PostgreSQL, levantada con Docker Compose en el puerto **8085**.

## Arranque

`docker-compose.yml` es la configuración de **producción**: sin proxy y con la IA en OpenCode Go.
En **dev** se le suma `docker-compose.dev.yml`, que añade el proxy corporativo y usa Claude.

**Producción:**

```bash
cp .env.example .env        # ajustar contraseña de la BBDD, clave y modelo de OpenCode
docker compose up -d --build
```

La contraseña puede llevar cualquier carácter; si contiene `$`, ponla entre comillas simples en el `.env`
(`POSTGRES_PASSWORD='abc$123'`) para que Compose no intente interpretarla como variable.
La contraseña se fija al crear el volumen: si la cambias después, hay que cambiarla también en Postgres
o recrear la BBDD con `docker compose down -v` (borra los datos).

**Dev** (red con proxy): en el `.env` añade

```env
COMPOSE_FILE=docker-compose.yml:docker-compose.dev.yml
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
```

y lanza el mismo `docker compose up -d --build`. Sin tocar el `.env`, puedes usar
`docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build`.

Abrir http://localhost:8085

El esquema y 4 recetas de ejemplo se cargan desde `db/init.sql` la primera vez que se crea el volumen.
Para reiniciar la BBDD desde cero: `docker compose down -v && docker compose up -d`.

En dev el proxy (Zscaler) intercepta el HTTPS, así que la imagen incluye su CA raíz desde `app/certs/`
(en producción no molesta). Cualquier `*.crt` que se deje en esa carpeta se añade al almacén de certificados del contenedor.

## Crear recetas desde texto (IA)

En **✨ Testutik sortu** (`/recetas/importar`) se pega una receta en texto libre. Un LLM extrae título,
descripción, raciones, tags, ingredientes, pasos y tiempos, y **rellena el formulario para revisarlo antes de guardar**
(nunca guarda directamente). También hay API: `POST /api/recetas/extraer` con el texto plano como cuerpo.

El proveedor se elige en `.env` (ver `.env.example`):

| Entorno | `.env` |
|---------|--------|
| Dev: Claude | `COMPOSE_FILE=docker-compose.yml:docker-compose.dev.yml`, `LLM_PROVIDER=anthropic`, `ANTHROPIC_API_KEY=sk-ant-…`, `ANTHROPIC_MODEL=claude-opus-5-5` |
| Producción: OpenCode Go | `LLM_PROVIDER=opencode`, `OPENCODE_API_KEY=oc_sk_…`, `OPENCODE_MODEL=deepseek-v4.1-flash` |

Los dos usan salida estructurada con el mismo esquema JSON (Claude mediante *tool use* forzado, OpenCode
con `response_format` y el esquema también en el prompt), y la respuesta se valida y limpia en `app/ia.py`
antes de mostrarse.

**OpenCode Go:** es un servicio externo (HTTPS), así que no hace falta nada en el host. La clave se saca de
la consola de OpenCode (Console › API keys) y se pone en `OPENCODE_API_KEY`. La URL por defecto es
`https://opencode.ai/inference/go/openai/v1`; si usas otro gateway compatible con OpenAI, cámbiala con
`OPENCODE_BASE_URL`. El contenedor `web` sigue usando la red del host (`network_mode: host`) para llegar a
Postgres en `127.0.0.1:5433` (cambiable con `POSTGRES_HOST_PORT`) y publicar el 8085.

## Modelo de datos

Cada parte de una receta es una tabla independiente:

| Tabla               | Contenido                                        |
|---------------------|--------------------------------------------------|
| `recetas`           | título, descripción, raciones, personas          |
| `tags` / `receta_tags` | tags únicos y relación N:M con recetas        |
| `imagenes`          | una imagen por receta (binario en `BYTEA`) y la URL de origen si se descargó |
| `ingredientes`      | nombre, cantidad y orden                         |
| `pasos_preparacion` | texto de cada paso y orden                       |
| `tiempos_coccion`   | fase (preparación, horno, reposo…) y minutos     |
| `consejos`          | tips de la receta, con orden                     |
| `aparatos` / `receta_aparatos` | catálogo (airfryer, horno, microondas, sartén) y los que usa cada receta |

Los campos opcionales (personas, raciones, tiempos, consejos, aparatos) solo se muestran si están rellenos.
Debajo de cada receta aparecen hasta 3 **recetas parecidas**: las que comparten más tags.

**Imágenes:** se pueden subir como archivo o indicar una URL; en ese caso el servidor descarga la imagen
y la guarda en la BBDD (no se enlaza). Por seguridad solo se descargan URLs `http(s)` que apunten a IPs
públicas, también tras las redirecciones, así nadie puede usar la web para llegar a servicios internos
como Postgres. Máximo 5 MB, y el formato se comprueba por el contenido: JPEG, PNG, GIF, WebP o AVIF.
No se admite SVG, porque puede contener scripts.

**Tags en la portada:** se muestran los 5 con más recetas, más los que estén seleccionados; el resto se buscan
con el campo "Bilatu etiketa gehiago…", que autocompleta.

**Migraciones:** `app/migraciones.sql` es idempotente y la app lo ejecuta en cada arranque, así que una BBDD
ya existente recibe las tablas y columnas nuevas sin perder datos. En una BBDD nueva, Postgres ejecuta
`db/init.sql`, `app/migraciones.sql` y `db/ejemplos_extra.sql`, en ese orden.

## Rutas

| Ruta | Descripción |
|------|-------------|
| `/` | Listado; filtra por uno o varios tags: `/?tag=pescado&tag=sin%20gluten` (deben cumplirse todos) |
| `/tags/{nombre}` | Atajo al listado filtrado por un tag |
| `/recetas/nueva` | Formulario de alta |
| `/recetas/{id}` | Detalle |
| `/recetas/{id}/editar` | Edición |
| `/recetas/{id}/imagen` | Imagen de la receta |
| `/api/recetas?tag=…` | Listado en JSON |
| `/api/recetas/{id}` | Detalle en JSON |
| `/api/tags` | Tags con número de recetas |
| `/recetas/importar` | Crear receta desde texto con IA |
| `POST /api/recetas/extraer` | Extrae una receta de texto plano (JSON, no guarda) |
