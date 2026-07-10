"""Carga de suites YAML (`packages/evals/suites/*.yaml`) a `Suite` (WP-15).

Sin dependencia de paquetes hermanos: `pyyaml` + `edecan_evals.schema`.
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from edecan_evals.schema import Suite

logger = logging.getLogger(__name__)

# `edecan_evals/loader.py` -> parents[0] = edecan_evals/, parents[1] = packages/evals/
SUITES_DIR: Path = Path(__file__).resolve().parents[1] / "suites"

_EXTENSIONES = (".yaml", ".yml")


def listar_suites(*, directorio: Path | None = None) -> list[str]:
    """Nombres (sin extensión) de las suites disponibles en `directorio`, ordenados."""
    directorio = directorio or SUITES_DIR
    nombres = {ruta.stem for extension in _EXTENSIONES for ruta in directorio.glob(f"*{extension}")}
    return sorted(nombres)


def _ruta_suite(nombre: str, directorio: Path) -> Path:
    for extension in _EXTENSIONES:
        candidata = directorio / f"{nombre}{extension}"
        if candidata.exists():
            return candidata
    raise FileNotFoundError(
        f"No se encontró la suite {nombre!r} en {directorio} (se buscó "
        f"{', '.join(f'{nombre}{ext}' for ext in _EXTENSIONES)})."
    )


def cargar_suite(nombre: str, *, directorio: Path | None = None) -> Suite:
    """Carga y valida `<directorio>/<nombre>.yaml` (o `.yml`) como `Suite`.

    Lanza `FileNotFoundError` si el archivo no existe y
    `pydantic.ValidationError` si el YAML no respeta el esquema de `Suite`.
    """
    directorio = directorio or SUITES_DIR
    ruta = _ruta_suite(nombre, directorio)
    datos = yaml.safe_load(ruta.read_text(encoding="utf-8")) or {}
    if not isinstance(datos, dict):
        raise ValueError(f"{ruta} no contiene un mapeo YAML válido en la raíz.")

    suite = Suite.model_validate(datos)
    if suite.nombre != nombre:
        logger.warning(
            "La suite en %s declara nombre=%r pero el archivo se llama %r; se usa el nombre "
            "de archivo %r como clave.",
            ruta,
            suite.nombre,
            nombre,
            nombre,
        )
    return suite


def cargar_todas(*, directorio: Path | None = None) -> dict[str, Suite]:
    """Carga todas las suites de `directorio`, indexadas por nombre de archivo."""
    directorio = directorio or SUITES_DIR
    return {
        nombre: cargar_suite(nombre, directorio=directorio)
        for nombre in listar_suites(directorio=directorio)
    }
