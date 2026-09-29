import os
import zlib
from urllib.parse import quote
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from starlette.concurrency import run_in_threadpool

import ia

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://recetas:recetas@localhost:5432/recetas")
MAX_IMAGEN_BYTES = 5 * 1024 * 1024

pool = ConnectionPool(DATABASE_URL, open=False, kwargs={"row_factory": dict_row})


@asynccontextmanager
async def lifespan(_app: FastAPI):
    pool.open(wait=True)
    yield
    pool.close()


app = FastAPI(title="Errezetak", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")
# Color estable por tag (índice 0-5 de la paleta del CSS).
templates.env.filters["color_tag"] = lambda nombre: zlib.crc32(nombre.encode()) % 6


# ---------------------------------------------------------------
# Consultas
# ---------------------------------------------------------------

def normalizar_tags(tags):
    vistos = []
    for t in tags:
        t = t.strip().lower()
        if t and t not in vistos:
            vistos.append(t)
    return vistos


def listar_recetas(tags):
    """Recetas que tienen TODOS los tags indicados (sin tags: todas)."""
    sql = """
        SELECT r.id, r.titulo, r.descripcion, r.raciones,
               COALESCE((SELECT sum(minutos) FROM tiempos_coccion WHERE receta_id = r.id), 0) AS minutos_totales,
               EXISTS (SELECT 1 FROM imagenes WHERE receta_id = r.id) AS tiene_imagen,
               ARRAY(SELECT t.nombre FROM receta_tags rt JOIN tags t ON t.id = rt.tag_id
                     WHERE rt.receta_id = r.id ORDER BY t.nombre) AS tags
        FROM recetas r
    """
    params = []
    if tags:
        sql += """
        WHERE (SELECT count(*) FROM receta_tags rt JOIN tags t ON t.id = rt.tag_id
               WHERE rt.receta_id = r.id AND t.nombre = ANY(%s)) = %s
        """
        params = [tags, len(tags)]
    sql += " ORDER BY r.creada_en DESC, r.id DESC"
    with pool.connection() as conn:
        return conn.execute(sql, params).fetchall()


def listar_tags():
    with pool.connection() as conn:
        return conn.execute("""
            SELECT t.nombre, count(rt.receta_id) AS total
            FROM tags t JOIN receta_tags rt ON rt.tag_id = t.id
            GROUP BY t.nombre ORDER BY t.nombre
        """).fetchall()


def obtener_receta(receta_id):
    with pool.connection() as conn:
        receta = conn.execute(
            """SELECT id, titulo, descripcion, raciones, creada_en,
                      EXISTS (SELECT 1 FROM imagenes WHERE receta_id = recetas.id) AS tiene_imagen
               FROM recetas WHERE id = %s""",
            [receta_id],
        ).fetchone()
        if not receta:
            return None
        receta["tags"] = [r["nombre"] for r in conn.execute(
            """SELECT t.nombre FROM receta_tags rt JOIN tags t ON t.id = rt.tag_id
               WHERE rt.receta_id = %s ORDER BY t.nombre""", [receta_id])]
        receta["ingredientes"] = conn.execute(
            "SELECT nombre, cantidad FROM ingredientes WHERE receta_id = %s ORDER BY orden", [receta_id]).fetchall()
        receta["pasos"] = [r["texto"] for r in conn.execute(
            "SELECT texto FROM pasos_preparacion WHERE receta_id = %s ORDER BY orden", [receta_id])]
        receta["tiempos"] = conn.execute(
            "SELECT fase, minutos FROM tiempos_coccion WHERE receta_id = %s ORDER BY orden", [receta_id]).fetchall()
        receta["minutos_totales"] = sum(t["minutos"] for t in receta["tiempos"])
        return receta


def guardar_receta(datos, receta_id=None):
    """Inserta o actualiza una receta y sus tablas dependientes en una transacción."""
    with pool.connection() as conn, conn.transaction():
        if receta_id is None:
            receta_id = conn.execute(
                "INSERT INTO recetas (titulo, descripcion, raciones) VALUES (%s, %s, %s) RETURNING id",
                [datos["titulo"], datos["descripcion"], datos["raciones"]],
            ).fetchone()["id"]
        else:
            cur = conn.execute(
                "UPDATE recetas SET titulo = %s, descripcion = %s, raciones = %s WHERE id = %s",
                [datos["titulo"], datos["descripcion"], datos["raciones"], receta_id],
            )
            if cur.rowcount == 0:
                return None
            for tabla in ("receta_tags", "ingredientes", "pasos_preparacion", "tiempos_coccion"):
                conn.execute(f"DELETE FROM {tabla} WHERE receta_id = %s", [receta_id])

        for nombre in datos["tags"]:
            tag_id = conn.execute(
                """INSERT INTO tags (nombre) VALUES (%s)
                   ON CONFLICT (nombre) DO UPDATE SET nombre = EXCLUDED.nombre RETURNING id""",
                [nombre],
            ).fetchone()["id"]
            conn.execute("INSERT INTO receta_tags (receta_id, tag_id) VALUES (%s, %s)", [receta_id, tag_id])

        for i, (nombre, cantidad) in enumerate(datos["ingredientes"], 1):
            conn.execute(
                "INSERT INTO ingredientes (receta_id, orden, nombre, cantidad) VALUES (%s, %s, %s, %s)",
                [receta_id, i, nombre, cantidad])
        for i, texto in enumerate(datos["pasos"], 1):
            conn.execute(
                "INSERT INTO pasos_preparacion (receta_id, orden, texto) VALUES (%s, %s, %s)",
                [receta_id, i, texto])
        for i, (fase, minutos) in enumerate(datos["tiempos"], 1):
            conn.execute(
                "INSERT INTO tiempos_coccion (receta_id, orden, fase, minutos) VALUES (%s, %s, %s, %s)",
                [receta_id, i, fase, minutos])

        if datos["imagen"]:
            nombre, mime, contenido = datos["imagen"]
            conn.execute(
                """INSERT INTO imagenes (receta_id, nombre, mime, datos) VALUES (%s, %s, %s, %s)
                   ON CONFLICT (receta_id) DO UPDATE
                   SET nombre = EXCLUDED.nombre, mime = EXCLUDED.mime, datos = EXCLUDED.datos""",
                [receta_id, nombre, mime, contenido])
        elif datos["quitar_imagen"]:
            conn.execute("DELETE FROM imagenes WHERE receta_id = %s", [receta_id])

        conn.execute("DELETE FROM tags WHERE id NOT IN (SELECT tag_id FROM receta_tags)")
    return receta_id


def borrar_receta(receta_id):
    with pool.connection() as conn, conn.transaction():
        conn.execute("DELETE FROM recetas WHERE id = %s", [receta_id])
        conn.execute("DELETE FROM tags WHERE id NOT IN (SELECT tag_id FROM receta_tags)")


def obtener_imagen(receta_id):
    with pool.connection() as conn:
        return conn.execute("SELECT mime, datos FROM imagenes WHERE receta_id = %s", [receta_id]).fetchone()


# ---------------------------------------------------------------
# Formulario
# ---------------------------------------------------------------

async def leer_formulario(request: Request):
    """Devuelve (datos, errores, valores_para_repintar)."""
    form = await request.form()
    errores = []

    titulo = (form.get("titulo") or "").strip()
    if not titulo:
        errores.append("Izenburua derrigorrezkoa da.")

    raciones = None
    if (form.get("raciones") or "").strip():
        try:
            raciones = int(form["raciones"])
            if raciones <= 0:
                raise ValueError
        except ValueError:
            errores.append("Anoen kopuruak zenbaki oso positiboa izan behar du.")

    ingredientes = [
        (n.strip(), c.strip())
        for n, c in zip(form.getlist("ingrediente_nombre"), form.getlist("ingrediente_cantidad"))
        if n.strip()
    ]
    pasos = [p.strip() for p in form.getlist("paso") if p.strip()]

    tiempos = []
    for fase, minutos in zip(form.getlist("tiempo_fase"), form.getlist("tiempo_minutos")):
        if not fase.strip() and not minutos.strip():
            continue
        try:
            m = int(minutos)
            if m < 0 or not fase.strip():
                raise ValueError
            tiempos.append((fase.strip(), m))
        except ValueError:
            errores.append(f"Denbora ez da baliozkoa: «{fase} {minutos}». Adierazi fasea eta minutuak (zenbaki osoa, ≥ 0).")

    imagen = None
    archivo = form.get("imagen")
    if archivo is not None and getattr(archivo, "filename", ""):
        contenido = await archivo.read()
        mime = archivo.content_type or ""
        if not mime.startswith("image/"):
            errores.append("Igotako fitxategia ez da irudi bat.")
        elif len(contenido) > MAX_IMAGEN_BYTES:
            errores.append("Irudiak ezin ditu 5 MB gainditu.")
        elif contenido:
            imagen = (archivo.filename, mime, contenido)

    datos = {
        "titulo": titulo,
        "descripcion": (form.get("descripcion") or "").strip(),
        "raciones": raciones,
        "tags": normalizar_tags((form.get("tags") or "").split(",")),
        "ingredientes": ingredientes,
        "pasos": pasos,
        "tiempos": tiempos,
        "imagen": imagen,
        "quitar_imagen": form.get("quitar_imagen") == "1",
    }
    valores = {
        **datos,
        "raciones": form.get("raciones") or "",
        "ingredientes": [{"nombre": n, "cantidad": c} for n, c in ingredientes],
        "tiempos": [{"fase": f, "minutos": m} for f, m in tiempos],
    }
    return datos, errores, valores


def pintar_formulario(request, receta, errores=None, status_code=200, desde_ia=False):
    return templates.TemplateResponse(
        request, "formulario.html",
        {"receta": receta, "errores": errores or [], "desde_ia": desde_ia}, status_code=status_code)


# ---------------------------------------------------------------
# Rutas HTML
# ---------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def inicio(request: Request, tag: list[str] = Query(default=[])):
    seleccion = normalizar_tags(tag)
    return templates.TemplateResponse(request, "index.html", {
        "recetas": listar_recetas(seleccion),
        "tags": listar_tags(),
        "seleccion": seleccion,
    })


@app.get("/tags/{nombre}")
def por_tag(nombre: str):
    return RedirectResponse(f"/?tag={quote(nombre)}", status_code=302)


@app.get("/recetas/nueva", response_class=HTMLResponse)
def nueva(request: Request):
    return pintar_formulario(request, None)


@app.get("/recetas/importar", response_class=HTMLResponse)
def importar(request: Request):
    return templates.TemplateResponse(request, "importar.html", {
        "texto": "", "error": None, "proveedor": ia.descripcion_proveedor()})


@app.post("/recetas/importar", response_class=HTMLResponse)
async def importar_texto(request: Request):
    """Extrae la receta con IA y la muestra en el formulario para revisarla antes de guardar."""
    texto = (await request.form()).get("texto") or ""
    try:
        receta = await run_in_threadpool(ia.extraer_receta, texto)
    except ia.ErrorIA as e:
        return templates.TemplateResponse(request, "importar.html", {
            "texto": texto, "error": str(e), "proveedor": ia.descripcion_proveedor()}, status_code=422)
    return pintar_formulario(request, receta, desde_ia=True)


@app.post("/recetas")
async def crear(request: Request):
    datos, errores, valores = await leer_formulario(request)
    if errores:
        return pintar_formulario(request, valores, errores, 422)
    receta_id = await run_in_threadpool(guardar_receta, datos)
    return RedirectResponse(f"/recetas/{receta_id}", status_code=303)


@app.get("/recetas/{receta_id}", response_class=HTMLResponse)
def detalle(request: Request, receta_id: int):
    receta = obtener_receta(receta_id)
    if not receta:
        raise HTTPException(404, "Ez da errezeta aurkitu")
    return templates.TemplateResponse(request, "detalle.html", {"receta": receta})


@app.get("/recetas/{receta_id}/editar", response_class=HTMLResponse)
def editar(request: Request, receta_id: int):
    receta = obtener_receta(receta_id)
    if not receta:
        raise HTTPException(404, "Ez da errezeta aurkitu")
    return pintar_formulario(request, receta)


@app.post("/recetas/{receta_id}")
async def actualizar(request: Request, receta_id: int):
    datos, errores, valores = await leer_formulario(request)
    if errores:
        valores["id"] = receta_id
        return pintar_formulario(request, valores, errores, 422)
    if await run_in_threadpool(guardar_receta, datos, receta_id) is None:
        raise HTTPException(404, "Ez da errezeta aurkitu")
    return RedirectResponse(f"/recetas/{receta_id}", status_code=303)


@app.post("/recetas/{receta_id}/borrar")
def borrar(receta_id: int):
    borrar_receta(receta_id)
    return RedirectResponse("/", status_code=303)


@app.get("/recetas/{receta_id}/imagen")
def imagen(receta_id: int):
    img = obtener_imagen(receta_id)
    if not img:
        return RedirectResponse("/static/placeholder.svg", status_code=302)
    return Response(content=bytes(img["datos"]), media_type=img["mime"],
                    headers={"Cache-Control": "no-cache"})


# ---------------------------------------------------------------
# API JSON
# ---------------------------------------------------------------

@app.get("/api/tags")
def api_tags():
    return listar_tags()


@app.get("/api/recetas")
def api_recetas(tag: list[str] = Query(default=[])):
    return listar_recetas(normalizar_tags(tag))


@app.get("/api/recetas/{receta_id}")
def api_receta(receta_id: int):
    receta = obtener_receta(receta_id)
    if not receta:
        raise HTTPException(404, "Ez da errezeta aurkitu")
    return receta


@app.post("/api/recetas/extraer")
async def api_extraer(request: Request):
    """Cuerpo: texto plano. Devuelve la receta extraída (no la guarda)."""
    texto = (await request.body()).decode("utf-8", errors="replace")
    try:
        return await run_in_threadpool(ia.extraer_receta, texto)
    except ia.ErrorIA as e:
        raise HTTPException(422, str(e))


@app.get("/health")
def health():
    with pool.connection() as conn:
        conn.execute("SELECT 1")
    return {"ok": True}
