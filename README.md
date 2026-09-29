# Recetas

Web de recetas con FastAPI + PostgreSQL, levantada con Docker Compose en el puerto **8085**.

## Arranque

```bash
cp .env.example .env        # opcional: credenciales de la BBDD
docker compose up -d --build
```

Abrir http://localhost:8085

El esquema y 4 recetas de ejemplo se cargan desde `db/init.sql` la primera vez que se crea el volumen.
Para reiniciar la BBDD desde cero: `docker compose down -v && docker compose up -d`.

El build usa por defecto el proxy `http://proxycloud.eitb.lan:80` para instalar las dependencias
(ver `build.args` en `docker-compose.yml`). Fuera de la red corporativa:

```bash
HTTP_PROXY= HTTPS_PROXY= docker compose up -d --build
```

El proxy (Zscaler) intercepta el HTTPS, así que la imagen incluye su CA raíz desde `app/certs/`.
Cualquier `*.crt` que se deje en esa carpeta se añade al almacén de certificados del contenedor.

## Crear recetas desde texto (IA)

En **✨ Testutik sortu** (`/recetas/importar`) se pega una receta en texto libre. Un LLM extrae título,
descripción, raciones, tags, ingredientes, pasos y tiempos, y **rellena el formulario para revisarlo antes de guardar**
(nunca guarda directamente). También hay API: `POST /api/recetas/extraer` con el texto plano como cuerpo.

El proveedor se elige en `.env` (ver `.env.example`):

| Entorno | `.env` |
|---------|--------|
| Dev: Claude | `LLM_PROVIDER=anthropic`, `ANTHROPIC_API_KEY=sk-ant-…`, `ANTHROPIC_MODEL=claude-opus-5-5` |
| Producción: Ollama | `LLM_PROVIDER=ollama`, `OLLAMA_URL=http://host.docker.internal:11434`, `OLLAMA_MODEL=qwen2.5:7b` |

Los dos usan salida estructurada con el mismo esquema JSON (Claude mediante *tool use* forzado, Ollama
con `format`), y la respuesta se valida y limpia en `app/ia.py` antes de mostrarse.

**Ollama en la misma máquina que Docker:** por defecto Ollama solo escucha en `127.0.0.1`, y el contenedor
no le llega. Hazlo escuchar en todas las interfaces:

```bash
sudo systemctl edit ollama      # añadir:
# [Service]
# Environment="OLLAMA_HOST=0.0.0.0:11434"
sudo systemctl restart ollama
```

Si Ollama está en otra máquina, pon su dirección en `OLLAMA_URL`. Para comprobar la conexión desde el contenedor:


```bash
docker compose exec web python -c "import os, httpx; print(httpx.get(os.environ['OLLAMA_URL'] + '/api/tags', trust_env=False).text)"
```

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
