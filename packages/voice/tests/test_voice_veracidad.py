"""Contrato de veracidad (`edecan_core.veracidad`) aplicado a `edecan_voice`.

Este es el paquete que motivó el arreglo: `StubTTS` devolvía 0.5s de
silencio absoluto con HTTP 200 y `SintetizarVozTool` le decía al modelo
"puedes escucharlo desde tus archivos" — la clase SABÍA que era un stub
(`es_stub = isinstance(provider, StubTTS)`, calculado un par de líneas más
abajo del `synthesize`) y ese booleano se usaba solo para elegir la
extensión del archivo, nunca para avisar.

Tres capas de prueba:

1. Arquitectura: TODO `STTProvider`/`TTSProvider` de este paquete tiene que
   heredar también `ProveedorDeclarado` — un proveedor nuevo que no lo haga
   rompe ESTE test, no se cuela.
2. Unitaria: cada proveedor concreto declara lo que se espera de él (stubs
   SIMULADO con motivo, proveedores reales REAL).
3. Integración: `SintetizarVozTool` con `StubTTS` produce un resultado que
   NO dice "puedes escucharlo" y sí lleva `ToolResult.fidelidad`.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

from edecan_core.veracidad import Fidelidad, ProveedorDeclarado
from edecan_voice.base import STTProvider, TTSProvider

# Importados por su nombre (no solo por side-effect) para que quede explícito
# QUÉ clases exige cubrir este archivo — si alguien agrega un proveedor
# nuevo en un módulo que este test no importa, `_todas_las_subclases` de
# abajo no lo va a ver, así que el checklist de abajo (`test_...`) es la
# defensa real, no solo el barrido automático.
from edecan_voice.deepgram import DeepgramSTT
from edecan_voice.elevenlabs import ElevenLabsTTS
from edecan_voice.polly import PollyTTS
from edecan_voice.stubs import StubSTT, StubTTS

CLASES_STT_TTS_CONOCIDAS = (StubSTT, StubTTS, DeepgramSTT, ElevenLabsTTS, PollyTTS)


def _todas_las_subclases(cls: type) -> set[type]:
    directas = set(cls.__subclasses__())
    return directas | {nieta for hija in directas for nieta in _todas_las_subclases(hija)}


# ---------------------------------------------------------------------------
# 1. Arquitectura: barrido automático + checklist explícito.
# ---------------------------------------------------------------------------


def test_ningun_stt_o_tts_de_edecan_voice_escapa_al_contrato_de_veracidad():
    """El barrido: cualquier subclase de `STTProvider`/`TTSProvider` que
    Python conozca en el momento de correr este test (es decir, cualquier
    módulo que algún test/import haya cargado) tiene que ser también
    `ProveedorDeclarado`. Es el test que hoy NO existe en el repo y que hace
    que agregar un proveedor mudo sea posible sin que nada lo note."""
    candidatos = _todas_las_subclases(STTProvider) | _todas_las_subclases(TTSProvider)
    sin_declarar = [c for c in candidatos if not issubclass(c, ProveedorDeclarado)]
    assert sin_declarar == [], (
        f"Estas clases implementan STTProvider/TTSProvider pero NO heredan "
        f"ProveedorDeclarado (edecan_core.veracidad): {[c.__qualname__ for c in sin_declarar]}. "
        "Sin declarar fidelidad, una tool no puede saber si avisar al modelo/dueño."
    )


def test_checklist_explicito_de_las_5_clases_conocidas_del_paquete():
    """Ancla las 5 clases concretas que existen HOY en `edecan_voice`
    (`packages/voice/edecan_voice/{stubs,deepgram,elevenlabs,polly}.py`) —
    si alguien borra la herencia de una sola, este test la señala por
    nombre en vez de un `sin_declarar` genérico."""
    for cls in CLASES_STT_TTS_CONOCIDAS:
        assert issubclass(cls, ProveedorDeclarado), (
            f"{cls.__qualname__} dejó de heredar ProveedorDeclarado"
        )


# ---------------------------------------------------------------------------
# 2. Unitaria: cada clase declara lo esperado.
# ---------------------------------------------------------------------------


def test_stubs_se_declaran_simulados_con_motivo_accionable():
    for cls in (StubSTT, StubTTS):
        info = cls().info_fidelidad()
        assert info.fidelidad is Fidelidad.SIMULADO
        assert info.motivo_simulado  # no vacío, no None
        assert "falta" in info.motivo_simulado.lower()


def test_proveedores_reales_se_declaran_reales():
    deepgram = DeepgramSTT(api_key="fake")
    elevenlabs = ElevenLabsTTS(api_key="fake", default_voice_id="voz-1")
    polly = PollyTTS(session=SimpleNamespace())  # session inyectada, ver edecan_voice.polly
    for provider in (deepgram, elevenlabs, polly):
        info = provider.info_fidelidad()
        assert info.fidelidad is Fidelidad.REAL
        assert info.motivo_simulado is None
        assert info.aviso_para_el_modelo() == ""


def test_stub_tts_aviso_dice_explicitamente_que_es_silencio():
    """El texto exacto que le habría evitado al dueño la confusión con el
    WAV de 0.5s: no basta "es un stub", tiene que decir QUÉ es (silencio)."""
    aviso = StubTTS().info_fidelidad().aviso_para_el_modelo()
    assert "silencio" in aviso.lower()
    assert "ELEVENLABS_API_KEY" in aviso


# ---------------------------------------------------------------------------
# 3. Integración: `SintetizarVozTool` de punta a punta con `StubTTS`.
# ---------------------------------------------------------------------------


class _FakeSession:
    """`ctx.session` mínimo: solo necesita aceptar el INSERT de
    `_registrar_uso_de_voz` (ver `edecan_voice.tools`) sin tocar Postgres."""

    async def execute(self, *args: Any, **kwargs: Any) -> None:
        return None


def _make_ctx() -> SimpleNamespace:
    return SimpleNamespace(
        tenant_id=uuid4(), user_id=uuid4(), session=_FakeSession(), settings=None, llm=None,
        vault=None, extras={},
    )


class _FakeUploader:
    def __init__(self) -> None:
        self.file_id = uuid4()

    async def __call__(
        self, ctx: Any, *, data: bytes, filename: str, mime: str
    ) -> tuple[UUID, str]:
        return self.file_id, filename


async def test_sintetizar_voz_con_stub_no_dice_que_ya_se_puede_escuchar():
    from edecan_voice.tools import SintetizarVozTool

    tool = SintetizarVozTool(tts_provider=StubTTS(), uploader=_FakeUploader())
    resultado = await tool.run(_make_ctx(), {"texto": "Hola, buenas tardes señor"})

    # ANTES del arreglo, el content literal era "... puedes escucharlo desde
    # tus archivos" sin ninguna condición — la frase exacta que el dueño
    # reportó como engañosa.
    assert "puedes escucharlo" not in resultado.content
    assert "NO es tu voz" in resultado.content
    assert "silencio" in resultado.content.lower()

    assert resultado.fidelidad is not None
    assert resultado.fidelidad.fidelidad is Fidelidad.SIMULADO
    assert resultado.data["source_mode"] == "demo"


async def test_sintetizar_voz_con_proveedor_real_si_dice_que_se_puede_escuchar():
    from edecan_voice.tools import SintetizarVozTool

    real = ElevenLabsTTS(api_key="fake-key", default_voice_id="voz-1")

    async def _synthesize_falso(*args: Any, **kwargs: Any) -> bytes:
        return b"AUDIO-MP3-DE-VERDAD"

    real.synthesize = _synthesize_falso  # type: ignore[method-assign]
    tool = SintetizarVozTool(tts_provider=real, uploader=_FakeUploader())

    resultado = await tool.run(_make_ctx(), {"texto": "Hola"})

    assert "puedes escucharlo" in resultado.content
    assert resultado.fidelidad.fidelidad is Fidelidad.REAL
    assert resultado.data["source_mode"] == "live"


# ---------------------------------------------------------------------------
# Fail-closed: un `TTSProvider` inyectado que NO declara nada se trata como
# simulado, nunca como real sin haberlo comprobado (ver
# `edecan_voice.tools._info_fidelidad_tts`).
# ---------------------------------------------------------------------------


async def test_proveedor_inyectado_sin_declarar_se_trata_fail_closed_como_simulado():
    from edecan_voice.tools import SintetizarVozTool

    class ProveedorMudo:
        """A propósito NO hereda `ProveedorDeclarado` — simula el proveedor
        número 6 que alguien agrega sin saber de este contrato."""

        async def synthesize(
            self, text: str, voice_id: str | None = None, fmt: str = "mp3"
        ) -> bytes:
            return b"bytes-cualquiera"

    tool = SintetizarVozTool(tts_provider=ProveedorMudo(), uploader=_FakeUploader())
    resultado = await tool.run(_make_ctx(), {"texto": "Hola"})

    assert resultado.fidelidad.fidelidad is Fidelidad.SIMULADO
    assert "puedes escucharlo" not in resultado.content
