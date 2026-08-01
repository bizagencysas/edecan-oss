"""Criterio de `edecan-detect-ollama-barra-final`.

`detect_local_providers` arma la URL de Ollama concatenando
`f"{base_url}/api/tags"`. Si el usuario configuró `OLLAMA_BASE_URL` con barra
final —lo que escribe cualquiera al copiar de un navegador— sale
`http://localhost:11434//api/tags` y la detección falla en silencio: Ollama
está corriendo y la pantalla de Configuración dice que no.

Falla hoy: la URL pedida trae la barra doble. No usa red: sustituye
`httpx.get` dentro del módulo por un doble que registra la URL.
"""

from __future__ import annotations

import sys
from typing import Any

from edecan_llm import detect


class _RespuestaFalsa:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return {"models": [{"name": "llama3.2:3b"}]}


class _Ajustes:
    def __init__(self, base_url: str) -> None:
        self.OLLAMA_BASE_URL = base_url  # noqa: N815 - imita el nombre real del setting


def _detectar(base_url: str) -> tuple[str, dict[str, Any]]:
    pedidas: list[str] = []

    def _get_falso(url: str, **_kwargs: Any) -> _RespuestaFalsa:
        pedidas.append(url)
        return _RespuestaFalsa()

    original = detect.httpx.get
    detect.httpx.get = _get_falso  # type: ignore[assignment]
    try:
        resultado = detect.detect_local_providers(_Ajustes(base_url))
    finally:
        detect.httpx.get = original  # type: ignore[assignment]
    return (pedidas[0] if pedidas else ""), resultado["ollama"]


def main() -> int:
    for base_url in ("http://localhost:11434/", "http://127.0.0.1:11434///"):
        url, ollama = _detectar(base_url)
        if url != "http://localhost:11434/api/tags" and url != "http://127.0.0.1:11434/api/tags":
            print(f"con OLLAMA_BASE_URL={base_url!r} se pidió {url!r}")
            return 1
        if not ollama["running"] or ollama["models"] != ["llama3.2:3b"]:
            print(f"con OLLAMA_BASE_URL={base_url!r} la detección devolvió {ollama!r}")
            return 1
        if ollama["base_url"].endswith("/"):
            print(f"base_url reportada sin normalizar: {ollama['base_url']!r}")
            return 1

    url, _ = _detectar("http://localhost:11434")
    if url != "http://localhost:11434/api/tags":
        print(f"sin barra final se pidió {url!r}")
        return 1

    print("ok: la barra final de OLLAMA_BASE_URL ya no rompe la detección")
    return 0


if __name__ == "__main__":
    sys.exit(main())
