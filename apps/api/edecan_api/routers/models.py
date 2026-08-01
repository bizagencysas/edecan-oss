"""`GET /v1/models/chat` — catálogo único del selector de modelos del chat.

Existe para que NADIE duplique la lista en el cliente: la hoja "Seleccionar
modelo" de iOS, la de la web y cualquier otra superficie leen de aquí qué
modelos hay, cuáles van en la portada (`principal`), cuáles ven imágenes
(`ve_imagenes`, el aviso de ceguera) y a cuáles les corresponde la fila
Esfuerzo (`soporta_esfuerzo`). La autoridad de la lista es
`config/modelos.yml` -> `modelos_chat`, leída en runtime por
`edecan_llm.task_router` (ver `modelos_chat_disponibles`): agregar o retirar un
modelo del selector es editar ese YAML, sin migración y sin tocar código.

Deliberadamente NO se reabre `PATCH /v1/credentials/llm/models` (sigue en 409):
el modelo es propiedad de la CONVERSACIÓN (`PUT /v1/conversations/{id}/model`),
no de la credencial del tenant. Mezclarlos reviviría el bug de tres
autoridades del modelo que ya costó caro una vez.
"""

from __future__ import annotations

from typing import Any

from edecan_llm.task_router import (
    ESFUERZO_CHAT_POR_DEFECTO,
    ESFUERZOS_CHAT,
    modelo_chat_por_defecto,
    modelos_chat_disponibles,
)
from fastapi import APIRouter, Depends

from edecan_api.deps import CurrentUser, get_current_user

router = APIRouter(prefix="/v1/models", tags=["models"])

_CAMPOS_PUBLICOS = (
    "id",
    "nombre",
    "descripcion",
    "orden",
    "principal",
    "ve_imagenes",
    "soporta_esfuerzo",
    "contexto_ventana",
)


@router.get("/chat")
async def get_chat_models(
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Catálogo del chat. Auth como el resto; el contenido es el mismo para
    todos los tenants (el catálogo es configuración de la instalación, no
    algo por usuario)."""

    del current_user
    # Solo los campos del contrato: el YAML puede llevar comentarios o claves
    # de más para humanos y esas no deben filtrarse al wire ni volverse
    # contrato accidental de las tres UIs.
    modelos = [
        {campo: row[campo] for campo in _CAMPOS_PUBLICOS if campo in row}
        for row in modelos_chat_disponibles()
    ]
    return {
        "default": modelo_chat_por_defecto(),
        "esfuerzos": list(ESFUERZOS_CHAT),
        "esfuerzo_default": ESFUERZO_CHAT_POR_DEFECTO,
        "modelos": modelos,
    }
