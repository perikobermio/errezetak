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
import imagenes

# Sin DATABASE_URL, libpq usa las variables PGHOST, PGUSER, PGPASSWORD, PGDATABASE, PGPORT
# (así la contraseña puede llevar cualquier carácter sin romper una URL).
DATABASE_URL = os.environ.get("DATABASE_URL", "")
TAGS_DESTACADOS = 5

pool = ConnectionPool(DATABASE_URL, open=False, kwargs={"row_factory": dict_row})


MIGRACIONES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "migraciones.sql")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    pool.open(wait=True)
    with pool.connection() as conn, open(MIGRACIONES, encoding="utf-8") as f:
        conn.execute(f.read())
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
        SELECT r.id, r.titulo, r.descripcion, r.raciones, r.personas,
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


def listar_aparatos():
    """Catálogo de aparatos: [{codigo, nombre, icono}] en orden."""
    with pool.connection() as conn:
        return conn.execute("SELECT codigo, nombre, icono FROM aparatos ORDER BY orden, nombre").fetchall()


def recetas_parecidas(receta_id, limite=3):
    """Recetas que comparten tags con la dada, las de más tags en común primero."""
    with pool.connection() as conn:
        return conn.execute("""
            SELECT r.id, r.titulo, r.descripcion, r.raciones, r.personas,
                   COALESCE((SELECT sum(minutos) FROM tiempos_coccion WHERE receta_id = r.id), 0) AS minutos_totales,
                   ARRAY(SELECT t.nombre FROM receta_tags rt2 JOIN tags t ON t.id = rt2.tag_id
                         WHERE rt2.receta_id = r.id ORDER BY t.nombre) AS tags,
                   count(*) AS comunes
            FROM receta_tags rt JOIN recetas r ON r.id = rt.receta_id
            WHERE rt.tag_id IN (SELECT tag_id FROM receta_tags WHERE receta_id = %s) AND r.id <> %s
            GROUP BY r.id
            ORDER BY comunes DESC, r.creada_en DESC, r.id DESC
            LIMIT %s
        """, [receta_id, receta_id, limite]).fetchall()


def obtener_receta(receta_id):
    with pool.connection() as conn:
        receta = conn.execute(
            """SELECT id, titulo, descripcion, raciones, personas, creada_en,
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
        receta["consejos"] = [r["texto"] for r in conn.execute(
            "SELECT texto FROM consejos WHERE receta_id = %s ORDER BY orden", [receta_id])]
        receta["aparatos"] = [r["codigo"] for r in conn.execute(
            """SELECT a.codigo FROM receta_aparatos ra JOIN aparatos a ON a.id = ra.aparato_id
               WHERE ra.receta_id = %s ORDER BY a.orden""", [receta_id])]
        return receta


def guardar_receta(datos, receta_id=None):
    """Inserta o actualiza una receta y sus tablas dependientes en una transacción."""
    with pool.connection() as conn, conn.transaction():
        if receta_id is None:
            receta_id = conn.execute(
                """INSERT INTO recetas (titulo, descripcion, raciones, personas)
                   VALUES (%s, %s, %s, %s) RETURNING id""",
                [datos["titulo"], datos["descripcion"], datos["raciones"], datos["personas"]],
            ).fetchone()["id"]
        else:
            cur = conn.execute(
                "UPDATE recetas SET titulo = %s, descripcion = %s, raciones = %s, personas = %s WHERE id = %s",
                [datos["titulo"], datos["descripcion"], datos["raciones"], datos["personas"], receta_id],
            )
            if cur.rowcount == 0:
                return None
            for tabla in ("receta_tags", "ingredientes", "pasos_preparacion", "tiempos_coccion",
                          "consejos", "receta_aparatos"):
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
        for i, texto in enumerate(datos["consejos"], 1):
            conn.execute(
                "INSERT INTO consejos (receta_id, orden, texto) VALUES (%s, %s, %s)",
                [receta_id, i, texto])
        if datos["aparatos"]:
            conn.execute(
                """INSERT INTO receta_aparatos (receta_id, aparato_id)
                   SELECT %s, id FROM aparatos WHERE codigo = ANY(%s)""",
                [receta_id, datos["aparatos"]])

        if datos["imagen"]:
            nombre, mime, contenido, url_origen = datos["imagen"]
            conn.execute(
                """INSERT INTO imagenes (receta_id, nombre, mime, datos, url_origen) VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT (receta_id) DO UPDATE
                   SET nombre = EXCLUDED.nombre, mime = EXCLUDED.mime, datos = EXCLUDED.datos,
                       url_origen = EXCLUDED.url_origen""",
                [receta_id, nombre, mime, contenido, url_origen])
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

    def entero_positivo(campo, error):
        valor = (form.get(campo) or "").strip()
        if not valor:
            return None
        try:
            n = int(valor)
            if n <= 0:
                raise ValueError
            return n
        except ValueError:
            errores.append(error)
            return None

    raciones = entero_positivo("raciones", "Anoen kopuruak zenbaki oso positiboa izan behar du.")
    personas = entero_positivo("personas", "Pertsona kopuruak zenbaki oso positiboa izan behar du.")

    ingredientes = [
        (n.strip(), c.strip())
        for n, c in zip(form.getlist("ingrediente_nombre"), form.getlist("ingrediente_cantidad"))
        if n.strip()
    ]
    pasos = [p.strip() for p in form.getlist("paso") if p.strip()]
    consejos = [c.strip() for c in form.getlist("consejo") if c.strip()]
    aparatos = list(dict.fromkeys(a for a in form.getlist("aparato") if a))

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

    # Imagen: el archivo subido tiene prioridad; si no hay, se descarga de la URL.
    imagen = None
    archivo = form.get("imagen")
    imagen_url = (form.get("imagen_url") or "").strip()
    try:
        if archivo is not None and getattr(archivo, "filename", ""):
            contenido = await archivo.read(imagenes.MAX_BYTES + 1)
            if contenido:
                imagen = (archivo.filename[:255], imagenes.validar(contenido), contenido, None)
        elif imagen_url:
            imagen = (*await imagenes.descargar(imagen_url), imagen_url)
    except imagenes.ErrorImagen as e:
        errores.append(str(e))

    datos = {
        "titulo": titulo,
        "descripcion": (form.get("descripcion") or "").strip(),
        "raciones": raciones,
        "personas": personas,
        "tags": normalizar_tags((form.get("tags") or "").split(",")),
        "ingredientes": ingredientes,
        "pasos": pasos,
        "tiempos": tiempos,
        "consejos": consejos,
        "aparatos": aparatos,
        "imagen": imagen,
        "quitar_imagen": form.get("quitar_imagen") == "1",
    }
    valores = {
        **datos,
        "raciones": form.get("raciones") or "",
        "personas": form.get("personas") or "",
        "imagen_url": imagen_url,
        "ingredientes": [{"nombre": n, "cantidad": c} for n, c in ingredientes],
        "tiempos": [{"fase": f, "minutos": m} for f, m in tiempos],
    }
    return datos, errores, valores


def pintar_formulario(request, receta, errores=None, status_code=200, desde_ia=False):
    return templates.TemplateResponse(
        request, "formulario.html",
        {"receta": receta, "errores": errores or [], "desde_ia": desde_ia, "aparatos": listar_aparatos()},
        status_code=status_code)


# ---------------------------------------------------------------
# Rutas HTML
# ---------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def inicio(request: Request, tag: list[str] = Query(default=[])):
    seleccion = normalizar_tags(tag)
    tags = listar_tags()
    # Se muestran los tags con más recetas y, además, los seleccionados; el resto, en el buscador.
    destacados = sorted(tags, key=lambda t: (-t["total"], t["nombre"]))[:TAGS_DESTACADOS]
    destacados += [t for t in tags if t["nombre"] in seleccion and t not in destacados]
    destacados += [{"nombre": s, "total": 0} for s in seleccion if s not in {t["nombre"] for t in tags}]
    return templates.TemplateResponse(request, "index.html", {
        "recetas": listar_recetas(seleccion),
        "tags": tags,
        "destacados": destacados,
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
    return templates.TemplateResponse(request, "detalle.html", {
        "receta": receta,
        "aparatos": {a["codigo"]: a for a in listar_aparatos()},
        "parecidas": recetas_parecidas(receta_id),
    })


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
