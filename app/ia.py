"""Extracción de recetas desde texto plano con un LLM.

Proveedor según LLM_PROVIDER:
  - "anthropic": API de Claude (dev). Requiere ANTHROPIC_API_KEY.
  - "ollama": servidor Ollama local (producción). Usa OLLAMA_URL y OLLAMA_MODEL.
Los dos devuelven el mismo dict, validado por normalizar().
"""
import json
import os

import httpx

PROVIDER = os.environ.get("LLM_PROVIDER", "anthropic").strip().lower()
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-opus-5-5")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://host.docker.internal:11434").rstrip("/")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b")
TIMEOUT = float(os.environ.get("LLM_TIMEOUT", "300"))
MAX_TEXTO = 20_000


class ErrorIA(Exception):
    """Fallo al extraer la receta; el mensaje se muestra al usuario."""


ESQUEMA = {
    "type": "object",
    "properties": {
        "titulo": {"type": "string", "description": "Nombre de la receta"},
        "descripcion": {"type": "string", "description": "Resumen breve (1-2 frases). Vacío si el texto no lo da."},
        "raciones": {"type": "integer", "description": "Número de raciones/personas; 0 si no se indica"},
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
    },
    "required": ["titulo", "descripcion", "raciones", "tags", "ingredientes", "pasos", "tiempos"],
}

INSTRUCCIONES = """Extraes recetas de cocina de un texto libre y las devuelves estructuradas.
Reglas:
- Usa solo la información del texto. No inventes ingredientes, cantidades ni pasos.
- Mantén el idioma del texto original.
- Separa cada ingrediente con su cantidad; si no hay cantidad, deja la cantidad vacía.
- Divide la preparación en pasos claros, en orden, sin numerarlos.
- Los tiempos van en minutos enteros por fase. Si el texto solo da un tiempo total, usa una única fase.
  Si no menciona tiempos, devuelve una lista vacía.
- Las etiquetas van en minúsculas, cortas y sin '#'."""


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
    return normalizar(datos)


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
                    {"role": "user", "content": f"<texto>\n{texto}\n</texto>\n\nDevuelve solo el JSON."},
                ],
            })
            r.raise_for_status()
    except httpx.HTTPError as e:
        raise ErrorIA(f"Ezin izan da Ollama-rekin konektatu ({OLLAMA_URL}): {e}") from e
    try:
        return json.loads(r.json()["message"]["content"])
    except (KeyError, ValueError) as e:
        raise ErrorIA("Ollama-k ez du JSON baliozkorik itzuli.") from e


def normalizar(d) -> dict:
    """Convierte la salida del modelo al formato del formulario, descartando basura."""
    if not isinstance(d, dict):
        raise ErrorIA("Ereduaren erantzunak ez du formatu egokia.")

    def texto(v):
        return v.strip() if isinstance(v, str) else ("" if v is None else str(v).strip())

    def entero(v):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return None

    raciones = entero(d.get("raciones"))
    tags = []
    for t in d.get("tags") or []:
        t = texto(t).lstrip("#").lower()
        if t and t not in tags:
            tags.append(t[:50])
    ingredientes = [
        {"nombre": texto(i.get("nombre"))[:200], "cantidad": texto(i.get("cantidad"))[:100]}
        for i in d.get("ingredientes") or [] if isinstance(i, dict) and texto(i.get("nombre"))
    ]
    pasos = [texto(p) for p in d.get("pasos") or [] if texto(p)]
    tiempos = []
    for t in d.get("tiempos") or []:
        if isinstance(t, dict) and texto(t.get("fase")) and (m := entero(t.get("minutos"))) is not None and m >= 0:
            tiempos.append({"fase": texto(t["fase"])[:100], "minutos": m})

    receta = {
        "titulo": texto(d.get("titulo"))[:200],
        "descripcion": texto(d.get("descripcion")),
        "raciones": raciones if raciones and raciones > 0 else None,
        "tags": tags,
        "ingredientes": ingredientes,
        "pasos": pasos,
        "tiempos": tiempos,
    }
    if not receta["titulo"] and not ingredientes and not pasos:
        raise ErrorIA("Ez da errezetarik aurkitu testuan.")
    return receta


def descripcion_proveedor() -> str:
    return f"Claude ({ANTHROPIC_MODEL})" if PROVIDER == "anthropic" else f"Ollama ({OLLAMA_MODEL})"
