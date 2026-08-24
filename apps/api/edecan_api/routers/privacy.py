"""Exportación portable de datos propios (`/v1/privacy`).

El export solo reúne datos del usuario autenticado dentro de su tenant. No
incluye credenciales de conectores, embeddings, claves internas ni argumentos
de tools; el objetivo es portabilidad útil, no volcar secretos operativos.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from edecan_core.safety import redact
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field

from edecan_api.deps import (
    DELETED_USER_KEY_TTL_SECONDS,
    CurrentUser,
    get_auth_redis,
    get_current_user,
    get_platform_repo,
    get_repo,
    rate_limit,
)
from edecan_api.repo import Repo
from edecan_api.security import verify_password, verify_totp_code

router = APIRouter(prefix="/v1/privacy", tags=["privacy"], dependencies=[Depends(rate_limit)])


class AccountDeletionIn(BaseModel):
    password: str = Field(min_length=1, max_length=256)
    confirmation: str = Field(min_length=1, max_length=64)
    totp_code: str | None = Field(default=None, max_length=32)


@router.get("")
async def privacy_center(
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Describe controles disponibles sin fingir que la cuenta completa ya se borra."""
    return {
        "version": "edecan-privacy-center.v1",
        "tenant_id": current_user.tenant_id,
        "user_id": current_user.user_id,
        "controls": {
            "export": {"available": True, "method": "GET", "path": "/v1/privacy/export"},
            "erase_memory": {
                "available": True,
                "method": "DELETE",
                "path": "/v1/memory",
            },
            "erase_account": {
                "available": True,
                "method": "DELETE",
                "path": "/v1/privacy/account",
                "requires_reauthentication": True,
                "status": (
                    "requiere reautenticación y puede bloquearse si quedan dependencias externas"
                ),
            },
        },
    }


@router.delete("/account")
async def delete_account(
    body: AccountDeletionIn,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    platform_repo: Repo = Depends(get_platform_repo),
    redis_client: Any = Depends(get_auth_redis),
) -> dict[str, Any]:
    """Borra la identidad solo después de reautenticación y preflight seguro.

    Archivos S3, conectores y suscripciones bloquean la operación porque esta
    ruta no puede limpiar esos sistemas externos dentro de la transacción DB.
    """

    del request
    if body.confirmation.strip().upper() != "ELIMINAR MI CUENTA":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Escribe exactamente «ELIMINAR MI CUENTA» para confirmar.",
        )
    user = await platform_repo.get_user(current_user.user_id)
    if user is None or not verify_password(str(user.get("password_hash") or ""), body.password):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No se pudo verificar la reautenticación.",
        )
    totp_secret = user.get("totp_secret")
    if totp_secret and (
        not body.totp_code or not verify_totp_code(str(totp_secret), body.totp_code)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="La cuenta requiere un código de verificación válido.",
        )

    denylist_key = f"auth:deleted-user:{current_user.user_id}"
    # Se escribe ANTES de borrar la fila. Así un fallo posterior de la DB no
    # deja una identidad parcialmente eliminada con access tokens utilizables.
    await redis_client.set(denylist_key, "1", ex=DELETED_USER_KEY_TTL_SECONDS)
    try:
        result = await platform_repo.delete_user_account(current_user.user_id)
    except Exception:
        # Si la transacción no llegó a borrar la identidad, no debemos dejar al
        # usuario bloqueado por un intento fallido. Si este cleanup falla,
        # conservar el denylist es la opción segura y el error se propaga.
        await redis_client.delete(denylist_key)
        raise
    blockers = list(result.get("blocked") or [])
    if blockers:
        await redis_client.delete(denylist_key)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "account_deletion_blocked",
                "blockers": blockers,
                "message": "Primero limpia las dependencias externas indicadas.",
            },
        )
    if not result.get("deleted"):
        await redis_client.delete(denylist_key)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuario no encontrado.")

    # El denylist invalida access tokens vigentes de todos los procesos durante
    # al menos el TTL máximo del JWT. El cliente actual también debe cerrar su
    # sesión.
    return {
        "deleted": True,
        "user_id": str(current_user.user_id),
        "message": "La identidad y sus datos personales fueron eliminados.",
    }


@router.get("/account/preflight")
async def account_deletion_preflight(
    current_user: CurrentUser = Depends(get_current_user),
    platform_repo: Repo = Depends(get_platform_repo),
) -> dict[str, Any]:
    """Muestra blockers externos sin reautenticar ni ejecutar borrado."""
    result = await platform_repo.account_deletion_preflight(current_user.user_id)
    explanations = {
        "files_s3": "Archivos almacenados externamente; elimínalos o expórtalos antes.",
        "connector_credentials": "Conectores activos; desconéctalos desde Conectores.",
        "billing_subscription": "Suscripción activa; cancélala desde Facturación.",
        "tenant_ownership": "Transfiere la propiedad del tenant antes de salir.",
    }
    blockers = list(result.get("blocked") or [])
    return {
        "format": "edecan-account-deletion-preflight.v1",
        "ready": bool(result.get("ready")),
        "blockers": [
            {"code": code, "message": explanations.get(code, "Dependencia externa pendiente.")}
            for code in blockers
        ],
        "mutated": False,
    }


def _safe_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {str(key): _safe_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_safe_value(item) for item in value]
    return value


def _public_row(row: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    mapping = dict(row)
    return {field: _safe_value(mapping.get(field)) for field in fields if field in mapping}


@router.get("/export")
async def export_my_data(
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Devuelve un export JSON de los datos de aplicación del usuario."""
    tenant_id = current_user.tenant_id
    user_id = current_user.user_id
    conversations = await repo.list_conversations(tenant_id=tenant_id, user_id=user_id)
    conversation_out: list[dict[str, Any]] = []
    for conversation in conversations:
        public_conversation = _public_row(
            conversation,
            ("id", "title", "channel", "created_at", "updated_at"),
        )
        conversation_id = uuid.UUID(str(conversation["id"]))
        messages = await repo.list_messages(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            limit=100_000,
        )
        public_conversation["messages"] = [
            _public_row(message, ("id", "role", "content", "created_at")) for message in messages
        ]
        conversation_out.append(public_conversation)

    memories = await repo.list_memory(tenant_id=tenant_id, user_id=user_id, q=None, k=100_000)
    persona = await repo.get_persona(tenant_id=tenant_id, user_id=user_id)
    reminders = await repo.list_reminders(tenant_id=tenant_id, user_id=user_id)
    contacts = await repo.list_contacts(tenant_id=tenant_id, user_id=user_id, q=None)
    transactions = await repo.list_transactions(tenant_id=tenant_id, user_id=user_id, mes=None)
    files = await repo.list_files(tenant_id=tenant_id)
    owned_files = [row for row in files if str(row.get("user_id")) == str(user_id)]
    missions = await repo.list_user_missions(tenant_id=tenant_id, user_id=user_id)
    automations = await repo.list_user_automations(tenant_id=tenant_id, user_id=user_id)
    devices = await repo.list_user_devices(tenant_id=tenant_id, user_id=user_id)
    connector_accounts = await repo.list_connector_accounts(tenant_id=tenant_id)
    feedback = await repo.list_user_quality_feedback(tenant_id=tenant_id, user_id=user_id)

    return jsonable_encoder(
        {
            "format": "edecan-user-export.v1",
            "generated_at": datetime.now(UTC),
            "scope": {"tenant_id": tenant_id, "user_id": user_id},
            "persona": _public_row(
                persona,
                ("nombre_asistente", "idioma", "formalidad", "instrucciones", "memoria_activada"),
            )
            if persona is not None
            else None,
            "conversations": conversation_out,
            "memory": [
                _public_row(
                    row,
                    (
                        "id",
                        "kind",
                        "content",
                        "importance",
                        "confidence",
                        "source",
                        "expires_at",
                        "created_at",
                    ),
                )
                for row in memories
            ],
            "reminders": [
                _public_row(
                    row, ("id", "due_at", "rrule", "message", "channel", "status", "created_at")
                )
                for row in reminders
            ],
            "contacts": [
                _public_row(
                    row,
                    ("id", "nombre", "emails", "phones", "empresa", "notas", "tags", "created_at"),
                )
                for row in contacts
            ],
            "transactions": [
                _public_row(
                    row,
                    (
                        "id",
                        "fecha",
                        "monto",
                        "moneda",
                        "categoria",
                        "descripcion",
                        "cuenta",
                        "created_at",
                    ),
                )
                for row in transactions
            ],
            "files": [
                _public_row(row, ("id", "filename", "mime", "size_bytes", "status", "created_at"))
                for row in owned_files
            ],
            "missions": [
                _public_row(
                    row,
                    (
                        "id",
                        "objetivo",
                        "status",
                        "plan",
                        "resultado",
                        "presupuesto",
                        "error",
                        "created_at",
                        "updated_at",
                    ),
                )
                for row in missions
            ],
            "automations": [
                _public_row(
                    row,
                    (
                        "id",
                        "nombre",
                        "descripcion",
                        "enabled",
                        "timezone",
                        "next_run_at",
                        "last_run_at",
                        "created_at",
                    ),
                )
                for row in automations
            ],
            "devices": [
                _public_row(
                    row,
                    (
                        "id",
                        "nombre",
                        "plataforma",
                        "kind",
                        "status",
                        "last_seen_at",
                        "push_platform",
                        "paired_at",
                        "created_at",
                    ),
                )
                for row in devices
            ],
            "connections": [
                _public_row(row, ("connector_key", "display_name", "status", "scopes"))
                for row in connector_accounts
            ],
            "feedback": [
                _public_row(row, ("id", "kind", "category", "feedback_ref", "created_at"))
                for row in feedback
            ],
            "excluded_operational_secrets": [
                "connector_credentials",
                "embeddings",
                "tool_arguments",
                "s3_keys",
            ],
        }
    )
