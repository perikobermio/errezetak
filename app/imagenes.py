"""Validación y descarga de imágenes de recetas.

La imagen se identifica por su contenido (no por la extensión ni la cabecera) y
solo se admiten formatos raster: un SVG podría llevar scripts.
Las descargas por URL solo van a IPs públicas, para que el servidor no pueda
usarse para llegar a servicios internos (Ollama, Postgres, la red corporativa…).
"""
import asyncio
import ipaddress
import os
from urllib.parse import urljoin, urlparse

import httpx

MAX_BYTES = 5 * 1024 * 1024
TIMEOUT = 15
MAX_REDIRECCIONES = 3


class ErrorImagen(Exception):
    """El mensaje se muestra al usuario."""


def detectar_mime(datos: bytes):
    if datos.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if datos.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if datos[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if datos[:4] == b"RIFF" and datos[8:12] == b"WEBP":
        return "image/webp"
    if datos[4:8] == b"ftyp" and datos[8:12] in (b"avif", b"avis"):
        return "image/avif"
    return None


def validar(datos: bytes) -> str:
    if len(datos) > MAX_BYTES:
        raise ErrorImagen("Irudiak ezin ditu 5 MB gainditu.")
    mime = detectar_mime(datos)
    if not mime:
        raise ErrorImagen("Fitxategia ez da irudi onartua (JPEG, PNG, GIF, WebP edo AVIF).")
    return mime


async def _comprobar_destino(url: str):
    partes = urlparse(url)
    if partes.scheme not in ("http", "https") or not partes.hostname:
        raise ErrorImagen("URLak http:// edo https:// izan behar du.")
    try:
        direcciones = await asyncio.get_running_loop().getaddrinfo(
            partes.hostname, partes.port or (443 if partes.scheme == "https" else 80))
    except OSError:
        raise ErrorImagen(f"Ezin da zerbitzaria aurkitu: {partes.hostname}")
    for *_, sockaddr in direcciones:
        if not ipaddress.ip_address(sockaddr[0]).is_global:
            raise ErrorImagen("URL horretara ezin da sartu (helbide pribatua edo lokala).")


async def descargar(url: str):
    """Devuelve (nombre, mime, datos). Sigue hasta 3 redirecciones, comprobando cada destino."""
    url = url.strip()
    # trust_env: en dev las descargas salen por el proxy corporativo (HTTPS_PROXY).
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False, trust_env=True,
                                 headers={"User-Agent": "Errezetak/1.0"}) as cliente:
        for _ in range(MAX_REDIRECCIONES + 1):
            await _comprobar_destino(url)
            try:
                async with cliente.stream("GET", url) as r:
                    if r.is_redirect:
                        url = urljoin(url, r.headers.get("location", ""))
                        continue
                    if r.status_code != 200:
                        raise ErrorImagen(f"Ezin izan da irudia deskargatu (HTTP {r.status_code}).")
                    datos = bytearray()
                    async for trozo in r.aiter_bytes():
                        datos += trozo
                        if len(datos) > MAX_BYTES:
                            raise ErrorImagen("Irudiak ezin ditu 5 MB gainditu.")
            except httpx.HTTPError as e:
                raise ErrorImagen(f"Ezin izan da irudia deskargatu: {e}") from e
            datos = bytes(datos)
            nombre = os.path.basename(urlparse(url).path)[:255] or "irudia"
            return nombre, validar(datos), datos
    raise ErrorImagen("Birbideratze gehiegi.")
