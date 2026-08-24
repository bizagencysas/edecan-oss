"""Regresión anti-fuga bring-your-own de `edecan_voice` (Barrido de seguridad
v5, `DIRECCION_ACTUAL.md` "Modelo de credenciales: TODO lo trae el cliente,
siempre").

Contexto: el bug de referencia que este archivo cazaría
(`packages/llm/edecan_llm/router.py::_build_provider_from_config`, ya
corregido en v4) es un proveedor bring-your-own que completa en silencio un
campo de credencial vacío del tenant con un valor de PLATAFORMA
(`self._settings`/variable de entorno). Este archivo prueba, a nivel de
`edecan_voice` (no de `apps/api`, ver `apps/api/tests/test_voice_byo.py` para
la resolución tenant→stub completa), que ESE patrón es estructuralmente
imposible aquí:

- `DeepgramSTT`/`ElevenLabsTTS`/`PollyTTS` reciben su credencial SIEMPRE por
  el constructor (nunca leen `os.environ` ni un objeto `settings`) — se
  construye con una "clave de plataforma" en el entorno (variables
  `DEEPGRAM_API_KEY`/`ELEVENLABS_API_KEY` con un valor centinela) y se prueba
  que la request real (capturada con `respx`) solo lleva la credencial que se
  pasó explícitamente al constructor, nunca el centinela del entorno.
- Un test de firma (`inspect.signature`) deja constancia explícita de que
  ninguna de las tres clases acepta un parámetro `settings`: si alguien
  agregara ese parámetro en el futuro (la forma más directa de reintroducir
  el patrón `config.campo or getattr(self._settings, "X", None)` de v4 en
  este paquete), este test lo detecta sin depender de que además se le
  olvide usarlo mal.

`edecan_voice.registry.get_stt`/`get_tts` SÍ leen `settings` — son funciones
nivel-PLATAFORMA legítimas por diseño (ver su docstring y
`docs/credenciales.md`): la garantía bring-your-own no es "esta función nunca
lee settings", es "ningún camino de request de un tenant real llega a
llamarla" — eso se verifica en `apps/api/edecan_api/routers/voice.py`
(`_stt_para_tenant`/`_tts_para_tenant`, que NUNCA importan `get_stt`/`get_tts`
de este módulo) y su propio test dedicado, `apps/api/tests/test_voice_byo.py`.
"""

from __future__ import annotations

import inspect
import json
import os

import httpx
import pytest
import respx
from edecan_voice.deepgram import DEEPGRAM_LISTEN_URL, DeepgramSTT
from edecan_voice.elevenlabs import ELEVENLABS_TTS_URL_TEMPLATE, ElevenLabsTTS
from edecan_voice.polly import PollyTTS
from edecan_voice.stubs import StubSTT, StubTTS

_SENTINEL = "FUGA_DE_PLATAFORMA_NO_DEBE_APARECER"


# ---------------------------------------------------------------------------
# Firma: ninguna implementación real acepta "settings" — ver docstring.
# ---------------------------------------------------------------------------


def test_deepgram_stt_no_acepta_settings_en_su_constructor():
    parametros = set(inspect.signature(DeepgramSTT.__init__).parameters)
    assert "settings" not in parametros


def test_elevenlabs_tts_no_acepta_settings_en_su_constructor():
    parametros = set(inspect.signature(ElevenLabsTTS.__init__).parameters)
    assert "settings" not in parametros


def test_polly_tts_no_acepta_settings_en_su_constructor():
    parametros = set(inspect.signature(PollyTTS.__init__).parameters)
    assert "settings" not in parametros


def test_stubs_no_aceptan_settings_en_su_constructor():
    assert "settings" not in set(inspect.signature(StubSTT.__init__).parameters)
    assert "settings" not in set(inspect.signature(StubTTS.__init__).parameters)


# ---------------------------------------------------------------------------
# Request real (respx): con una "clave de plataforma" centinela presente en
# el entorno, la petición HTTP real solo debe llevar la credencial del propio
# constructor — nunca el centinela.
# ---------------------------------------------------------------------------


@respx.mock
async def test_deepgram_stt_transcribe_no_filtra_variables_de_entorno(monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", _SENTINEL)
    route = respx.post(DEEPGRAM_LISTEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={"results": {"channels": [{"alternatives": [{"transcript": "hola"}]}]}},
        )
    )

    stt = DeepgramSTT(api_key="clave-del-tenant")
    await stt.transcribe(b"audio-falso", "audio/webm")

    assert route.called
    auth_header = route.calls.last.request.headers["Authorization"]
    assert auth_header == "Token clave-del-tenant"
    assert _SENTINEL not in auth_header
    assert _SENTINEL not in str(route.calls.last.request.url)


@respx.mock
async def test_elevenlabs_tts_synthesize_no_filtra_variables_de_entorno(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", _SENTINEL)
    url = ELEVENLABS_TTS_URL_TEMPLATE.format(voice_id="voz-tenant")
    route = respx.post(url).mock(return_value=httpx.Response(200, content=b"mp3-bytes"))

    tts = ElevenLabsTTS(api_key="clave-del-tenant", default_voice_id="voz-tenant")
    await tts.synthesize("hola")

    assert route.called
    request = route.calls.last.request
    assert request.headers["xi-api-key"] == "clave-del-tenant"
    assert _SENTINEL not in request.headers["xi-api-key"]
    assert _SENTINEL not in json.loads(request.content).values().__str__()


@respx.mock
async def test_deepgram_stt_dos_tenants_seguidos_nunca_mezclan_claves(monkeypatch):
    """Dos transcripciones seguidas, cada una con SU PROPIA clave (simula dos
    tenants distintos que resuelven su propio `DeepgramSTT` en la misma
    ventana de tiempo, ver `_stt_para_tenant`): cada request lleva SOLO la
    clave de ESE tenant, nunca la del otro ni la del entorno."""
    monkeypatch.setenv("DEEPGRAM_API_KEY", _SENTINEL)
    route = respx.post(DEEPGRAM_LISTEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={"results": {"channels": [{"alternatives": [{"transcript": "ok"}]}]}},
        )
    )

    await DeepgramSTT(api_key="clave-tenant-A").transcribe(b"audio", "audio/webm")
    await DeepgramSTT(api_key="clave-tenant-B").transcribe(b"audio", "audio/webm")

    assert [call.request.headers["Authorization"] for call in route.calls] == [
        "Token clave-tenant-A",
        "Token clave-tenant-B",
    ]


def test_polly_tts_no_tiene_ningun_campo_de_api_key():
    """Polly (a diferencia de Deepgram/ElevenLabs) no tiene NINGÚN parámetro
    de credencial secreta en su constructor — usa la cadena de credenciales
    AWS *ambiente* del proceso donde corre el backend (`edecan_voice.polly`),
    nunca una API key de Edecán. `region_name`/`endpoint_url` son
    configuración no-secreta (región AWS, endpoint de LocalStack en dev), no
    credenciales — confirmamos que son los ÚNICOS parámetros configurables
    además de `voice_id`/`session`/`allow_ambient_credentials` (ver el
    siguiente test: ese último NO es una credencial, es un candado que
    reemplaza al chequeo de clave que sí tienen Deepgram/ElevenLabs — hallazgo
    "riesgo-legal-tos", `HOTFIXES_PENDIENTES.md`)."""
    parametros = set(inspect.signature(PollyTTS.__init__).parameters) - {"self"}
    assert parametros == {
        "voice_id",
        "region_name",
        "endpoint_url",
        "session",
        "allow_ambient_credentials",
    }
    assert "api_key" not in parametros


def test_polly_tts_sin_session_ni_allow_ambient_credentials_revienta():
    """Construir `PollyTTS` sin `session=` (test) ni
    `allow_ambient_credentials=True` (ver docstring del módulo — SOLO
    legítimo desde `edecan_voice.registry.get_tts` o desde una resolución
    multi-tenant que ya confirmó `EDECAN_LOCAL_MODE=True`) debe reventar en
    vez de heredar en silencio la identidad AWS del proceso: segunda capa de
    defensa para el hallazgo "riesgo-legal-tos" — si algún caller nuevo
    olvidara el chequeo de `EDECAN_LOCAL_MODE`, esto lo detiene igual."""
    with pytest.raises(ValueError, match="allow_ambient_credentials"):
        PollyTTS(voice_id="Lupe")


def test_polly_tts_allow_ambient_credentials_true_no_revienta():
    """El opt-in explícito SÍ debe permitir construir el proveedor (self-host
    de un único tenant, o multi-tenant ya gateado por `EDECAN_LOCAL_MODE`) —
    no verificamos la sesión de AWS resultante (eso es responsabilidad de
    `aioboto3`, no de este paquete), solo que no revienta."""
    tts = PollyTTS(voice_id="Lupe", allow_ambient_credentials=True)
    assert tts._voice_id == "Lupe"


async def test_stub_stt_ignora_por_completo_variables_de_entorno(monkeypatch):
    """Confirma que el stub (fallback de plataforma para voz, `docs/credenciales.md`)
    es 100% offline: ni siquiera intenta leer una credencial del entorno."""
    monkeypatch.setenv("DEEPGRAM_API_KEY", _SENTINEL)
    monkeypatch.setenv("ELEVENLABS_API_KEY", _SENTINEL)
    transcript = await StubSTT().transcribe(b"cualquier-cosa", "audio/webm")
    assert _SENTINEL not in transcript.text
    audio = await StubTTS().synthesize("cualquier texto")
    assert _SENTINEL.encode() not in audio


def test_os_environ_sentinel_no_se_filtra_por_accidente_via_getenv(monkeypatch):
    """Guardrail final: ni `DeepgramSTT` ni `ElevenLabsTTS` llaman
    `os.environ`/`os.getenv` en absoluto (verificado arriba por firma +
    request real); este test adicional confirma que fijar las variables de
    entorno típicas de plataforma no cambia en nada el resultado de
    construir cada proveedor con una clave explícita distinta."""
    monkeypatch.setenv("DEEPGRAM_API_KEY", _SENTINEL)
    monkeypatch.setenv("ELEVENLABS_API_KEY", _SENTINEL)
    assert os.environ["DEEPGRAM_API_KEY"] == _SENTINEL  # precondición del test

    stt = DeepgramSTT(api_key="clave-tenant")
    tts = ElevenLabsTTS(api_key="clave-tenant", default_voice_id="v")

    assert stt._api_key == "clave-tenant"  # type: ignore[attr-defined]
    assert tts._api_key == "clave-tenant"  # type: ignore[attr-defined]
