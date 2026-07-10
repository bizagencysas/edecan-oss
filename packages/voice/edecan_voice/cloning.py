"""Cliente HTTP de ElevenLabs para voces del tenant: listar, clonar y generar
efectos de sonido (WP-V5-10; `ROADMAP_V2.md` §6.3 "clonación SOLO con
consentimiento grabado y verificado").

`api_key` SIEMPRE se recibe por parámetro — ninguna función de este módulo
lee `ELEVENLABS_API_KEY` de `Settings`/`.env` (mismo guardrail bring-your-own
que `edecan_voice.elevenlabs.ElevenLabsTTS`/`edecan_voice.tenant`; ver
`DIRECCION_ACTUAL.md` "Modelo de credenciales").

## El agente JAMÁS clona una voz

Este módulo es un cliente HTTP puro — no decide POR SÍ SOLO cuándo clonar.
Su único invocador de `crear_clon`/`borrar_clon` en todo el repo es
`apps/api/edecan_api/routers/voz_avanzada.py` (endpoint de UI con un humano
presente que sube la grabación de consentimiento y marca la declaración
explícita — ver el docstring de ese router). **Ninguna** `Tool` de
`edecan_voice.tools` (ni de ningún otro paquete) llama a `crear_clon`: el
catálogo de herramientas del agente (`edecan_voice.tools.get_all_tools`) solo
expone `listar_voces`/`sintetizar_voz`, nunca una acción de clonación.

## Nunca se filtra la `api_key` en un error

`VoiceCloningError` SIEMPRE se arma con el `status_code`/cuerpo de la
RESPUESTA de ElevenLabs — nunca con los headers de la petición saliente (que
es donde vive `api_key`, vía `xi-api-key`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

import httpx

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.elevenlabs.io/v1"
_DEFAULT_TIMEOUT_SECONDS = 30.0
# Subir muestras de audio (multipart, hasta 25 archivos según los límites de
# ElevenLabs) puede tardar más que un GET/DELETE simple.
_UPLOAD_TIMEOUT_SECONDS = 120.0
_SOUND_GENERATION_TIMEOUT_SECONDS = 60.0

_ERROR_SNIPPET_CHARS = 300


class VoiceCloningError(Exception):
    """Error al hablar con la API de voces/efectos de ElevenLabs.

    El mensaje SIEMPRE viene del `status_code`/cuerpo de la respuesta del
    proveedor (ver docstring del módulo) — nunca incluye `api_key`.
    """


@dataclass(frozen=True)
class VozDisponible:
    """Una voz del catálogo de ElevenLabs del tenant (de stock + clones propios)."""

    voice_id: str
    nombre: str
    categoria: Literal["premade", "cloned", "generated", "professional"]
    preview_url: str | None = None


@dataclass(frozen=True)
class MuestraVoz:
    """Un archivo de audio de muestra para `crear_clon`."""

    data: bytes
    filename: str
    content_type: str = "audio/mpeg"


def _error_desde_respuesta(accion: str, response: httpx.Response) -> VoiceCloningError:
    snippet = response.text[:_ERROR_SNIPPET_CHARS]
    return VoiceCloningError(
        f"ElevenLabs rechazó {accion} (status {response.status_code}): {snippet}"
    )


async def listar_voces(
    api_key: str, *, timeout: float = _DEFAULT_TIMEOUT_SECONDS
) -> list[VozDisponible]:
    """`GET /v1/voices` — catálogo completo del tenant (voces de stock + los
    clones que ya existan en esa cuenta de ElevenLabs)."""
    headers = {"xi-api-key": api_key}
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            response = await client.get(f"{_BASE_URL}/voices", headers=headers)
        except httpx.HTTPError as exc:
            raise VoiceCloningError(f"No pudimos conectar con ElevenLabs: {exc}") from exc

    if response.status_code != 200:
        raise _error_desde_respuesta("listar las voces", response)
    try:
        payload = response.json()
    except ValueError as exc:
        raise VoiceCloningError(
            "ElevenLabs devolvió una respuesta no-JSON al listar voces."
        ) from exc

    voces: list[VozDisponible] = []
    for item in payload.get("voices") or []:
        voice_id = item.get("voice_id")
        nombre = item.get("name")
        if not voice_id or not nombre:
            continue
        voces.append(
            VozDisponible(
                voice_id=voice_id,
                nombre=nombre,
                categoria=item.get("category") or "premade",
                preview_url=item.get("preview_url"),
            )
        )
    return voces


async def crear_clon(
    api_key: str,
    nombre: str,
    muestras: list[MuestraVoz],
    descripcion: str | None = None,
    *,
    timeout: float = _UPLOAD_TIMEOUT_SECONDS,
) -> str:
    """`POST /v1/voices/add` (multipart) — crea un clon a partir de `muestras`
    y devuelve el `voice_id` nuevo.

    NUNCA se llama sin que ya exista consentimiento verificado — ver el
    docstring del módulo ("El agente JAMÁS clona una voz"): el único
    invocador real es `apps/api/edecan_api/routers/voz_avanzada.py`, después
    de validar la declaración de consentimiento (`attestation`) y de subir la
    grabación del consentimiento como evidencia.
    """
    if not muestras:
        raise VoiceCloningError("crear_clon requiere al menos una muestra de audio.")

    headers = {"xi-api-key": api_key}
    form_data: dict[str, str] = {"name": nombre}
    if descripcion:
        form_data["description"] = descripcion
    files = [
        ("files", (muestra.filename, muestra.data, muestra.content_type)) for muestra in muestras
    ]

    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            response = await client.post(
                f"{_BASE_URL}/voices/add", headers=headers, data=form_data, files=files
            )
        except httpx.HTTPError as exc:
            raise VoiceCloningError(f"No pudimos conectar con ElevenLabs: {exc}") from exc

    if response.status_code not in (200, 201):
        raise _error_desde_respuesta("crear el clon de voz", response)
    try:
        payload = response.json()
    except ValueError as exc:
        raise VoiceCloningError(
            "ElevenLabs devolvió una respuesta no-JSON al crear el clon."
        ) from exc

    voice_id = payload.get("voice_id")
    if not voice_id:
        raise VoiceCloningError("ElevenLabs no devolvió 'voice_id' al crear el clon.")
    return voice_id


async def borrar_clon(
    api_key: str, voice_id: str, *, timeout: float = _DEFAULT_TIMEOUT_SECONDS
) -> None:
    """`DELETE /v1/voices/{voice_id}` — borra el clon en ElevenLabs.

    `apps/api/edecan_api/routers/voz_avanzada.py` la llama en modo
    "best-effort" (captura `VoiceCloningError` y sigue): el registro local de
    consentimiento (`voice_consents`) NUNCA se borra aunque esto falle — es
    evidencia legal, no una simple referencia técnica (ver el docstring de
    ese router).
    """
    headers = {"xi-api-key": api_key}
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            response = await client.delete(f"{_BASE_URL}/voices/{voice_id}", headers=headers)
        except httpx.HTTPError as exc:
            raise VoiceCloningError(f"No pudimos conectar con ElevenLabs: {exc}") from exc

    if response.status_code not in (200, 204):
        raise _error_desde_respuesta("borrar el clon de voz", response)


async def generar_efecto(
    api_key: str, text: str, *, timeout: float = _SOUND_GENERATION_TIMEOUT_SECONDS
) -> bytes:
    """`POST /v1/sound-generation` — genera un efecto de sonido (NO habla, no
    usa ninguna voz concreta) a partir de una descripción en `text`, y
    devuelve los bytes mp3 crudos.

    Pensado para que lo consuma un paquete de trabajo posterior de efectos /
    ambientación sonora vía import directo de este módulo (`from
    edecan_voice import generar_efecto` — exportado en
    `edecan_voice.__init__`); ninguna `Tool` de este paquete lo usa todavía.
    """
    headers = {"xi-api-key": api_key}
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            response = await client.post(
                f"{_BASE_URL}/sound-generation", headers=headers, json={"text": text}
            )
        except httpx.HTTPError as exc:
            raise VoiceCloningError(f"No pudimos conectar con ElevenLabs: {exc}") from exc

    if response.status_code != 200:
        raise _error_desde_respuesta("generar el efecto de sonido", response)
    return response.content
