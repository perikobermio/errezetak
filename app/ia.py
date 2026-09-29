"""Extracción de recetas desde texto plano con un LLM.

Proveedor según LLM_PROVIDER:
  - "anthropic": API de Claude (dev). Requiere ANTHROPIC_API_KEY.
  - "ollama": servidor Ollama local (producción). Usa OLLAMA_URL y OLLAMA_MODEL.
Los dos devuelven el mismo dict, validado por normalizar().
"""
import json
import logging
import os
import re
import unicodedata

import httpx

log = logging.getLogger("uvicorn.error")

PROVIDER = os.environ.get("LLM_PROVIDER", "anthropic").strip().lower()
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-opus-5-5")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b")
TIMEOUT = float(os.environ.get("LLM_TIMEOUT", "300"))
MAX_TEXTO = 20_000


class ErrorIA(Exception):
    """Fallo al extraer la receta; el mensaje se muestra al usuario."""


APARATOS = ["airfryer", "horno", "microondas", "sarten"]

ESQUEMA = {
    "type": "object",
    "properties": {
        "titulo": {"type": "string", "description": "Nombre de la receta"},
        "descripcion": {"type": "string", "description": "Resumen breve (1-2 frases). Vacío si el texto no lo da."},
        "raciones": {"type": "integer", "description": "Número de raciones o unidades (p. ej. 6 tortitas); 0 si no se indica"},
        "personas": {"type": "integer", "description": "Para cuántas personas es; 0 si no se indica"},
        "tags": {
            "type": "array", "items": {"type": "string"},
            "description": "3-6 etiquetas cortas en minúsculas: tipo de plato, ingrediente principal, técnica, dieta",
        },
        "ingredientes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "nombre": {"type": "string"},
                    "cantidad": {"type": "string", "description": "Cantidad con unidad, p. ej. '200 g'. Vacío si no se indica"},
                },
                "required": ["nombre", "cantidad"],
            },
        },
        "pasos": {"type": "array", "items": {"type": "string"}, "description": "Pasos de preparación en orden"},
        "tiempos": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "fase": {"type": "string", "description": "p. ej. Preparación, Horno, Reposo"},
                    "minutos": {"type": "integer"},
                },
                "required": ["fase", "minutos"],
            },
            "description": "Tiempos por fase en minutos (convierte horas a minutos)",
        },
        "consejos": {"type": "array", "items": {"type": "string"},
                     "description": "Tips, trucos, sustituciones o notas del texto; lista vacía si no hay"},
        "aparatos": {"type": "array", "items": {"type": "string", "enum": APARATOS},
                     "description": "Aparatos que se usan para cocinar; lista vacía si no se usa ninguno de estos"},
    },
    "required": ["titulo", "descripcion", "raciones", "personas", "tags", "ingredientes", "pasos", "tiempos",
                 "consejos", "aparatos"],
}

INSTRUCCIONES = """Extraes recetas de cocina de un texto libre y las devuelves estructuradas.
Reglas:
- Usa solo la información del texto. No inventes ingredientes, cantidades ni pasos.
- Mantén el idioma del texto original.
- Separa cada ingrediente con su cantidad; si no hay cantidad, deja la cantidad vacía.
- Divide la preparación en pasos claros, en orden, sin numerarlos.
- Los tiempos van en minutos enteros por fase. Si el texto solo da un tiempo total, usa una única fase.
  Si no menciona tiempos, devuelve una lista vacía.
- Las etiquetas van en minúsculas, cortas y sin '#'.
- "raciones" son las unidades o raciones que salen; "personas", para cuántas personas es. Si el texto
  no lo dice, pon 0. No deduzcas uno del otro.
- "consejos": los tips, trucos, sustituciones o notas que aparezcan (no los pasos de la preparación).
- "aparatos": solo de esta lista y solo si el texto los usa: airfryer (freidora de aire), horno,
  microondas, sarten."""


def extraer_receta(texto: str) -> dict:
    texto = (texto or "").strip()
    if not texto:
        raise ErrorIA("Testua hutsik dago.")
    if len(texto) > MAX_TEXTO:
        raise ErrorIA(f"Testua luzeegia da ({MAX_TEXTO} karaktere gehienez).")
    if PROVIDER == "anthropic":
        datos = _anthropic(texto)
    elif PROVIDER == "ollama":
        datos = _ollama(texto)
    else:
        raise ErrorIA(f"LLM_PROVIDER ezezaguna: {PROVIDER!r} (anthropic edo ollama).")
    try:
        return normalizar(datos)
    except ErrorIA:
        # La respuesta cruda en los logs (docker compose logs web) ayuda a ver qué devolvió el modelo.
        log.warning("Respuesta del modelo no reconocida como receta: %s",
                    json.dumps(datos, ensure_ascii=False)[:3000])
        raise


def _anthropic(texto):
    import anthropic

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise ErrorIA("ANTHROPIC_API_KEY ez dago konfiguratuta.")
    # Forzar la herramienta garantiza una salida JSON que cumple el esquema.
    try:
        respuesta = anthropic.Anthropic(timeout=TIMEOUT).messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=4096,
            system=INSTRUCCIONES,
            tools=[{"name": "guardar_receta", "description": "Guarda la receta extraída del texto.",
                    "input_schema": ESQUEMA}],
            tool_choice={"type": "tool", "name": "guardar_receta"},
            messages=[{"role": "user", "content": f"<texto>\n{texto}\n</texto>"}],
        )
    except anthropic.APIError as e:
        raise ErrorIA(f"Claude-ren errorea: {e}") from e
    for bloque in respuesta.content:
        if bloque.type == "tool_use":
            return bloque.input
    raise ErrorIA("Claude-k ez du errezetarik itzuli.")


def _ollama(texto):
    # trust_env=False: Ollama está en la red interna, no debe pasar por el proxy corporativo.
    try:
        with httpx.Client(timeout=TIMEOUT, trust_env=False) as cliente:
            r = cliente.post(f"{OLLAMA_URL}/api/chat", json={
                "model": OLLAMA_MODEL,
                "stream": False,
                "format": ESQUEMA,
                "options": {"temperature": 0},
                "messages": [
                    {"role": "system", "content": INSTRUCCIONES},
                    # El esquema también en el prompt: no todos los modelos (p. ej. los cloud) aplican `format`.
                    {"role": "user", "content": f"<texto>\n{texto}\n</texto>\n\n"
                                                "Devuelve solo un objeto JSON que cumpla este esquema, "
                                                "con estas claves exactas:\n"
                                                + json.dumps(ESQUEMA, ensure_ascii=False)},
                ],
            })
            if r.status_code == 404:
                # Ollama responde 404 cuando el modelo no está instalado.
                raise ErrorIA(f"Ollama-k ez du '{OLLAMA_MODEL}' eredua. Jarri OLLAMA_MODEL-en instalatutako "
                              f"eredu bat: {_modelos_ollama(cliente) or '(ez dago eredurik: ollama pull …)'}")
            if r.status_code != 200:
                raise ErrorIA(f"Ollama-ren errorea ({r.status_code}): {_mensaje_error(r)}")
    except httpx.HTTPError as e:
        raise ErrorIA(f"Ezin izan da Ollama-rekin konektatu ({OLLAMA_URL}): {e}") from e
    try:
        mensaje = r.json()["message"]
    except (KeyError, ValueError) as e:
        raise ErrorIA("Ollama-k ez du erantzun baliozkorik itzuli.") from e
    # Algunos modelos de razonamiento dejan la respuesta en `thinking` si `content` viene vacío.
    for contenido in (mensaje.get("content"), mensaje.get("thinking")):
        datos = _json_de_texto(contenido)
        if datos is not None:
            return datos
    log.warning("Respuesta de Ollama sin JSON: %s", json.dumps(mensaje, ensure_ascii=False)[:3000])
    raise ErrorIA("Ollama-k ez du JSON baliozkorik itzuli.")


def _json_de_texto(contenido):
    """JSON de la respuesta, tolerando bloques ```json``` o texto alrededor."""
    if not isinstance(contenido, str) or not contenido.strip():
        return None
    try:
        return json.loads(contenido)
    except ValueError:
        pass
    inicio, fin = contenido.find("{"), contenido.rfind("}")
    if inicio != -1 and fin > inicio:
        try:
            return json.loads(contenido[inicio:fin + 1])
        except ValueError:
            pass
    return None


def _modelos_ollama(cliente):
    try:
        return ", ".join(m["name"] for m in cliente.get(f"{OLLAMA_URL}/api/tags").json()["models"])
    except (httpx.HTTPError, KeyError, ValueError):
        return ""


def _mensaje_error(r):
    try:
        return r.json()["error"]
    except (KeyError, ValueError):
        return r.text[:300]


# Nombres alternativos que usan algunos modelos cuando no respetan el esquema.
ALIAS = {
    "titulo": ("titulo", "title", "nombre", "name", "recipe_name", "nombre_receta", "izenburua"),
    "descripcion": ("descripcion", "description", "resumen", "summary", "deskribapena"),
    "raciones": ("raciones", "servings", "porciones", "unidades", "yield", "rendimiento", "anoak"),
    "personas": ("personas", "comensales", "people", "persons", "diners", "para_cuantas_personas", "pertsonak"),
    "consejos": ("consejos", "tips", "trucos", "notas", "notes", "sugerencias", "consejo", "aholkuak"),
    "aparatos": ("aparatos", "electrodomesticos", "appliances", "utensilios", "equipment", "equipo", "tresnak"),
    "tags": ("tags", "etiquetas", "categorias", "categories", "keywords", "etiketak"),
    "ingredientes": ("ingredientes", "ingredients", "osagaiak"),
    "pasos": ("pasos", "steps", "instrucciones", "instructions", "preparacion", "pasos_preparacion",
              "elaboracion", "method", "directions", "prestaketa", "urratsak"),
    "tiempos": ("tiempos", "times", "tiempos_coccion", "timings", "tiempo", "denborak"),
}
# Palabras que identifican cada aparato en la respuesta del modelo.
PALABRAS_APARATO = {
    "airfryer": ("airfryer", "air_fryer", "air fryer", "freidora de aire", "freidora", "aire-frijigailu"),
    "horno": ("horno", "oven", "labe"),
    "microondas": ("microondas", "microwave", "mikrouhin"),
    "sarten": ("sarten", "pan", "skillet", "frying", "plancha", "zartagin"),
}
CLAVES_INGREDIENTE = ("nombre", "name", "ingrediente", "ingredient", "item", "producto")
CLAVES_CANTIDAD = ("cantidad", "quantity", "amount", "qty", "medida", "kantitatea")
CLAVES_PASO = ("texto", "text", "descripcion", "description", "paso", "step", "instruccion", "instruction")
CLAVES_FASE = ("fase", "phase", "etapa", "nombre", "name", "tipo", "type", "descripcion", "label")
CLAVES_MINUTOS = ("minutos", "minutes", "duracion", "duration", "tiempo", "time", "min")


def _clave(k):
    k = unicodedata.normalize("NFKD", str(k)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "_", k).strip("_")


def _campo(d, alias):
    if not isinstance(d, dict):
        return None
    claves = {_clave(k): v for k, v in d.items()}
    for a in alias:
        if a in claves:
            return claves[a]
    return None


def _desenvolver(d):
    """Acepta {"receta": {...}}, [{...}] y similares."""
    for _ in range(3):
        if isinstance(d, list) and len(d) == 1:
            d = d[0]
        elif isinstance(d, dict) and not any(_campo(d, al) is not None for al in ALIAS.values()):
            anidados = [v for v in d.values() if isinstance(v, (dict, list))]
            if len(anidados) != 1:
                break
            d = anidados[0]
        else:
            break
    return d


def normalizar(d) -> dict:
    """Convierte la salida del modelo al formato del formulario, descartando basura."""
    d = _desenvolver(d)
    if not isinstance(d, dict):
        raise ErrorIA("Ereduaren erantzunak ez du formatu egokia.")

    def texto(v):
        return v.strip() if isinstance(v, str) else ("" if v is None else str(v).strip())

    def entero(v):
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return int(v)
        m = re.search(r"\d+", texto(v))  # "6 tortitas", "30 min"
        return int(m.group()) if m else None

    def lista(v):
        if isinstance(v, list):
            return v
        if isinstance(v, str):
            return [x for x in re.split(r"\n+", v) if x.strip()]
        return []

    raciones = entero(_campo(d, ALIAS["raciones"]))
    personas = entero(_campo(d, ALIAS["personas"]))

    tags = []
    valor_tags = _campo(d, ALIAS["tags"])
    for t in (valor_tags.split(",") if isinstance(valor_tags, str) else lista(valor_tags)):
        t = texto(t).lstrip("#").lower()
        if t and t not in tags:
            tags.append(t[:50])

    ingredientes = []

    def anadir_ingrediente(i):
        if isinstance(i, str):
            nombre, cantidad = i, ""
        elif isinstance(i, dict):
            grupo = _campo(i, ALIAS["ingredientes"])
            if isinstance(grupo, list):  # {"grupo": "Para acompañar", "ingredientes": [...]}
                for sub in grupo:
                    anadir_ingrediente(sub)
                return
            nombre, cantidad = _campo(i, CLAVES_INGREDIENTE), _campo(i, CLAVES_CANTIDAD)
            unidad = _campo(i, ("unidad", "unit"))
            if unidad and cantidad is not None:
                cantidad = f"{texto(cantidad)} {texto(unidad)}"
        else:
            return
        nombre = texto(nombre).lstrip("-•* ").strip()
        if nombre:
            ingredientes.append({"nombre": nombre[:200], "cantidad": texto(cantidad)[:100]})

    for i in lista(_campo(d, ALIAS["ingredientes"])):
        anadir_ingrediente(i)

    pasos = []
    for p in lista(_campo(d, ALIAS["pasos"])):
        p = _campo(p, CLAVES_PASO) if isinstance(p, dict) else p
        p = re.sub(r"^\s*(\d+[.)-]|[-•*])\s*", "", texto(p))
        if p:
            pasos.append(p)

    tiempos = []
    for t in lista(_campo(d, ALIAS["tiempos"])):
        if isinstance(t, dict):
            fase, m = texto(_campo(t, CLAVES_FASE)), entero(_campo(t, CLAVES_MINUTOS))
            if fase and m is not None and m >= 0:
                tiempos.append({"fase": fase[:100], "minutos": m})

    consejos = []
    for c in lista(_campo(d, ALIAS["consejos"])):
        c = _campo(c, CLAVES_PASO) if isinstance(c, dict) else c
        c = re.sub(r"^\s*[-•*]\s*", "", texto(c))
        if c:
            consejos.append(c)

    aparatos = []
    valor_aparatos = _campo(d, ALIAS["aparatos"])
    for a in (valor_aparatos.split(",") if isinstance(valor_aparatos, str) else lista(valor_aparatos)):
        a = unicodedata.normalize("NFKD", texto(a)).encode("ascii", "ignore").decode().lower()
        for codigo, palabras in PALABRAS_APARATO.items():
            if any(p in a for p in palabras) and codigo not in aparatos:
                aparatos.append(codigo)
                break

    receta = {
        "titulo": texto(_campo(d, ALIAS["titulo"]))[:200],
        "descripcion": texto(_campo(d, ALIAS["descripcion"])),
        "raciones": raciones if raciones and raciones > 0 else None,
        "personas": personas if personas and personas > 0 else None,
        "tags": tags,
        "ingredientes": ingredientes,
        "pasos": pasos,
        "tiempos": tiempos,
        "consejos": consejos,
        "aparatos": aparatos,
    }
    if not receta["titulo"] and not ingredientes and not pasos:
        raise ErrorIA("Ez da errezetarik aurkitu testuan.")
    return receta


def descripcion_proveedor() -> str:
    return f"Claude ({ANTHROPIC_MODEL})" if PROVIDER == "anthropic" else f"Ollama ({OLLAMA_MODEL})"
