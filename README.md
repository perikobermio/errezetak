# Recetas

Web de recetas con FastAPI + PostgreSQL, levantada con Docker Compose en el puerto **8085**.

## Arranque

`docker-compose.yml` es la configuración de **producción**: sin proxy y con la IA en Ollama.
En **dev** se le suma `docker-compose.dev.yml`, que añade el proxy corporativo y usa Claude.

**Producción:**

```bash
cp .env.example .env        # ajustar contraseña de la BBDD y modelo de Ollama
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
| Producción: Ollama | `LLM_PROVIDER=ollama`, `OLLAMA_URL=http://127.0.0.1:11434`, `OLLAMA_MODEL=qwen2.5:7b` |

Los dos usan salida estructurada con el mismo esquema JSON (Claude mediante *tool use* forzado, Ollama
con `format`), y la respuesta se valida y limpia en `app/ia.py` antes de mostrarse.

**Ollama en el host:** el contenedor `web` usa la red del host (`network_mode: host`), así que llama a
Ollama en `http://127.0.0.1:11434` sin tener que cambiar la configuración de Ollama (que por defecto solo
escucha en localhost). Por eso mismo, Postgres se publica solo en `127.0.0.1:5433` (cambiable con
`POSTGRES_HOST_PORT`) y la web se conecta por ahí. Para comprobar Ollama desde el contenedor:

```bash
docker compose exec web python -c "import httpx; print(httpx.get('http://127.0.0.1:11434/api/tags', trust_env=False).text)"
```

Si Ollama está en otra máquina, pon su dirección en `OLLAMA_URL`.

## Modelo de datos

Cada parte de una receta es una tabla independiente:

| Tabla               | Contenido                                        |
|---------------------|--------------------------------------------------|
| `recetas`           | título, descripción, raciones                    |
| `tags` / `receta_tags` | tags únicos y relación N:M con recetas        |
| `imagenes`          | una imagen por receta (binario en `BYTEA`)       |
| `ingredientes`      | nombre, cantidad y orden                         |
| `pasos_preparacion` | texto de cada paso y orden                       |
| `tiempos_coccion`   | fase (preparación, horno, reposo…) y minutos     |

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
