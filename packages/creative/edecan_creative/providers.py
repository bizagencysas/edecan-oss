"""Proveedores de generación de imágenes (`ARCHITECTURE.md` §10.2; `ROADMAP_V2.md`
§7.5, §7.7 — flag de plan `tools.images`).

`ImageProvider` es el protocolo intercambiable (mismo patrón que
`edecan_toolkit.research.SearchProvider` o `edecan_voice.base.TTSProvider`):
`StubImageProvider` genera un PNG determinista sin red — proveedor por
defecto, gratis y offline — y `OpenAICompatImagesProvider` habla con
cualquier endpoint compatible con `POST {base_url}/images/generations`
(OpenAI, o un proxy que replique ese contrato). El tenant configura sus
propias `IMAGES_BASE_URL`/`IMAGES_API_KEY`/`IMAGES_MODEL`: el costo de cada
llamada real corre por su cuenta, la plataforma no las subsidia (igual
filosofía que `OPENAI_COMPAT_BASE_URL` en `edecan_llm`).

`get_image_provider(settings)` resuelve el proveedor activo según
`IMAGES_PROVIDER` (`stub` por defecto), leyendo `settings` de forma
defensiva (`getattr(settings, "CAMPO", default)` — convención dura de
`ROADMAP_V2.md` §7.5: ninguna tool revienta porque falte un campo de
settings) y cayendo a `StubImageProvider` con `logging.warning` si falta
configuración, igual que `edecan_voice.registry.get_stt/get_tts`. Esto es
SIEMPRE la config de PLATAFORMA (`.env`/`Settings` de quien opera el
servidor), nunca por tenant — se conserva por back-compat (self-host de un
solo tenant que configura `IMAGES_*` en su propio `.env`, scripts/tests
propios de este paquete), pero desde la corrección de diseño de
`DIRECCION_ACTUAL.md` ("nunca una llave compartida de plataforma") NINGÚN
flujo de tenant la invoca — ver `get_tenant_image_provider` abajo.

`get_tenant_image_provider(ctx)` es la variante bring-your-own real (mismo
criterio que `docs/credenciales.md` para LLM/voz, `DIRECCION_ACTUAL.md`
"Modelo de credenciales: TODO lo trae el cliente, siempre"): si el tenant
conectó su propia credencial de imágenes (`PUT /v1/credentials/images`,
`apps/api/edecan_api/routers/credentials.py`, `TokenVault` connector_key
`IMAGES_CONNECTOR_KEY`), la usa; si no —o si falla cualquier paso de esa
resolución— cae DIRECTO a `StubImageProvider()`, nunca a
`get_image_provider(ctx.settings)` ni a `IMAGES_API_KEY` de plataforma:
"tenant → stub", el mismo criterio de dos niveles (sin paso intermedio de
plataforma) que ya sigue `apps/api/edecan_api/routers/voice.py`
(`_stt_para_tenant`/`_tts_para_tenant`) para voz web. `GenerarImagenTool`
usa esta variante; `get_image_provider(settings)` en sí queda igual que
antes (back-compat total, pero ya NO es lo que usa este fallback).
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
from io import BytesIO
from typing import Any, Protocol, runtime_checkable

import httpx
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import text as sql_text

logger = logging.getLogger(__name__)

DEFAULT_SIZE = "1024x1024"
DEFAULT_TIMEOUT_SECONDS = 60.0

_MIN_SIZE_PX = 64
_MAX_SIZE_PX = 2048
_MAX_PROMPT_CHARS_EN_IMAGEN = 200
_LINE_HEIGHT_PX = 14

# `connector_key` del `TokenVault` para la credencial de imágenes bring-your-own
# del tenant (ver docstring del módulo). Definido acá y, por separado, en
# `apps/api/edecan_api/routers/credentials.py` (el mismo string literal
# duplicado a propósito: `edecan_creative` no puede importar `edecan_api` —
# dependencia en sentido contrario — igual criterio que `LLM_CONNECTOR_KEY`,
# duplicado entre `apps/api/edecan_api/deps.py` y `apps/worker/edecan_worker/deps.py`).
IMAGES_CONNECTOR_KEY = "images"


@runtime_checkable
class ImageProvider(Protocol):
    """Protocolo común de proveedor de generación de imágenes."""

    async def generate(self, prompt: str, size: str = DEFAULT_SIZE) -> bytes:
        """Genera una imagen a partir de `prompt` y devuelve los bytes PNG crudos.

        `size` sigue el formato `"ANCHOxALTO"` en píxeles (ej. `"1024x1024"`).
        """
        ...


def _parse_size(size: str) -> tuple[int, int]:
    """Convierte `"ANCHOxALTO"` a `(ancho, alto)`, acotado a [64, 2048] px por lado.

    Cualquier valor no parseable (formato inválido, no numérico, vacío, etc.)
    cae al tamaño por defecto `1024x1024` — un tamaño mal formado enviado por
    el modelo nunca debe tumbar la generación de la imagen.
    """
    try:
        ancho_str, alto_str = str(size).lower().split("x", 1)
        ancho, alto = int(ancho_str), int(alto_str)
    except (ValueError, AttributeError):
        ancho, alto = 1024, 1024
    ancho = max(_MIN_SIZE_PX, min(_MAX_SIZE_PX, ancho))
    alto = max(_MIN_SIZE_PX, min(_MAX_SIZE_PX, alto))
    return ancho, alto


class StubImageProvider:
    """Genera un PNG determinista sin red ni dependencias externas.

    El color de fondo se deriva de `sha256(prompt)` (mismo prompt → mismo
    color siempre) y el prompt (truncado) se dibuja como texto envuelto sobre
    la imagen, al tamaño solicitado. Es el proveedor por defecto
    (`IMAGES_PROVIDER=stub`): gratis, 100% offline y determinista — pensado
    para desarrollo, self-host sin presupuesto de imágenes, y tests.
    """

    async def generate(self, prompt: str, size: str = DEFAULT_SIZE) -> bytes:
        ancho, alto = _parse_size(size)
        digest = hashlib.sha256(prompt.encode("utf-8")).digest()
        color = (digest[0], digest[1], digest[2])

        image = Image.new("RGB", (ancho, alto), color=color)
        draw = ImageDraw.Draw(image)
        texto = prompt.strip()[:_MAX_PROMPT_CHARS_EN_IMAGEN] or "(sin prompt)"
        _draw_wrapped_text(draw, texto, ancho, alto)

        buffer = BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()


def _draw_wrapped_text(draw: ImageDraw.ImageDraw, text: str, ancho: int, alto: int) -> None:
    """Dibuja `text` centrado y envuelto a lo ancho de la imagen.

    Usa la fuente bitmap por defecto de Pillow (`ImageFont.load_default()`):
    no requiere empaquetar ni descargar ningún archivo `.ttf` (regla dura:
    nada de red en esta generación offline).
    """
    font = ImageFont.load_default()
    max_chars_por_linea = max(10, ancho // 7)
    palabras = text.split()
    lineas: list[str] = []
    actual = ""
    for palabra in palabras:
        candidata = f"{actual} {palabra}".strip()
        if len(candidata) > max_chars_por_linea and actual:
            lineas.append(actual)
            actual = palabra
        else:
            actual = candidata
    if actual:
        lineas.append(actual)

    alto_total = len(lineas) * _LINE_HEIGHT_PX
    y = max(0, (alto - alto_total) // 2)
    for linea in lineas:
        bbox = draw.textbbox((0, 0), linea, font=font)
        ancho_texto = bbox[2] - bbox[0]
        x = max(0, (ancho - ancho_texto) // 2)
        draw.text((x, y), linea, fill=(255, 255, 255), font=font)
        y += _LINE_HEIGHT_PX


class OpenAICompatImagesProvider:
    """Habla con cualquier endpoint compatible con `POST {base_url}/images/generations`
    (contrato de OpenAI Images: `{model, prompt, size, response_format}`).

    Se activa con `IMAGES_PROVIDER=openai_compat` + `IMAGES_BASE_URL` +
    `IMAGES_API_KEY` + `IMAGES_MODEL` configurados por el tenant/operador
    (`ROADMAP_V2.md` §7.5) — nunca credenciales de la plataforma compartidas.
    """

    name = "openai_compat"

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._model = model
        self._api_key = api_key
        self._client = http_client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"), timeout=timeout
        )

    async def aclose(self) -> None:
        """Cierra el cliente HTTP subyacente (pool de conexiones)."""
        await self._client.aclose()

    async def generate(self, prompt: str, size: str = DEFAULT_SIZE) -> bytes:
        headers = {
            "authorization": f"Bearer {self._api_key}",
            "content-type": "application/json",
        }
        body = {
            "model": self._model,
            "prompt": prompt,
            "size": size,
            "response_format": "b64_json",
        }
        response = await self._client.post("/images/generations", json=body, headers=headers)
        response.raise_for_status()
        data = response.json()
        items = data.get("data") or []
        b64 = items[0].get("b64_json") if items else None
        if not b64:
            raise ValueError(
                "El proveedor de imágenes OpenAI-compatible no devolvió 'data[0].b64_json'."
            )
        return base64.b64decode(b64)


def get_image_provider(settings: Any) -> ImageProvider:
    """Resuelve el `ImageProvider` activo según `IMAGES_PROVIDER` (`stub` por defecto).

    - `"openai_compat"` + `IMAGES_BASE_URL`/`IMAGES_API_KEY`/`IMAGES_MODEL`
      presentes → `OpenAICompatImagesProvider`.
    - Cualquier otro caso (incluido `"openai_compat"` sin configuración
      completa, `"stub"`, o un valor no reconocido) → `StubImageProvider`,
      con `logging.warning` si el operador pidió explícitamente un proveedor
      que no se pudo activar.
    """
    proveedor = str(getattr(settings, "IMAGES_PROVIDER", "stub") or "stub").strip().lower()

    if proveedor == "openai_compat":
        base_url = getattr(settings, "IMAGES_BASE_URL", None)
        api_key = getattr(settings, "IMAGES_API_KEY", None)
        model = getattr(settings, "IMAGES_MODEL", None)
        if base_url and api_key and model:
            return OpenAICompatImagesProvider(base_url=base_url, api_key=api_key, model=model)
        logger.warning(
            "IMAGES_PROVIDER=openai_compat pero falta IMAGES_BASE_URL/IMAGES_API_KEY/"
            "IMAGES_MODEL; usando StubImageProvider."
        )
        return StubImageProvider()

    if proveedor != "stub":
        logger.warning("IMAGES_PROVIDER=%r no reconocido; usando StubImageProvider.", proveedor)

    return StubImageProvider()


def _stub_con_aviso(tenant_id: Any) -> StubImageProvider:
    """`StubImageProvider` + `logger.warning` accionable — se llama desde
    CADA rama de `get_tenant_image_provider` que no encontró una credencial
    de imágenes del tenant utilizable (nunca conectó nada, cuenta a medio
    escribir, JSON corrupto, campos incompletos). JAMÁS consulta
    `IMAGES_API_KEY`/`IMAGES_PROVIDER` de plataforma."""
    logger.warning(
        "tenant_id=%s no tiene una credencial de imágenes propia conectada (o no es "
        "utilizable); usando StubImageProvider. Conecta tu propia credencial en "
        "Configuración -> PUT /v1/credentials/images.",
        tenant_id,
    )
    return StubImageProvider()


async def get_tenant_image_provider(ctx: Any) -> ImageProvider:
    """`ImageProvider` bring-your-own del tenant — "tenant → stub", SIN paso
    intermedio de plataforma (ver docstring del módulo).

    Lee `ctx.tenant_id`/`ctx.session`/`ctx.vault` de forma defensiva (`ctx` es
    `edecan_core.tools.ToolContext` en producción, pero un `Any` a propósito
    — mismo criterio que `edecan_api.deps.load_tenant_llm_config`/
    `edecan_worker.deps.Deps._resolve_tenant_llm_router`, que resuelven la
    misma pregunta para LLM): si falta cualquiera de los tres, o el tenant
    nunca conectó `PUT /v1/credentials/images`, o CUALQUIER paso falla (vault
    caído, JSON corrupto, faltan campos), se devuelve DIRECTO
    `StubImageProvider()` — nunca `get_image_provider(ctx.settings)` ni
    `IMAGES_API_KEY` de plataforma — con `logger.warning` indicando al tenant
    cómo conectar su propia credencial. Nunca revienta `generar_imagen` por
    esto.
    """
    tenant_id = getattr(ctx, "tenant_id", None)
    session = getattr(ctx, "session", None)
    vault = getattr(ctx, "vault", None)
    if tenant_id is None or session is None or vault is None:
        return _stub_con_aviso(tenant_id)

    try:
        row = (
            await session.execute(
                sql_text(
                    "SELECT id FROM connector_accounts WHERE tenant_id = :tenant_id "
                    "AND connector_key = :connector_key ORDER BY created_at DESC LIMIT 1"
                ),
                {"tenant_id": tenant_id, "connector_key": IMAGES_CONNECTOR_KEY},
            )
        ).mappings().first()
        if row is None:
            return _stub_con_aviso(tenant_id)

        bundle = await vault.get(tenant_id=tenant_id, connector_account_id=row["id"])
        if bundle is None:
            return _stub_con_aviso(tenant_id)

        data = json.loads(bundle.access_token)
        base_url = data.get("base_url")
        api_key = data.get("api_key")
        model = data.get("model")
        if not (base_url and api_key and model):
            return _stub_con_aviso(tenant_id)
        return OpenAICompatImagesProvider(base_url=base_url, api_key=api_key, model=model)
    except Exception:
        logger.warning(
            "No se pudo resolver el ImageProvider bring-your-own del tenant_id=%s (fallo "
            "leyendo su credencial); usando StubImageProvider, NUNCA la config de "
            "plataforma. Conecta tu propia credencial en Configuración -> "
            "PUT /v1/credentials/images.",
            tenant_id,
            exc_info=True,
        )
        return StubImageProvider()
