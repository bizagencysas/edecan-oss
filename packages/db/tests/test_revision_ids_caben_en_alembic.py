"""Ningún revision id puede pasar de 32 caracteres. NUNCA.

`alembic_version.version_num` es `varchar(32)`. Un id más largo no falla al escribir la
migración ni al correr los tests del paquete: falla AL ESTAMPARSE en la base real
(`StringDataRightTruncationError`), o sea en el ARRANQUE del backend -- y como las
migraciones corren en el boot de la app instalada, el fallo es "la app quedó sin motor",
no "un test rojo". Pasó el 02-ago-2026 con `0031_conversation_context_cleared` (33
caracteres): la app notarizada arrancaba y el backend moría en silencio aplicando la
migración. Este test convierte ese fallo de producción en un test rojo de un segundo.
"""

from __future__ import annotations

import re
from pathlib import Path

_VERSIONS = Path(__file__).parents[1] / "alembic" / "versions"
_RE_REVISION = re.compile(r'^revision(?::\s*str)?\s*=\s*["\']([^"\']+)["\']', re.MULTILINE)

# El límite REAL de `alembic_version.version_num` (varchar(32)).
_MAX_CHARS_ALEMBIC = 32


def test_todos_los_revision_ids_caben_en_version_num() -> None:
    archivos = sorted(_VERSIONS.glob("[0-9]*.py"))
    assert archivos, "no se encontraron migraciones; ¿se movió el directorio?"
    largos: list[str] = []
    for archivo in archivos:
        match = _RE_REVISION.search(archivo.read_text(encoding="utf-8"))
        assert match, f"{archivo.name} no declara `revision = ...`"
        rid = match.group(1)
        if len(rid) > _MAX_CHARS_ALEMBIC:
            largos.append(f"{archivo.name}: '{rid}' ({len(rid)} chars)")
    assert not largos, (
        "Estos revision ids NO caben en alembic_version.version_num (varchar(32)) y van a "
        "tumbar el arranque del backend al estamparse:\n- " + "\n- ".join(largos)
    )
