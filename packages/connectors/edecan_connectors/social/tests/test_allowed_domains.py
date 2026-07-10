"""Verifica que toda URL usada por el código de `edecan_connectors.social`
apunte a dominios oficiales permitidos (Meta, X, Google/YouTube).

Solo se escanean los módulos de implementación en la raíz de `social/`
(`meta.py`, `x.py`, `youtube.py`, `_util.py`, `__init__.py`), no `tests/`:
los tests usan legítimamente URLs de ejemplo ajenas a estos dominios como
valores de parámetros que en producción provee el tenant (p. ej. un
`redirect_uri` propio de la app del cliente, o la `image_url` de una foto
que el tenant quiere publicar) — no son llamadas que este paquete origine.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

URL_RE = re.compile(r"https://[^\s\"'<>)\]}]+")

DOMINIOS_PERMITIDOS = {
    "facebook.com",
    "graph.facebook.com",
    "twitter.com",
    "api.twitter.com",
    "googleapis.com",
    "accounts.google.com",
}


def _dominio_permitido(host: str) -> bool:
    return any(host == dominio or host.endswith(f".{dominio}") for dominio in DOMINIOS_PERMITIDOS)


def _carpeta_social() -> Path:
    # .../social/tests/test_allowed_domains.py -> parents[0]=tests [1]=social
    return Path(__file__).resolve().parents[1]


def test_urls_de_los_modulos_de_social_son_de_dominios_oficiales():
    carpeta = _carpeta_social()
    assert carpeta.name == "social", f"ruta inesperada, revisa el layout: {carpeta}"

    urls_vistas: set[str] = set()
    for ruta in sorted(carpeta.glob("*.py")):  # no recursivo: excluye tests/
        texto = ruta.read_text(encoding="utf-8")
        for match in URL_RE.finditer(texto):
            urls_vistas.add(match.group(0).rstrip(".,;"))

    assert urls_vistas, "no se encontró ninguna URL: revisa el patrón de búsqueda o el layout"

    ofensoras = []
    for url in sorted(urls_vistas):
        host = urlparse(url).hostname or ""
        if not _dominio_permitido(host):
            ofensoras.append(url)

    assert not ofensoras, f"URLs fuera de los dominios oficiales permitidos: {ofensoras}"
