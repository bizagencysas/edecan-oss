"""Configuración de la suite de `edecan-forge-probe`.

El paquete YA es miembro del workspace de uv, así que `uv sync --all-packages` lo
instala y los imports funcionan sin ayuda. La inserción en `sys.path` se conserva
como red de seguridad para quien corra la suite sobre un entorno sincronizado con
`uv sync` a secas —que sólo instala el paquete raíz— en vez de `--all-packages`.

Aquí vive también la única defensa que importa de verdad en esta suite: **ningún
test toca la red**. Cada llamada a Workers AI cuesta dinero real, así que toda la
suite corre dentro de un router de respx con `assert_all_mocked`: una petición
que nadie haya declarado explícitamente no sale a internet, revienta el test.

Los tests que sí necesitan red se marcan con `@requiere_integracion` y se saltan
salvo que esté puesta `FORGE_PROBE_INTEGRACION=1`. Tener el token en el entorno
NO es condición suficiente: eso convertiría cualquier `pytest` distraído en una
factura.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
import respx

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))


INTEGRACION_ACTIVA = os.environ.get("FORGE_PROBE_INTEGRACION") == "1"

requiere_integracion = pytest.mark.skipif(
    not INTEGRACION_ACTIVA,
    reason=(
        "Test de integración: gasta dinero real. Actívalo con FORGE_PROBE_INTEGRACION=1. "
        "Tener el token en el entorno NO basta como condición."
    ),
)


@pytest.fixture(autouse=True)
def red() -> Iterator[respx.MockRouter | None]:
    """Router de respx activo en toda la suite; sin él no hay salida a la red."""
    if INTEGRACION_ACTIVA:
        yield None
        return
    with respx.mock(assert_all_called=False) as router:
        yield router


@pytest.fixture(autouse=True)
def entorno_limpio(monkeypatch: pytest.MonkeyPatch) -> None:
    """Aísla la suite del `.env` real del repositorio.

    Sin esto, un test que ejecute el CLI cargaría el token de verdad. En ningún
    momento se imprime el valor de ninguna de estas variables.
    """
    for clave in ("CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID", "FORGE_PROBE_MODEL"):
        monkeypatch.delenv(clave, raising=False)
