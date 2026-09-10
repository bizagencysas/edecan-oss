"""Registro del handler `create_organization_linkedin_post` en `HANDLERS`.

El job type sanitizado es `create_organization_linkedin_post`, pero el archivo
del handler es `create_organization_social_post.py`. El registro defensivo en
`edecan_worker.handlers` debe mapear ese job type al MÓDULO correcto (el 3er
argumento de `_register_defensive` es el nombre de módulo, no el job type).

Regresión del bug donde el nombre de módulo no coincidía con el archivo
(`create_organization_linkedin_post` como nombre de módulo, que no existe en
disco) y el handler quedaba sin registrar — sólo un `logger.warning` silencioso,
sin ningún test que lo atrapara.
"""

from __future__ import annotations

import inspect

from edecan_schemas import JOB_TYPES
from edecan_worker.handlers import HANDLERS

_JOB_TYPE = "create_organization_linkedin_post"


def test_job_type_esta_en_job_types() -> None:
    assert _JOB_TYPE in JOB_TYPES


def test_handler_esta_registrado() -> None:
    assert _JOB_TYPE in HANDLERS
    assert inspect.iscoroutinefunction(HANDLERS[_JOB_TYPE])
    # El handler vive en el módulo real en disco (`create_organization_social_post`),
    # no en un nombre de módulo inexistente.
    assert (
        HANDLERS[_JOB_TYPE].__module__
        == "edecan_worker.handlers.create_organization_social_post"
    )