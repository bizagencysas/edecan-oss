"""Las 2 herramientas de `edecan_voice` (nombres EXACTOS, pinned en el
paquete de trabajo WP-V5-10): `listar_voces` y `sintetizar_voz`. Ambas usan
las voces del **tenant** (bring-your-own, `edecan_voice.tenant`), ninguna es
`dangerous`, y ninguna clona nada — ver `edecan_voice.cloning` ("El agente
JAMÁS clona una voz"): la clonación vive EXCLUSIVAMENTE detrás de
`POST /v1/voz/clones` (`apps/api/edecan_api/routers/voz_avanzada.py`), un
endpoint de UI con un humano presente, nunca una tool.

Ambas gatean el flag de plan `voice.web` (`_FLAG_VOICE_WEB`, string local en
vez de `edecan_schemas.plans.FLAG_VOICE_WEB` importado — mismo patrón que ya
usa este mismo repo aunque la dependencia esté disponible en otros paquetes:
`edecan_creative.tools.GenerarImagenTool.requires_flags = frozenset({"tools.images"})`,
`edecan_browser.tools._FLAG_BROWSER`. Evita que `edecan_voice` declare
`edecan-schemas` como dependencia solo por una constante de string,
`ARCHITECTURE.md` §10.1): mismo flag que ya gatea `/v1/voice/transcribe`/
`/v1/voice/speak` (`apps/api/edecan_api/routers/voice.py`) — exponer
"sintetizar voz"/"listar voces" al agente es la misma capacidad de voz web,
solo que invocada por el modelo en vez de por el usuario desde el navegador.

## Subida de archivos generados

`sintetizar_voz` sube el audio resultante a S3 + una fila `files`, MISMO
patrón que `edecan_creative.edecan_creative._files.subir_archivo` (ver su
docstring) — no se importa directo: es un helper privado (prefijo `_`) de un
paquete hermano, así que se replica LOCALMENTE aquí (`_subir_archivo`), mismo
criterio de duplicación deliberada que ya usa el resto del repo para
utilidades privadas pequeñas entre paquetes/routers hermanos (p. ej.
`_find_account`/`_audit` duplicados entre `routers/erp.py`/`routers/
vehiculos.py`/`routers/credentials.py`). Constructor-inyectable (`Uploader`)
para que los tests puedan sustituirlo sin tocar S3 ni Postgres.

## Cuota mensual de voz (`limits.voice_minutes_month`)

`sintetizar_voz` es la MISMA capacidad que `POST /v1/voice/speak`
(`apps/api/edecan_api/routers/voice.py::speak`), así que debe respetar y
consumir la MISMA cuota mensual de esa capacidad — no solo su flag booleano
`voice.web` (`docs/voz-telefonia.md` "Cuotas": "se mide igual sin importar
qué nivel de la resolución respondió"). `_bajo_cuota_de_voz`/
`_registrar_uso_de_voz` replican LOCALMENTE (mismo criterio de duplicación
deliberada de arriba, y mismo motivo que `_FLAG_VOICE_WEB` — evitar que este
paquete dependa de `edecan-schemas` solo por dos constantes) el criterio
EXACTO de `voice.py::_check_voice_quota`/`speak`: estima segundos del texto
a ~150 palabras/minuto, compara contra `limits.voice_minutes_month` del
tenant (`-1` = ilimitado) sumando `usage_events` de este mes-calendario UTC,
y — si hay cupo — registra `usage_events(kind="voice_seconds")` tras
sintetizar, para que el consumo por chat cuente contra el mismo cupo que ya
hace cumplir `POST /v1/voice/speak` (y sea visible para reconciliación/
facturación igual que ese camino).
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

import aioboto3
from edecan_core import Tool, ToolContext, ToolResult
from sqlalchemy import text as sql_text

from edecan_voice import cloning
from edecan_voice.base import TTSProvider
from edecan_voice.stubs import StubTTS
from edecan_voice.telephony import (
    TelephonyError,
    normalize_e164,
    normalize_goal,
)
from edecan_voice.tenant import resolver_config_tts_del_tenant, resolver_tts_del_tenant

logger = logging.getLogger(__name__)

# Ver docstring del módulo: misma capacidad que `/v1/voice/{transcribe,speak}`.
_FLAG_VOICE_WEB = "voice.web"
_FLAG_VOICE_TELEPHONY = "voice.telephony"

# Cuota de plan de esa MISMA capacidad — ver docstring del módulo ("Cuota
# mensual de voz"). String/sentinel locales en vez de
# `edecan_schemas.plans.LIMIT_VOICE_MINUTES_MONTH`/`UNLIMITED` importados,
# mismo criterio que `_FLAG_VOICE_WEB` arriba.
_LIMIT_VOICE_MINUTES_MONTH = "limits.voice_minutes_month"
_UNLIMITED = -1

_MAX_TEXTO_CHARS = 3000
_MAX_PREVIEW_CHARS = 80

_DEFAULT_S3_BUCKET = "edecan-files"
_DEFAULT_AWS_REGION = "us-east-1"

# Dos voces de ejemplo, deterministas y offline — se devuelven cuando el
# tenant no conectó ElevenLabs (o su TTS es Polly/no está configurado): mismo
# espíritu que `StubTTS`/`StubSTT`, nunca se llama a un proveedor real sin
# credencial propia del tenant.
VOCES_STUB: tuple[cloning.VozDisponible, ...] = (
    cloning.VozDisponible(
        voice_id="stub-voz-neutral", nombre="Voz neutral (offline)", categoria="premade"
    ),
    cloning.VozDisponible(
        voice_id="stub-voz-calida", nombre="Voz cálida (offline)", categoria="premade"
    ),
)


def _cap_str(value: Any, max_chars: int) -> str:
    return str(value or "").strip()[:max_chars]


def _voz_a_dict(voz: cloning.VozDisponible) -> dict[str, Any]:
    return {
        "voice_id": voz.voice_id,
        "nombre": voz.nombre,
        "categoria": voz.categoria,
        "preview_url": voz.preview_url,
    }


# ---------------------------------------------------------------------------
# Subida del audio generado — ver docstring del módulo.
# ---------------------------------------------------------------------------


@runtime_checkable
class Uploader(Protocol):
    """Firma que acepta `SintetizarVozTool` para guardar el audio generado."""

    async def __call__(
        self, ctx: Any, *, data: bytes, filename: str, mime: str
    ) -> tuple[uuid.UUID, str]:
        """Sube `data` (bytes crudos) y devuelve `(file_id, filename)`."""
        ...


async def _subir_archivo(
    ctx: Any, *, data: bytes, filename: str, mime: str
) -> tuple[uuid.UUID, str]:
    """Sube `data` a `s3://$S3_BUCKET/tenants/{tenant_id}/files/{file_id}/{filename}`
    e inserta la fila correspondiente en `files` con `status="ready"` — ver
    docstring del módulo (mismo patrón que `edecan_creative._files.subir_archivo`,
    replicado localmente).
    """
    file_id = uuid.uuid4()
    s3_key = f"tenants/{ctx.tenant_id}/files/{file_id}/{filename}"
    bucket = getattr(ctx.settings, "S3_BUCKET", None) or _DEFAULT_S3_BUCKET
    region = getattr(ctx.settings, "AWS_REGION", None) or _DEFAULT_AWS_REGION
    endpoint_url = getattr(ctx.settings, "AWS_ENDPOINT_URL", None)

    session = aioboto3.Session()
    async with session.client("s3", region_name=region, endpoint_url=endpoint_url) as s3:
        await s3.put_object(Bucket=bucket, Key=s3_key, Body=data, ContentType=mime)

    await ctx.session.execute(
        sql_text(
            "INSERT INTO files "
            "(id, tenant_id, user_id, s3_key, filename, mime, size_bytes, status, "
            "created_at, updated_at) "
            "VALUES (:id, :tenant_id, :user_id, :s3_key, :filename, :mime, :size_bytes, "
            "'ready', :now, :now)"
        ),
        {
            "id": file_id,
            "tenant_id": str(ctx.tenant_id),
            "user_id": str(ctx.user_id),
            "s3_key": s3_key,
            "filename": filename,
            "mime": mime,
            "size_bytes": len(data),
            "now": datetime.now(UTC),
        },
    )
    return file_id, filename


# ---------------------------------------------------------------------------
# listar_voces
# ---------------------------------------------------------------------------


class ListarVocesTool(Tool):
    name = "listar_voces"
    description = (
        "Lista las voces disponibles para síntesis de voz (TTS): las de tu cuenta de "
        "ElevenLabs si la conectaste (incluidos tus propios clones autorizados, si tienes "
        "alguno), o dos voces de ejemplo offline si no configuraste un proveedor real. NO "
        "clona ninguna voz — eso solo se hace desde la página de Voz, con tu confirmación "
        "explícita."
    )
    requires_flags = frozenset({_FLAG_VOICE_WEB})
    input_schema = {"type": "object", "properties": {}}

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        config = await resolver_config_tts_del_tenant(ctx)
        voces: list[cloning.VozDisponible]

        if config is not None and config.get("provider") == "elevenlabs" and config.get("api_key"):
            try:
                voces = await cloning.listar_voces(config["api_key"])
            except cloning.VoiceCloningError as exc:
                logger.warning("No se pudieron listar las voces de ElevenLabs: %s", exc)
                return ToolResult(content=f"No pude obtener tus voces de ElevenLabs: {exc}")
        else:
            voces = list(VOCES_STUB)

        if not voces:
            return ToolResult(content="No encontré ninguna voz disponible.", data={"voces": []})

        lineas = [f"- {v.nombre} (`{v.voice_id}`, {v.categoria})" for v in voces]
        return ToolResult(
            content="Voces disponibles:\n" + "\n".join(lineas),
            data={"voces": [_voz_a_dict(v) for v in voces]},
        )


# ---------------------------------------------------------------------------
# sintetizar_voz
# ---------------------------------------------------------------------------


def _tenant_flags(ctx: Any) -> dict[str, Any]:
    """Lee `ctx.extras["flags"]` (los flags/límites de plan del tenant que
    `Agent.run_turn`/`_build_ctx` dejan ahí, ARCHITECTURE.md §10.7) — mismo
    helper que `edecan_automations.tools._tenant_flags`/
    `edecan_toolkit.contenido._tenant_flags` (duplicado a propósito: este
    paquete no depende de ninguno de los otros dos). Nunca revienta si
    `ctx.extras` no trae la clave (turno sin flags = trátalo como `{}`)."""
    extras = ctx.extras if isinstance(ctx.extras, dict) else {}
    flags = extras.get("flags")
    return flags if isinstance(flags, dict) else {}


def _estimate_seconds_from_text(texto: str) -> float:
    """Aproximación de duración hablada a ~150 palabras por minuto — MISMA
    fórmula que `apps/api/edecan_api/routers/voice.py::_estimate_seconds_from_text`
    (duplicada a propósito, ver docstring del módulo "Cuota mensual de voz"):
    la cuota debe medir lo mismo sin importar qué camino (HTTP o chat) generó
    el audio."""
    palabras = max(len(texto.split()), 1)
    return round((palabras / 150.0) * 60.0, 2)


async def _segundos_de_voz_usados_este_mes(ctx: Any) -> float:
    """Suma `usage_events.quantity` de `kind='voice_seconds'` del tenant
    desde el inicio del mes-calendario UTC — misma consulta que
    `apps/api/edecan_api/repo.py::SqlRepo.sum_usage_since` (duplicada a
    propósito: esta `Tool` no tiene acceso a `Repo`, que vive en `apps/api`)."""
    since = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    resultado = await ctx.session.execute(
        sql_text(
            "SELECT COALESCE(SUM(quantity), 0) AS total FROM usage_events "
            "WHERE tenant_id = :tenant_id AND kind = 'voice_seconds' AND created_at >= :since"
        ),
        {"tenant_id": str(ctx.tenant_id), "since": since},
    )
    fila = resultado.mappings().first()
    return float(fila["total"]) if fila else 0.0


async def _bajo_cuota_de_voz(ctx: Any, *, segundos_extra: float) -> tuple[bool, int]:
    """`(True, límite)` si el tenant todavía tiene cupo para `segundos_extra`
    más de síntesis este mes; `límite` es `limits.voice_minutes_month` (para
    el mensaje al usuario si no alcanza). Mismo criterio EXACTO que
    `apps/api/edecan_api/routers/voice.py::_check_voice_quota` (duplicado a
    propósito, ver docstring del módulo): `-1` = ilimitado, mes-calendario
    UTC, nunca lanza (fail-open a "sin límite configurado" si la clave no
    está en `flags`, igual que el router)."""
    limite_minutos = _tenant_flags(ctx).get(_LIMIT_VOICE_MINUTES_MONTH, _UNLIMITED)
    if limite_minutos == _UNLIMITED:
        return True, limite_minutos
    usados = await _segundos_de_voz_usados_este_mes(ctx)
    return (usados + segundos_extra) <= limite_minutos * 60, limite_minutos


async def _registrar_uso_de_voz(ctx: Any, *, segundos: float) -> None:
    """Inserta la fila de `usage_events(kind='voice_seconds')` que
    `_bajo_cuota_de_voz`/`GET /v1/usage` necesitan para contabilizar este
    consumo — mismo patrón de INSERT directo por `ctx.session` que
    `edecan_messaging.tools._registrar_envio` (duplicado a propósito, ver
    docstring del módulo "Cuota mensual de voz"). Se llama SOLO tras un
    `synthesize`/subida exitosos, igual que `voice.py::speak` registra el
    uso después de sintetizar, nunca antes."""
    await ctx.session.execute(
        sql_text(
            """
            INSERT INTO usage_events (tenant_id, kind, quantity, meta)
            VALUES (:tenant_id ::uuid, 'voice_seconds', :quantity, CAST(:meta AS jsonb))
            """
        ),
        {
            "tenant_id": str(ctx.tenant_id),
            "quantity": segundos,
            "meta": json.dumps({"origen": "sintetizar_voz"}),
        },
    )


class SintetizarVozTool(Tool):
    name = "sintetizar_voz"
    description = (
        "Convierte un texto en un archivo de audio hablado (TTS) usando tu proveedor de voz "
        "configurado (o un audio de silencio offline si no configuraste ninguno), y lo "
        "guarda como archivo del usuario. Máximo 3000 caracteres por llamada."
    )
    requires_flags = frozenset({_FLAG_VOICE_WEB})
    input_schema = {
        "type": "object",
        "properties": {
            "texto": {
                "type": "string",
                "description": "Texto a convertir en audio (máximo 3000 caracteres).",
            },
            "voice_id": {
                "type": "string",
                "description": (
                    "ID de una voz concreta a usar (ver 'listar_voces'). Si se omite, usa "
                    "la voz por defecto configurada."
                ),
            },
        },
        "required": ["texto"],
    }

    def __init__(
        self, *, tts_provider: TTSProvider | None = None, uploader: Uploader | None = None
    ) -> None:
        # Patrón inyectable (mismo criterio que `GenerarImagenTool` de
        # `edecan_creative.tools`): por defecto resuelve el TTS bring-your-own
        # real del tenant; los tests pueden sustituirlo sin tocar
        # `ctx.vault`/`ctx.session`/S3.
        self._tts_provider = tts_provider
        self._uploader = uploader or _subir_archivo

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        texto = _cap_str(args.get("texto"), _MAX_TEXTO_CHARS)
        if not texto:
            return ToolResult(content="Necesito el texto que quieres convertir en voz.")
        voice_id = _cap_str(args.get("voice_id"), 200) or None

        # Cuota de plan — ver docstring del módulo ("Cuota mensual de voz").
        # Se revisa ANTES de resolver/llamar al proveedor (igual que
        # `voice.py::speak`): si ya no hay cupo, ni siquiera se gasta una
        # llamada real al TTS bring-your-own del tenant (que puede ser un
        # proveedor de pago).
        estimated_seconds = _estimate_seconds_from_text(texto)
        bajo_cuota, limite_minutos = await _bajo_cuota_de_voz(ctx, segundos_extra=estimated_seconds)
        if not bajo_cuota:
            return ToolResult(
                content=(
                    f"Alcanzaste tu límite de {limite_minutos} minutos de voz de este mes. "
                    "Espera al próximo ciclo o mejora tu plan para seguir generando audio."
                )
            )

        provider = self._tts_provider or await resolver_tts_del_tenant(ctx)
        audio_bytes = await provider.synthesize(texto, voice_id=voice_id)

        # `StubTTS` (proveedor por defecto sin configuración real, ver
        # `edecan_voice.tenant.resolver_tts_del_tenant`) siempre produce WAV;
        # el resto de proveedores producen mp3 — mismo criterio que
        # `apps/api/edecan_api/routers/voice.py::speak`.
        es_stub = isinstance(provider, StubTTS)
        fmt = "wav" if es_stub else "mp3"
        mime = "audio/wav" if es_stub else "audio/mpeg"
        filename = f"voz-{uuid.uuid4().hex[:8]}.{fmt}"

        file_id, filename = await self._uploader(
            ctx, data=audio_bytes, filename=filename, mime=mime
        )

        # Registra el consumo SOLO tras sintetizar+subir con éxito (igual que
        # `voice.py::speak`) — así el mismo cupo que acaba de chequearse
        # arriba queda contabilizado para la próxima llamada, y el consumo
        # generado por chat es visible para reconciliación/facturación igual
        # que el de `/v1/voice/speak`.
        await _registrar_uso_de_voz(ctx, segundos=estimated_seconds)

        preview = texto[:_MAX_PREVIEW_CHARS]
        if len(texto) > _MAX_PREVIEW_CHARS:
            preview += "…"
        return ToolResult(
            content=(
                f"Convertí «{preview}» a voz y lo guardé como «{filename}» — puedes "
                "escucharlo desde tus archivos."
            ),
            data={
                "file_id": str(file_id),
                "filename": filename,
                "mime": mime,
                "caption": preview,
            },
        )


class ListarAgentesLlamadasTool(Tool):
    """Permite que el chat conozca las identidades disponibles antes de llamar."""

    name = "listar_agentes_llamadas"
    description = (
        "Lista los agentes telefónicos configurados por esta persona, sus nombres exactos, "
        "objetivos y cuál atiende por defecto. Úsala antes de llamar cuando el nombre pedido "
        "sea dudoso. Nunca inventes un agente ni sustituyas uno por otro."
    )
    requires_flags = frozenset({_FLAG_VOICE_TELEPHONY})
    input_schema = {"type": "object", "properties": {}}

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        result = await ctx.session.execute(
            sql_text(
                """
                SELECT id, name, agent_name, default_goal, is_default
                FROM phone_agent_templates
                WHERE tenant_id = :tenant_id AND user_id = :user_id
                ORDER BY is_default DESC, created_at ASC, id ASC
                """
            ),
            {"tenant_id": str(ctx.tenant_id), "user_id": str(ctx.user_id)},
        )
        rows = [dict(row) for row in result.mappings().all()]
        if not rows:
            return ToolResult(
                content=(
                    "Todavía no hay agentes de llamada configurados. Puedo ayudarte a crear "
                    "uno si me dices para qué sirve, cómo se presenta y qué debe conseguir."
                ),
                data={"agentes": []},
            )
        lines = [
            (
                f"- {row['name']} (se presenta como {row['agent_name']})"
                f"{' · predeterminado y entrantes' if row['is_default'] else ''}: "
                f"{row['default_goal']}"
            )
            for row in rows
        ]
        return ToolResult(
            content="Agentes de llamada disponibles:\n" + "\n".join(lines),
            data={
                "agentes": [
                    {
                        "id": str(row["id"]),
                        "nombre": row["name"],
                        "identidad": row["agent_name"],
                        "objetivo": row["default_goal"],
                        "predeterminado": bool(row["is_default"]),
                    }
                    for row in rows
                ]
            },
        )


class ConfigurarAgenteLlamadasTool(Tool):
    """Crea o actualiza una identidad telefónica a partir de lenguaje natural."""

    name = "configurar_agente_llamadas"
    description = (
        "Crea o actualiza un agente telefónico reutilizable. Antes de invocarla, reúne con "
        "preguntas breves lo necesario: nombre para seleccionarlo, identidad al presentarse, "
        "función, objetivo, forma de conversar, contexto autorizado frente a terceros y datos "
        "que debe obtener. No inventes precios, políticas ni información del negocio. Si falta "
        "contexto esencial, pregúntalo. Un mismo nombre actualiza el agente existente."
    )
    requires_flags = frozenset({_FLAG_VOICE_TELEPHONY})
    input_schema = {
        "type": "object",
        "properties": {
            "nombre": {
                "type": "string",
                "description": "Nombre exacto para pedirlo luego, por ejemplo Agente de negocios.",
            },
            "identidad": {
                "type": "string",
                "description": "Nombre con el que se presentará, por ejemplo Valentina.",
            },
            "personalidad": {
                "type": "string",
                "description": "Cómo escucha, pregunta, explica, negocia y cierra.",
            },
            "objetivo": {
                "type": "string",
                "description": "Resultado que normalmente debe conseguir.",
            },
            "apertura": {
                "type": "string",
                "description": (
                    "Primera frase después de identificarse como asistente automatizado."
                ),
            },
            "contexto_autorizado": {
                "type": "string",
                "description": (
                    "Datos del negocio que sí puede usar frente a terceros. Vacío si todavía "
                    "no hay ninguno; nunca copies memorias privadas por tu cuenta."
                ),
            },
            "informacion_a_obtener": {
                "type": "string",
                "description": "Datos y respuestas que debe preguntar durante la llamada.",
            },
            "voice_id": {
                "type": "string",
                "description": (
                    "ID exacto de una voz de ElevenLabs obtenida con listar_voces. Se omite "
                    "para usar la voz predeterminada."
                ),
            },
            "predeterminado": {
                "type": "boolean",
                "description": "Si atiende entrantes y se usa cuando no se indica otro.",
            },
        },
        "required": ["nombre", "identidad", "personalidad", "objetivo"],
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        name = _cap_str(args.get("nombre"), 80)
        agent_name = _cap_str(args.get("identidad"), 80)
        persona_prompt = _cap_str(args.get("personalidad"), 4000)
        default_goal = _cap_str(args.get("objetivo"), 500)
        opening_message = _cap_str(args.get("apertura"), 700)
        knowledge_context = _cap_str(args.get("contexto_autorizado"), 6000)
        required_information = _cap_str(args.get("informacion_a_obtener"), 3000)
        voice_id = _cap_str(args.get("voice_id"), 200)
        if not all((name, agent_name, persona_prompt, default_goal)):
            return ToolResult(
                content=(
                    "Antes de guardar necesito el nombre del agente, cómo se presenta, su "
                    "forma de conversar y el objetivo que debe conseguir."
                )
            )

        existing_result = await ctx.session.execute(
            sql_text(
                """
                SELECT * FROM phone_agent_templates
                WHERE tenant_id = :tenant_id AND user_id = :user_id
                  AND LOWER(name) = LOWER(:name)
                LIMIT 1
                """
            ),
            {
                "tenant_id": str(ctx.tenant_id),
                "user_id": str(ctx.user_id),
                "name": name,
            },
        )
        existing = existing_result.mappings().first()
        make_default = bool(args.get("predeterminado"))
        if existing is None:
            count_result = await ctx.session.execute(
                sql_text(
                    """
                    SELECT COUNT(*) AS total FROM phone_agent_templates
                    WHERE tenant_id = :tenant_id AND user_id = :user_id
                    """
                ),
                {"tenant_id": str(ctx.tenant_id), "user_id": str(ctx.user_id)},
            )
            count_row = count_result.mappings().first()
            total = int((count_row or {}).get("total", 0))
            if total >= 20:
                return ToolResult(
                    content="Ya tienes 20 agentes de llamada. Elimina uno antes de crear otro."
                )
            make_default = make_default or total == 0

        if make_default:
            await ctx.session.execute(
                sql_text(
                    """
                    UPDATE phone_agent_templates
                    SET is_default = false, updated_at = :now
                    WHERE tenant_id = :tenant_id AND user_id = :user_id AND is_default
                    """
                ),
                {
                    "tenant_id": str(ctx.tenant_id),
                    "user_id": str(ctx.user_id),
                    "now": datetime.now(UTC),
                },
            )

        values = {
            "tenant_id": str(ctx.tenant_id),
            "user_id": str(ctx.user_id),
            "name": name,
            "agent_name": agent_name,
            "persona_prompt": persona_prompt,
            "default_goal": default_goal,
            "opening_message": opening_message,
            "knowledge_context": knowledge_context,
            "required_information": required_information,
            "voice_id": voice_id or None,
            "is_default": make_default,
            "now": datetime.now(UTC),
        }
        if existing is None:
            values["id"] = str(uuid.uuid4())
            saved_result = await ctx.session.execute(
                sql_text(
                    """
                    INSERT INTO phone_agent_templates (
                        id, tenant_id, user_id, name, agent_name, persona_prompt,
                        default_goal, opening_message, knowledge_context,
                        required_information, voice_id, is_default, created_at, updated_at
                    ) VALUES (
                        CAST(:id AS uuid), CAST(:tenant_id AS uuid), CAST(:user_id AS uuid),
                        :name, :agent_name, :persona_prompt, :default_goal, :opening_message,
                        :knowledge_context, :required_information, :voice_id, :is_default,
                        :now, :now
                    )
                    RETURNING id, name, agent_name, default_goal, is_default
                    """
                ),
                values,
            )
            action = "creado"
        else:
            values["id"] = str(existing["id"])
            # Si no pidió volverlo predeterminado, conserva el estado actual.
            values["is_default"] = make_default or bool(existing.get("is_default"))
            saved_result = await ctx.session.execute(
                sql_text(
                    """
                    UPDATE phone_agent_templates
                    SET name = :name, agent_name = :agent_name,
                        persona_prompt = :persona_prompt, default_goal = :default_goal,
                        opening_message = :opening_message,
                        knowledge_context = :knowledge_context,
                        required_information = :required_information,
                        voice_id = :voice_id,
                        is_default = :is_default, updated_at = :now
                    WHERE tenant_id = CAST(:tenant_id AS uuid)
                      AND user_id = CAST(:user_id AS uuid)
                      AND id = CAST(:id AS uuid)
                    RETURNING id, name, agent_name, default_goal, is_default
                    """
                ),
                values,
            )
            action = "actualizado"
        saved = saved_result.mappings().first()
        if saved is None:
            return ToolResult(content="No pude guardar el agente de llamada.")
        return ToolResult(
            content=(
                f"Listo. El agente «{saved['name']}» quedó {action} y se presenta como "
                f"{saved['agent_name']}. Para usarlo, di: «llama a +… con el agente "
                f"{saved['name']} y…»."
            ),
            data={
                "id": str(saved["id"]),
                "nombre": saved["name"],
                "identidad": saved["agent_name"],
                "objetivo": saved["default_goal"],
                "predeterminado": bool(saved["is_default"]),
            },
        )


class LlamarContactoTool(Tool):
    """Llama solo después del gate `dangerous` del turno y del consentimiento vigente."""

    name = "llamar_contacto"
    description = (
        "Prepara y realiza una llamada telefónica real desde el número Twilio conectado por "
        "el usuario. Si la persona menciona un agente guardado, pasa su nombre exacto en "
        "`agente`; Edecan debe usar esa identidad y nunca sustituirla por otra. Requiere "
        "mostrar y confirmar explícitamente el número internacional, el agente elegido y el "
        "objetivo exacto antes de ejecutarse; también exige consentimiento de voz vigente del "
        "destinatario. La llamada y su transcripción continúan en la misma conversación."
    )
    requires_flags = frozenset({_FLAG_VOICE_TELEPHONY})
    dangerous = True
    input_schema = {
        "type": "object",
        "properties": {
            "telefono_e164": {
                "type": "string",
                "description": "Destino internacional exacto, por ejemplo +573001234567.",
            },
            "objetivo": {
                "type": "string",
                "description": "Qué debe conseguir Edecan durante la llamada.",
            },
            "agente": {
                "type": "string",
                "description": (
                    "Nombre de la plantilla o identidad de llamada solicitada por la persona, "
                    "por ejemplo «Agente de negocios». Se omite solo si no pidió una concreta."
                ),
            },
        },
        "required": ["telefono_e164", "objetivo"],
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        try:
            destination = normalize_e164(args.get("telefono_e164"))
            goal = normalize_goal(args.get("objetivo"))
        except ValueError as exc:
            return ToolResult(content=str(exc))

        extras = ctx.extras if isinstance(ctx.extras, dict) else {}
        dispatcher = extras.get("phone_call_dispatcher")
        if not callable(dispatcher):
            return ToolResult(
                content=(
                    "La telefonía todavía no está conectada a este proceso de Edecan. "
                    "Reinicia la app después de configurar Twilio."
                )
            )
        try:
            dispatch_args: dict[str, Any] = {"to_e164": destination, "goal": goal}
            agent_reference = " ".join(str(args.get("agente") or "").split()).strip()
            if agent_reference:
                dispatch_args["agent_ref"] = agent_reference
            data = await dispatcher(**dispatch_args)
        except TelephonyError as exc:
            return ToolResult(content=f"No pude iniciar la llamada: {exc}")
        agent_label = str(data.get("agent_name") or data.get("agent_template_name") or "").strip()
        agent_sentence = f" usando a {agent_label}" if agent_label else ""
        return ToolResult(
            content=(
                f"La llamada a {destination} quedó iniciada{agent_sentence} con el objetivo "
                f"confirmado: {goal}. "
                "Puedes seguir su estado y transcripción en Actividad."
            ),
            data={
                key: str(value) if isinstance(value, uuid.UUID) else value
                for key, value in data.items()
            },
        )


def get_all_tools() -> list[Tool]:
    """Instancia las herramientas de voz y llamada. Consumido por
    `edecan_voice.__init__.get_all_tools` — entry point
    `[project.entry-points."edecan.tools"]` en `pyproject.toml`."""
    return [
        ListarVocesTool(),
        SintetizarVozTool(),
        ListarAgentesLlamadasTool(),
        ConfigurarAgenteLlamadasTool(),
        LlamarContactoTool(),
    ]
