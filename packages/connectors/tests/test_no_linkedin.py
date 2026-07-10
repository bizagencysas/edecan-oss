"""Guardrail obligatorio (`ARCHITECTURE.md` §0.2 y §10.8): esta plataforma está
excluida permanentemente y en cualquier forma (código, scopes, URLs, texto de
UI, docs) de `packages/connectors/`. Este test escanea todo el árbol del
paquete y falla si la palabra prohibida aparece en cualquier archivo.

Nota sobre el nombre del archivo: `ARCHITECTURE.md` §0.2 pinea el nombre
`test_no_linkedin` para este guardrail. Este propio archivo necesita nombrar
la palabra vetada para construir su patrón de búsqueda, así que se excluye a
sí mismo del escaneo. La exclusión se hace por nombre de archivo (no solo por
la ruta exacta de este módulo) de forma defensiva: si en el futuro apareciera
un guardrail hermano con este mismo nombre pinned en otra ruta del árbol
(por ejemplo, dentro de un submódulo opcional como `edecan_connectors/social/`),
tampoco produciría un falso positivo cruzado. Hoy no existe tal archivo
hermano en el repo — solo este.
"""

from __future__ import annotations

from pathlib import Path

FORBIDDEN = "linkedin"

_SKIP_DIR_NAMES = {
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
}

# Nombre pinned de este guardrail (`ARCHITECTURE.md` §0.2). Cualquier archivo
# con este nombre exacto, en cualquier parte del árbol, sería un test
# guardrail hermano (no una integración real) — ver nota del docstring del
# módulo. Hoy solo existe este archivo; la exclusión por nombre es defensiva.
_GUARDRAIL_FILENAME = "test_no_linkedin.py"


def _package_root() -> Path:
    # tests/test_no_linkedin.py -> tests/ -> packages/connectors/
    return Path(__file__).resolve().parents[1]


def _iter_source_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.name == _GUARDRAIL_FILENAME:
            continue
        if any(part in _SKIP_DIR_NAMES or part.endswith(".egg-info") for part in path.parts):
            continue
        files.append(path)
    return files


def test_no_linkedin() -> None:
    root = _package_root()
    offenders = []
    for path in _iter_source_files(root):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except (UnicodeDecodeError, OSError):
            continue
        if FORBIDDEN in text.lower():
            offenders.append(str(path.relative_to(root)))

    assert not offenders, (
        "Encontrada una mención prohibida en: "
        + ", ".join(offenders)
        + " (ver ARCHITECTURE.md §0.2)"
    )
