"""Tests de `edecan_creative.podcast`: validación de guion, TTS bring-your-own
del tenant (fail-closed), stubs de audio y ensamblado final (WAV puro-Python /
mp3 con ffmpeg mockeado). `ARCHITECTURE.md` §14, WP-V5-11.

Nada de esto toca red/S3/Postgres real: `respx` mockea ElevenLabs, y las
llamadas a `ffmpeg` se mockean con `monkeypatch` sobre
`asyncio.create_subprocess_exec` (ver la sección "ensamblar_podcast (mp3)"
más abajo) — el paquete de trabajo pide explícitamente no requerir un
`ffmpeg` real instalado para que la suite pase offline.
"""

from __future__ import annotations

import asyncio
import inspect
import io
import json
import wave
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import httpx
import pytest
import respx
from edecan_creative import podcast

_SENTINEL = "FUGA_DE_PLATAFORMA_NO_DEBE_APARECER"


# ---------------------------------------------------------------------------
# validar_guion
# ---------------------------------------------------------------------------


def test_validar_guion_lista_vacia_da_error():
    with pytest.raises(podcast.GuionInvalidoError, match="segmentos"):
        podcast.validar_guion([])


def test_validar_guion_no_es_lista_da_error():
    with pytest.raises(podcast.GuionInvalidoError):
        podcast.validar_guion("esto no es una lista")


def test_validar_guion_mas_de_30_segmentos_da_error():
    segmentos = [{"texto": "hola"} for _ in range(31)]
    with pytest.raises(podcast.GuionInvalidoError, match="30"):
        podcast.validar_guion(segmentos)


def test_validar_guion_segmento_no_es_dict_da_error():
    with pytest.raises(podcast.GuionInvalidoError):
        podcast.validar_guion(["no es un objeto"])


def test_validar_guion_segmento_sin_texto_da_error():
    with pytest.raises(podcast.GuionInvalidoError, match="texto"):
        podcast.validar_guion([{"orador": "Ana"}])


def test_validar_guion_texto_muy_largo_da_error():
    with pytest.raises(podcast.GuionInvalidoError, match="2000"):
        podcast.validar_guion([{"texto": "x" * 2001}])


def test_validar_guion_texto_en_el_limite_exacto_no_falla():
    segmentos = podcast.validar_guion([{"texto": "x" * 2000}])
    assert len(segmentos[0].texto) == 2000


def test_validar_guion_total_supera_30000_da_error():
    # 16 segmentos * 2000 chars = 32000 > 30000, y 16 <= 30 (no dispara el
    # límite de cantidad de segmentos primero).
    segmentos = [{"texto": "x" * 2000} for _ in range(16)]
    with pytest.raises(podcast.GuionInvalidoError, match="30000"):
        podcast.validar_guion(segmentos)


def test_validar_guion_camino_feliz_normaliza_orador_y_voice_id():
    segmentos = podcast.validar_guion(
        [
            {"orador": "Ana", "texto": "Hola a todos", "voice_id": "voz-1"},
            {"texto": "Sin orador ni voice_id declarados"},
        ]
    )
    assert segmentos == [
        podcast.SegmentoPodcast(orador="Ana", texto="Hola a todos", voice_id="voz-1"),
        podcast.SegmentoPodcast(
            orador="Orador 2", texto="Sin orador ni voice_id declarados", voice_id=None
        ),
    ]


def test_validar_guion_devuelve_lista_de_segmentopodcast():
    segmentos = podcast.validar_guion([{"texto": "hola"}])
    assert isinstance(segmentos[0], podcast.SegmentoPodcast)


# ---------------------------------------------------------------------------
# resolver_config_tts_tenant — tenant -> stub, sin paso de plataforma
# ---------------------------------------------------------------------------


async def test_resolver_config_tts_tenant_sin_contexto_devuelve_none():
    resultado = await podcast.resolver_config_tts_tenant(session=None, vault=None, tenant_id=None)
    assert resultado is None


async def test_resolver_config_tts_tenant_sin_cuenta_conectada_devuelve_none(
    make_session, make_vault
):
    session = make_session([[]])
    vault = make_vault()
    resultado = await podcast.resolver_config_tts_tenant(
        session=session, vault=vault, tenant_id=uuid4()
    )
    assert resultado is None


async def test_resolver_config_tts_tenant_bundle_ausente_devuelve_none(make_session, make_vault):
    session = make_session([[{"id": "11111111-1111-1111-1111-111111111111"}]])
    vault = make_vault(bundle=None)
    resultado = await podcast.resolver_config_tts_tenant(
        session=session, vault=vault, tenant_id=uuid4()
    )
    assert resultado is None


async def test_resolver_config_tts_tenant_json_corrupto_devuelve_none(make_session, make_vault):
    session = make_session([[{"id": "11111111-1111-1111-1111-111111111111"}]])
    vault = make_vault(bundle=SimpleNamespace(access_token="esto-no-es-json{"))
    resultado = await podcast.resolver_config_tts_tenant(
        session=session, vault=vault, tenant_id=uuid4()
    )
    assert resultado is None


async def test_resolver_config_tts_tenant_sesion_que_revienta_devuelve_none():
    class _SesionQueRevienta:
        async def execute(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("la base de datos no respondió")

    resultado = await podcast.resolver_config_tts_tenant(
        session=_SesionQueRevienta(), vault=object(), tenant_id=uuid4()
    )
    assert resultado is None


async def test_resolver_config_tts_tenant_camino_feliz(make_session, make_vault):
    session = make_session([[{"id": "11111111-1111-1111-1111-111111111111"}]])
    bundle = SimpleNamespace(
        access_token=json.dumps(
            {"provider": "elevenlabs", "api_key": "clave-del-tenant", "voice_id": "voz-x"}
        )
    )
    vault = make_vault(bundle=bundle)
    resultado = await podcast.resolver_config_tts_tenant(
        session=session, vault=vault, tenant_id=uuid4()
    )
    assert resultado == {
        "provider": "elevenlabs",
        "api_key": "clave-del-tenant",
        "voice_id": "voz-x",
    }


def test_voice_tts_connector_key_es_el_mismo_que_usa_el_router_de_credenciales():
    """Regresión de nombres: si este string se desincroniza del literal
    duplicado en `apps/api/edecan_api/deps.py::VOICE_TTS_CONNECTOR_KEY` (y de
    `edecan_voice.tenant.VOICE_TTS_CONNECTOR_KEY`), `PUT
    /v1/credentials/voice/tts` y `resolver_config_tts_tenant` dejarían de
    hablarse."""
    assert podcast.VOICE_TTS_CONNECTOR_KEY == "voice_tts"


# ---------------------------------------------------------------------------
# sintetizar_segmento — tenant -> stub
# ---------------------------------------------------------------------------


async def test_sintetizar_segmento_sin_cfg_devuelve_wav_stub():
    resultado = await podcast.sintetizar_segmento(None, texto="hola mundo")
    assert resultado.es_stub is True
    assert resultado.formato == "wav"
    with wave.open(io.BytesIO(resultado.data), "rb") as clip:
        assert clip.getnchannels() == 1
        assert clip.getnframes() > 0


async def test_sintetizar_segmento_cfg_de_otro_proveedor_devuelve_stub():
    resultado = await podcast.sintetizar_segmento({"provider": "polly"}, texto="hola")
    assert resultado.es_stub is True


async def test_sintetizar_segmento_cfg_elevenlabs_sin_api_key_devuelve_stub():
    resultado = await podcast.sintetizar_segmento({"provider": "elevenlabs"}, texto="hola")
    assert resultado.es_stub is True


@respx.mock
async def test_sintetizar_segmento_elevenlabs_real_devuelve_mp3():
    ruta = respx.post("https://api.elevenlabs.io/v1/text-to-speech/voz-1").mock(
        return_value=httpx.Response(200, content=b"mp3-fake-bytes")
    )
    cfg = {"provider": "elevenlabs", "api_key": "clave-del-tenant"}

    resultado = await podcast.sintetizar_segmento(cfg, texto="hola", voice_id="voz-1")

    assert ruta.called
    assert resultado == podcast.AudioGenerado(data=b"mp3-fake-bytes", formato="mp3", es_stub=False)
    assert ruta.calls.last.request.headers["xi-api-key"] == "clave-del-tenant"


@respx.mock
async def test_sintetizar_segmento_usa_voice_id_default_del_cfg_si_el_segmento_no_trae():
    ruta = respx.post("https://api.elevenlabs.io/v1/text-to-speech/voz-default").mock(
        return_value=httpx.Response(200, content=b"mp3-bytes")
    )
    cfg = {"provider": "elevenlabs", "api_key": "clave", "voice_id": "voz-default"}

    resultado = await podcast.sintetizar_segmento(cfg, texto="hola", voice_id=None)

    assert ruta.called
    assert resultado.formato == "mp3"


async def test_sintetizar_segmento_elevenlabs_sin_voice_id_en_ningun_lado_lanza():
    cfg = {"provider": "elevenlabs", "api_key": "clave"}
    with pytest.raises(podcast.SintesisError, match="voice_id"):
        await podcast.sintetizar_segmento(cfg, texto="hola", voice_id=None)


@respx.mock
async def test_sintetizar_segmento_elevenlabs_error_http_se_propaga_no_cae_a_stub():
    respx.post("https://api.elevenlabs.io/v1/text-to-speech/voz-1").mock(
        return_value=httpx.Response(401, text="unauthorized")
    )
    cfg = {"provider": "elevenlabs", "api_key": "clave-invalida"}
    with pytest.raises(podcast.SintesisError, match="401"):
        await podcast.sintetizar_segmento(cfg, texto="hola", voice_id="voz-1")


# ---------------------------------------------------------------------------
# generar_efecto — tenant -> stub
# ---------------------------------------------------------------------------


async def test_generar_efecto_sin_cfg_devuelve_beep_wav_stub():
    resultado = await podcast.generar_efecto(None, descripcion="aplausos")
    assert resultado.es_stub is True
    assert resultado.formato == "wav"
    with wave.open(io.BytesIO(resultado.data), "rb") as clip:
        assert clip.getnframes() == int(podcast._SAMPLE_RATE_HZ * podcast._BEEP_SEGUNDOS)


@respx.mock
async def test_generar_efecto_elevenlabs_real_devuelve_mp3():
    ruta = respx.post("https://api.elevenlabs.io/v1/sound-generation").mock(
        return_value=httpx.Response(200, content=b"efecto-mp3-fake")
    )
    cfg = {"provider": "elevenlabs", "api_key": "clave-tenant"}

    resultado = await podcast.generar_efecto(cfg, descripcion="lluvia suave sobre un techo")

    assert ruta.called
    assert resultado == podcast.AudioGenerado(
        data=b"efecto-mp3-fake", formato="mp3", es_stub=False
    )
    enviado = json.loads(ruta.calls.last.request.content)
    assert enviado["text"] == "lluvia suave sobre un techo"
    assert ruta.calls.last.request.headers["xi-api-key"] == "clave-tenant"


@respx.mock
async def test_generar_efecto_elevenlabs_error_se_propaga():
    respx.post("https://api.elevenlabs.io/v1/sound-generation").mock(
        return_value=httpx.Response(500, text="boom")
    )
    cfg = {"provider": "elevenlabs", "api_key": "clave"}
    with pytest.raises(podcast.SintesisError):
        await podcast.generar_efecto(cfg, descripcion="algo")


# ---------------------------------------------------------------------------
# Anti-fuga: nunca una credencial de plataforma como fallback silencioso
# ---------------------------------------------------------------------------


def test_funciones_de_tts_nunca_aceptan_un_settings_de_plataforma():
    """Regresión estructural (Barrido de seguridad v5, ver
    `DIRECCION_ACTUAL.md` "v4 completado", hallazgo #1): ninguna función de
    resolución/síntesis de este módulo declara un parámetro `settings` — no
    existe ningún hueco por el que una credencial de PLATAFORMA
    (`ELEVENLABS_API_KEY` de `.env`) pueda colarse como fallback silencioso."""
    for fn in (
        podcast.resolver_config_tts_tenant,
        podcast.sintetizar_segmento,
        podcast.generar_efecto,
    ):
        parametros = set(inspect.signature(fn).parameters)
        assert "settings" not in parametros, fn.__name__


async def test_sin_credencial_del_tenant_nunca_llama_a_elevenlabs_aunque_haya_centinela(
    monkeypatch,
):
    """Aunque una 'plataforma' tenga una API key real disponible en el
    entorno del proceso, sin credencial DEL TENANT (`cfg=None`) el audio se
    genera 100% offline — nunca se intenta ninguna llamada HTTP real, y el
    centinela nunca aparece en los bytes producidos."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", _SENTINEL)

    def _explota(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("no debía llamar a ElevenLabs sin credencial del tenant")

    monkeypatch.setattr(podcast, "_elevenlabs_text_to_speech", _explota)
    monkeypatch.setattr(podcast, "_elevenlabs_sound_generation", _explota)

    segmento = await podcast.sintetizar_segmento(None, texto="hola")
    assert segmento.es_stub is True
    assert _SENTINEL.encode() not in segmento.data

    efecto = await podcast.generar_efecto(None, descripcion="aplausos")
    assert efecto.es_stub is True
    assert _SENTINEL.encode() not in efecto.data


# ---------------------------------------------------------------------------
# Consolidación con edecan_voice (WP-V6-04) — ver docstring del módulo.
# ---------------------------------------------------------------------------


async def test_resolver_config_tts_tenant_delega_en_edecan_voice_tenant(monkeypatch):
    """Pinned: `resolver_config_tts_tenant` debe seguir delegando en
    `edecan_voice.tenant.resolver_config_tts_del_tenant` (no en
    `resolver_tts_del_tenant`, que devuelve un `TTSProvider`, no un dict —
    ver "Diferencia resuelta" en el docstring del módulo). Si algún día se
    re-duplica la lógica localmente o se delega en la función equivocada,
    este test lo detecta."""
    recibido: dict[str, Any] = {}

    async def _fake_resolver(ctx: Any) -> dict[str, Any] | None:
        recibido["tenant_id"] = ctx.tenant_id
        recibido["session"] = ctx.session
        recibido["vault"] = ctx.vault
        return {"provider": "elevenlabs", "api_key": "clave-delegada"}

    monkeypatch.setattr(podcast, "_resolver_config_tts_del_tenant", _fake_resolver)

    tenant_id = uuid4()
    session = object()
    vault = object()
    resultado = await podcast.resolver_config_tts_tenant(
        session=session, vault=vault, tenant_id=tenant_id
    )

    assert resultado == {"provider": "elevenlabs", "api_key": "clave-delegada"}
    assert recibido == {"tenant_id": tenant_id, "session": session, "vault": vault}


async def test_elevenlabs_sound_generation_delega_en_edecan_voice_cloning(monkeypatch):
    """Pinned: la llamada HTTP real de `_elevenlabs_sound_generation` debe
    seguir viniendo de `edecan_voice.cloning.generar_efecto` (mismo
    endpoint/headers/payload que ElevenLabs, ver docstring del módulo) en vez
    de reimplementarse localmente con `httpx` directo."""
    recibido: dict[str, Any] = {}

    async def _fake_generar_efecto(api_key: str, text: str, *, timeout: float) -> bytes:
        recibido["api_key"] = api_key
        recibido["text"] = text
        recibido["timeout"] = timeout
        return b"delegado-a-edecan-voice"

    monkeypatch.setattr(podcast, "_generar_efecto_elevenlabs", _fake_generar_efecto)

    resultado = await podcast._elevenlabs_sound_generation(
        api_key="clave-tenant", descripcion="lluvia suave"
    )

    assert resultado == b"delegado-a-edecan-voice"
    assert recibido == {
        "api_key": "clave-tenant",
        "text": "lluvia suave",
        "timeout": podcast._ELEVENLABS_EFFECT_TIMEOUT_SECONDS,
    }


async def test_elevenlabs_sound_generation_traduce_voice_cloning_error_a_sintesis_error(
    monkeypatch,
):
    """La única diferencia real entre las dos implementaciones (ver
    "Diferencia resuelta" en el docstring del módulo) es el tipo de
    excepción — se traduce en el borde para no romper el contrato público
    (`SintesisError`) que ya dependen `tools.py`/`generate_podcast.py`."""

    async def _fake_generar_efecto(api_key: str, text: str, *, timeout: float) -> bytes:
        raise podcast._VoiceCloningError("ElevenLabs rechazó generar el efecto (status 500): boom")

    monkeypatch.setattr(podcast, "_generar_efecto_elevenlabs", _fake_generar_efecto)

    with pytest.raises(podcast.SintesisError, match="boom"):
        await podcast._elevenlabs_sound_generation(api_key="clave", descripcion="algo")


# ---------------------------------------------------------------------------
# ensamblar_podcast (wav) — puro Python con `wave`
# ---------------------------------------------------------------------------


def _wav_clip(
    *, seconds: float, nchannels: int = 1, sampwidth: int = 2, framerate: int = 16000
) -> bytes:
    n_frames = int(framerate * seconds)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as clip:
        clip.setnchannels(nchannels)
        clip.setsampwidth(sampwidth)
        clip.setframerate(framerate)
        clip.writeframes(b"\x00" * n_frames * sampwidth * nchannels)
    return buffer.getvalue()


async def test_ensamblar_podcast_wav_concatena_dos_clips_puro_python():
    clip1 = _wav_clip(seconds=0.1)
    clip2 = _wav_clip(seconds=0.2)

    resultado = await podcast.ensamblar_podcast([clip1, clip2], "wav")

    with wave.open(io.BytesIO(resultado), "rb") as final:
        assert final.getnchannels() == 1
        assert final.getsampwidth() == 2
        assert final.getframerate() == 16000
        assert final.getnframes() == int(16000 * 0.1) + int(16000 * 0.2)


async def test_ensamblar_podcast_wav_con_parametros_distintos_da_error_claro():
    clip1 = _wav_clip(seconds=0.1, nchannels=1)
    clip2 = _wav_clip(seconds=0.1, nchannels=2)  # estéreo: incompatible con el primero

    with pytest.raises(podcast.EnsambladoError, match="formato WAV distinto"):
        await podcast.ensamblar_podcast([clip1, clip2], "wav")


async def test_ensamblar_podcast_un_solo_clip_no_requiere_wave_ni_ffmpeg(monkeypatch):
    def _explota() -> None:
        raise AssertionError("un solo clip no debía intentar ensamblarse")

    monkeypatch.setattr(podcast, "ffmpeg_disponible", _explota)
    resultado = await podcast.ensamblar_podcast([b"unico-clip-de-audio"], "mp3")
    assert resultado == b"unico-clip-de-audio"


async def test_ensamblar_podcast_lista_vacia_da_error():
    with pytest.raises(podcast.EnsambladoError):
        await podcast.ensamblar_podcast([], "wav")


async def test_ensamblar_podcast_formato_no_soportado_da_error():
    with pytest.raises(podcast.EnsambladoError, match="no soportado"):
        await podcast.ensamblar_podcast([b"a", b"b"], "ogg")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# ensamblar_podcast (mp3) — ffmpeg mockeado (nunca requiere ffmpeg real)
# ---------------------------------------------------------------------------


class _FakeFFmpegProcess:
    def __init__(self, *, returncode: int = 0, stderr: bytes = b"") -> None:
        self.returncode = returncode
        self._stderr = stderr

    async def communicate(self) -> tuple[bytes, bytes]:
        return b"", self._stderr

    def kill(self) -> None:
        pass

    async def wait(self) -> None:
        return None


async def test_ensamblar_podcast_mp3_usa_ffmpeg_concat_demuxer_sin_shell(monkeypatch):
    monkeypatch.setattr(podcast, "ffmpeg_disponible", lambda: "/usr/bin/ffmpeg")
    llamadas: list[list[str]] = []

    async def _fake_exec(*args: str, **kwargs: Any) -> _FakeFFmpegProcess:
        llamadas.append(list(args))
        # El último argumento del comando armado por `_concatenar_mp3_ffmpeg`
        # es la ruta de salida — se simula que ffmpeg la escribió.
        Path(args[-1]).write_bytes(b"contenido-mp3-concatenado-falso")
        return _FakeFFmpegProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)

    resultado = await podcast.ensamblar_podcast([b"clip-mp3-1", b"clip-mp3-2"], "mp3")

    assert resultado == b"contenido-mp3-concatenado-falso"
    assert len(llamadas) == 1
    comando = llamadas[0]
    assert comando[0] == "/usr/bin/ffmpeg"
    assert "-f" in comando
    assert "concat" in comando
    # JAMÁS shell=True: `create_subprocess_exec` recibe una lista de
    # argumentos, nunca una sola cadena armada a mano.
    assert all(isinstance(arg, str) for arg in comando)


async def test_ensamblar_podcast_mp3_sin_ffmpeg_da_error_instructivo():
    with pytest.raises(podcast.EnsambladoError, match="ffmpeg"):
        await podcast.ensamblar_podcast([b"clip1", b"clip2"], "mp3")


async def test_ensamblar_podcast_mp3_ffmpeg_termina_con_error_se_propaga(monkeypatch):
    monkeypatch.setattr(podcast, "ffmpeg_disponible", lambda: "/usr/bin/ffmpeg")

    async def _fake_exec(*args: str, **kwargs: Any) -> _FakeFFmpegProcess:
        return _FakeFFmpegProcess(returncode=1, stderr=b"algo salio mal")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)

    with pytest.raises(podcast.EnsambladoError, match="algo salio mal"):
        await podcast.ensamblar_podcast([b"clip1", b"clip2"], "mp3")


@pytest.mark.integration
async def test_ensamblar_podcast_mp3_con_ffmpeg_real_si_esta_instalado():
    """Opcional: solo corre de verdad si el sistema tiene ffmpeg instalado.
    Marcado `integration` para poder excluirlo con `-m "not integration"`
    (ver `docs/creatividad.md`) — la suite normal nunca requiere ffmpeg."""
    if podcast.ffmpeg_disponible() is None:
        pytest.skip("ffmpeg no está instalado en esta máquina")

    clip1 = _wav_clip(seconds=0.05)
    clip2 = _wav_clip(seconds=0.05)
    resultado = await podcast.ensamblar_podcast([clip1, clip2], "wav")
    assert isinstance(resultado, bytes) and len(resultado) > 0


# ---------------------------------------------------------------------------
# slugify
# ---------------------------------------------------------------------------


def test_slugify_normaliza_acentos_mayusculas_y_espacios():
    assert podcast.slugify("Mi Podcast: Episodio Uno") == "mi-podcast-episodio-uno"


def test_slugify_cadena_vacia_cae_al_default():
    assert podcast.slugify("   ") == "podcast"


def test_slugify_se_acota_a_60_caracteres():
    assert len(podcast.slugify("x" * 200)) <= 60
