"""Tests de `edecan_voice.tools` (`listar_voces`/`sintetizar_voz`, WP-V5-10).

Fakes deliberadamente ligeros, por duck typing (mismo criterio que
`packages/ads/tests/conftest.py`/`packages/creative/tests/conftest.py`, ver
sus docstrings): `ctx` es un `SimpleNamespace`, no un `edecan_core.ToolContext`
real, aunque este paquete SÍ declare `edecan-core` como dependencia real —
`edecan_ads` hace lo mismo pese a la misma dependencia. Sin `conftest.py`
propio (no está en la lista de archivos de este paquete de trabajo): los
fixtures se definen directo en este módulo.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
import respx
from edecan_voice.cloning import VoiceCloningError, VozDisponible
from edecan_voice.stubs import StubTTS
from edecan_voice.tools import (
    VOCES_STUB,
    ConfigurarAgenteLlamadasTool,
    ListarAgentesLlamadasTool,
    ListarVocesTool,
    LlamarContactoTool,
    SintetizarVozTool,
    _estimate_seconds_from_text,
    _subir_archivo,
    get_all_tools,
)

FAKE_API_KEY = "fake-elevenlabs-key"
CUENTA_ID = "11111111-1111-1111-1111-111111111111"


# ---------------------------------------------------------------------------
# Fixtures / dobles locales
# ---------------------------------------------------------------------------


class FakeResult:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self._rows = rows or []

    def mappings(self) -> FakeResult:
        return self

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def all(self) -> list[dict[str, Any]]:
        return self._rows


@dataclass
class FakeSession:
    respuestas: list[list[dict[str, Any]]] = field(default_factory=list)
    llamadas: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> FakeResult:
        self.llamadas.append((str(stmt), dict(params or {})))
        filas = self.respuestas.pop(0) if self.respuestas else []
        return FakeResult(filas)


@dataclass
class FakeVault:
    bundle: Any = None
    llamadas: list[tuple[Any, Any]] = field(default_factory=list)

    async def get(self, tenant_id: Any, connector_account_id: Any) -> Any:
        self.llamadas.append((tenant_id, connector_account_id))
        return self.bundle


@dataclass
class FakeUploader:
    file_id: UUID = field(default_factory=uuid4)
    llamadas: list[dict[str, Any]] = field(default_factory=list)

    async def __call__(
        self, ctx: Any, *, data: bytes, filename: str, mime: str
    ) -> tuple[UUID, str]:
        self.llamadas.append({"data": data, "filename": filename, "mime": mime})
        return self.file_id, filename


class FakeTTSProvider:
    """`TTSProvider` falso: devuelve bytes fijos y registra la última llamada."""

    def __init__(self, audio: bytes = b"FAKE-AUDIO-BYTES") -> None:
        self._audio = audio
        self.llamadas: list[dict[str, Any]] = []

    async def synthesize(self, text: str, voice_id: str | None = None, fmt: str = "mp3") -> bytes:
        self.llamadas.append({"text": text, "voice_id": voice_id, "fmt": fmt})
        return self._audio


def _bundle(config: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(access_token=json.dumps(config))


@pytest.fixture
def make_ctx():
    def _make_ctx(
        *,
        session: Any = None,
        settings: Any = None,
        vault: Any = None,
        tenant_id: UUID | None = None,
        user_id: UUID | None = None,
        extras: dict[str, Any] | None = None,
    ) -> SimpleNamespace:
        return SimpleNamespace(
            tenant_id=tenant_id or uuid4(),
            user_id=user_id or uuid4(),
            session=session if session is not None else FakeSession(),
            settings=settings if settings is not None else SimpleNamespace(),
            llm=None,
            vault=vault,
            extras=extras or {},
        )

    return _make_ctx


# ---------------------------------------------------------------------------
# get_all_tools / metadatos pinned
# ---------------------------------------------------------------------------


def test_get_all_tools_incluye_telefonia_conversacional():
    tools = get_all_tools()
    nombres = [t.name for t in tools]
    assert nombres == [
        "listar_voces",
        "sintetizar_voz",
        "listar_agentes_llamadas",
        "configurar_agente_llamadas",
        "llamar_contacto",
    ]


def test_listar_voces_no_es_dangerous_y_gatea_voice_web():
    tool = ListarVocesTool()
    assert tool.dangerous is False
    assert tool.requires_flags == frozenset({"voice.web"})


def test_sintetizar_voz_no_es_dangerous_y_gatea_voice_web():
    tool = SintetizarVozTool()
    assert tool.dangerous is False
    assert tool.requires_flags == frozenset({"voice.web"})


def test_llamar_contacto_es_dangerous_y_gatea_telefonia():
    tool = LlamarContactoTool()
    assert tool.dangerous is True
    assert tool.requires_flags == frozenset({"voice.telephony"})


def test_configurar_y_listar_agentes_gatean_telefonia_sin_ser_dangerous():
    for tool in (ConfigurarAgenteLlamadasTool(), ListarAgentesLlamadasTool()):
        assert tool.dangerous is False
        assert tool.requires_flags == frozenset({"voice.telephony"})


async def test_configurar_agente_desde_chat_guarda_contexto_autorizado(make_ctx):
    saved_id = uuid4()
    session = FakeSession(
        respuestas=[
            [],  # agente inexistente
            [{"total": 0}],
            [],
            [
                {
                    "id": saved_id,
                    "name": "Negocios",
                    "agent_name": "Valentina",
                    "default_goal": "Acordar una reunión",
                    "is_default": True,
                }
            ],
        ]
    )
    result = await ConfigurarAgenteLlamadasTool().run(
        make_ctx(session=session),
        {
            "nombre": "Negocios",
            "identidad": "Valentina",
            "personalidad": "Consultiva y clara.",
            "objetivo": "Acordar una reunión",
            "contexto_autorizado": "La demostración dura 20 minutos.",
            "informacion_a_obtener": "Necesidad y fecha.",
        },
    )
    assert result.data["nombre"] == "Negocios"
    insert_sql, insert_params = session.llamadas[-1]
    assert "INSERT INTO phone_agent_templates" in insert_sql
    assert insert_params["knowledge_context"] == "La demostración dura 20 minutos."
    assert insert_params["required_information"] == "Necesidad y fecha."
    assert insert_params["is_default"] is True


async def test_listar_agentes_devuelve_nombres_exactos(make_ctx):
    session = FakeSession(
        respuestas=[
            [
                {
                    "id": uuid4(),
                    "name": "Agente de negocios",
                    "agent_name": "Valentina",
                    "default_goal": "Presentar la propuesta",
                    "is_default": True,
                }
            ]
        ]
    )
    result = await ListarAgentesLlamadasTool().run(make_ctx(session=session), {})
    assert result.data["agentes"][0]["nombre"] == "Agente de negocios"
    assert "predeterminado y entrantes" in result.content


async def test_llamar_contacto_delega_en_dispatcher_transaccional(make_ctx):
    calls: list[dict[str, str]] = []

    async def dispatch(**kwargs):
        calls.append(kwargs)
        return {"call_id": uuid4(), "conversation_id": uuid4(), "status": "queued"}

    ctx = make_ctx(extras={"phone_call_dispatcher": dispatch})
    result = await LlamarContactoTool().run(
        ctx,
        {"telefono_e164": " +573001234567 ", "objetivo": " Confirmar  la cita "},
    )
    assert calls == [{"to_e164": "+573001234567", "goal": "Confirmar la cita"}]
    assert result.data["status"] == "queued"

    await LlamarContactoTool().run(
        ctx,
        {
            "telefono_e164": "+573001234568",
            "objetivo": "Presentar la propuesta",
            "agente": "Negocios",
        },
    )
    assert calls[-1] == {
        "to_e164": "+573001234568",
        "goal": "Presentar la propuesta",
        "agent_ref": "Negocios",
    }


def test_ninguna_tool_de_voz_avanzada_clona_nada():
    """Ver `edecan_voice.cloning` ("El agente JAMÁS clona una voz"): ninguna
    de las dos tools expone `crear_clon`/`borrar_clon`."""
    for tool in get_all_tools():
        assert "clon" not in tool.name


# ---------------------------------------------------------------------------
# ListarVocesTool
# ---------------------------------------------------------------------------


async def test_listar_voces_sin_credencial_devuelve_stubs_deterministas(make_ctx):
    ctx = make_ctx(session=FakeSession([[]]), vault=FakeVault())
    resultado = await ListarVocesTool().run(ctx, {})

    assert resultado.data["voces"] == [
        {
            "voice_id": v.voice_id,
            "nombre": v.nombre,
            "categoria": v.categoria,
            "preview_url": v.preview_url,
        }
        for v in VOCES_STUB
    ]
    assert len(VOCES_STUB) == 2


async def test_listar_voces_sin_contexto_suficiente_devuelve_stubs(make_ctx):
    ctx = make_ctx()  # session=None (default de SimpleNamespace via FakeSession), vault=None
    ctx.session = None
    resultado = await ListarVocesTool().run(ctx, {})
    assert len(resultado.data["voces"]) == 2


@respx.mock
async def test_listar_voces_con_elevenlabs_configurado_usa_el_catalogo_real(make_ctx):
    respx.get("https://api.elevenlabs.io/v1/voices").mock(
        return_value=httpx.Response(
            200,
            json={"voices": [{"voice_id": "voz-real", "name": "Voz Real", "category": "cloned"}]},
        )
    )
    session = FakeSession([[{"id": CUENTA_ID}]])
    vault = FakeVault(bundle=_bundle({"provider": "elevenlabs", "api_key": FAKE_API_KEY}))
    ctx = make_ctx(session=session, vault=vault)

    resultado = await ListarVocesTool().run(ctx, {})

    assert resultado.data["voces"] == [
        {"voice_id": "voz-real", "nombre": "Voz Real", "categoria": "cloned", "preview_url": None}
    ]
    assert "Voz Real" in resultado.content


async def test_listar_voces_polly_no_es_elevenlabs_usa_stubs(make_ctx):
    session = FakeSession([[{"id": CUENTA_ID}]])
    vault = FakeVault(bundle=_bundle({"provider": "polly", "voice": "Mia"}))
    ctx = make_ctx(session=session, vault=vault)

    resultado = await ListarVocesTool().run(ctx, {})

    assert len(resultado.data["voces"]) == 2  # cae a VOCES_STUB, nunca llama a ElevenLabs


@respx.mock
async def test_listar_voces_error_de_elevenlabs_no_rompe_la_tool(make_ctx):
    respx.get("https://api.elevenlabs.io/v1/voices").mock(
        return_value=httpx.Response(401, text="invalid_api_key")
    )
    session = FakeSession([[{"id": CUENTA_ID}]])
    vault = FakeVault(bundle=_bundle({"provider": "elevenlabs", "api_key": FAKE_API_KEY}))
    ctx = make_ctx(session=session, vault=vault)

    resultado = await ListarVocesTool().run(ctx, {})

    assert isinstance(resultado.content, str)
    assert "ElevenLabs" in resultado.content
    assert FAKE_API_KEY not in resultado.content


# ---------------------------------------------------------------------------
# SintetizarVozTool
# ---------------------------------------------------------------------------


async def test_sintetizar_voz_sin_texto_pide_texto(make_ctx):
    ctx = make_ctx()
    resultado = await SintetizarVozTool().run(ctx, {"texto": "   "})
    assert "texto" in resultado.content.lower()
    assert resultado.data is None


async def test_sintetizar_voz_usa_proveedor_inyectado_y_sube_el_archivo(make_ctx):
    tts = FakeTTSProvider(audio=b"AUDIO-MP3-FALSO")
    uploader = FakeUploader()
    tool = SintetizarVozTool(tts_provider=tts, uploader=uploader)
    ctx = make_ctx()

    resultado = await tool.run(ctx, {"texto": "Hola mundo", "voice_id": "voz-1"})

    assert tts.llamadas == [{"text": "Hola mundo", "voice_id": "voz-1", "fmt": "mp3"}]
    assert len(uploader.llamadas) == 1
    subida = uploader.llamadas[0]
    assert subida["data"] == b"AUDIO-MP3-FALSO"
    assert subida["mime"] == "audio/mpeg"
    assert subida["filename"].endswith(".mp3")
    assert resultado.data == {
        "file_id": str(uploader.file_id),
        "filename": subida["filename"],
        "mime": "audio/mpeg",
        "caption": "Hola mundo",
    }
    assert "Hola mundo" in resultado.content


async def test_sintetizar_voz_sin_voice_id_pasa_none(make_ctx):
    tts = FakeTTSProvider()
    tool = SintetizarVozTool(tts_provider=tts, uploader=FakeUploader())
    ctx = make_ctx()

    await tool.run(ctx, {"texto": "Hola"})

    assert tts.llamadas[0]["voice_id"] is None


async def test_sintetizar_voz_con_stub_tts_produce_wav(make_ctx):
    tool = SintetizarVozTool(tts_provider=StubTTS(), uploader=FakeUploader())
    ctx = make_ctx()

    resultado = await tool.run(ctx, {"texto": "Hola"})

    subida_filename = resultado.data["filename"]
    assert subida_filename.endswith(".wav")


async def test_sintetizar_voz_trunca_texto_largo(make_ctx):
    tts = FakeTTSProvider()
    tool = SintetizarVozTool(tts_provider=tts, uploader=FakeUploader())
    ctx = make_ctx()
    texto_largo = "a" * 5000

    await tool.run(ctx, {"texto": texto_largo})

    assert len(tts.llamadas[0]["text"]) == 3000


async def test_sintetizar_voz_preview_del_content_se_trunca(make_ctx):
    tts = FakeTTSProvider()
    tool = SintetizarVozTool(tts_provider=tts, uploader=FakeUploader())
    ctx = make_ctx()
    texto = "x" * 200

    resultado = await tool.run(ctx, {"texto": texto})

    assert "…" in resultado.content


async def test_sintetizar_voz_sin_proveedor_inyectado_resuelve_del_tenant(make_ctx):
    """Sin `tts_provider=...` explícito, usa `resolver_tts_del_tenant` — sin
    credencial conectada, cae a `StubTTS` (mismo criterio "tenant → stub")."""
    tool = SintetizarVozTool(uploader=FakeUploader())
    ctx = make_ctx(session=FakeSession([[]]), vault=FakeVault())

    resultado = await tool.run(ctx, {"texto": "Hola"})

    assert resultado.data["filename"].endswith(".wav")  # StubTTS produce wav


# ---------------------------------------------------------------------------
# Cuota mensual de voz (`limits.voice_minutes_month`) — misma cuota que
# `POST /v1/voice/speak` (`apps/api/edecan_api/routers/voice.py`), ver
# docstring del módulo. Anti-fuga: sin esto, `sintetizar_voz` generaba audio
# sin límite por chat aunque el tenant ya hubiera agotado su cupo web.
# ---------------------------------------------------------------------------


async def test_sintetizar_voz_sin_cupo_no_llama_al_proveedor_ni_sube_nada(make_ctx):
    tts = FakeTTSProvider()
    uploader = FakeUploader()
    tool = SintetizarVozTool(tts_provider=tts, uploader=uploader)
    # Ya consumió 999999s este mes; el plan solo permite 1 minuto (60s).
    session = FakeSession([[{"total": 999999}]])
    ctx = make_ctx(session=session)
    ctx.extras["flags"] = {"limits.voice_minutes_month": 1}

    resultado = await tool.run(ctx, {"texto": "Hola mundo"})

    assert tts.llamadas == []  # nunca se gasta la credencial bring-your-own del tenant
    assert uploader.llamadas == []
    assert resultado.data is None
    assert "límite" in resultado.content.lower()
    assert "1 minuto" in resultado.content.lower() or "1 minutos" in resultado.content.lower()


async def test_sintetizar_voz_justo_en_el_limite_todavia_pasa(make_ctx):
    """Mismo límite que `voice.py::_check_voice_quota`: `<=`, no `<` —
    permite la última llamada que deja el consumo exactamente en el tope."""
    tts = FakeTTSProvider()
    tool = SintetizarVozTool(tts_provider=tts, uploader=FakeUploader())
    texto = "Hola"
    estimado = _estimate_seconds_from_text(texto)
    limite_segundos = 60  # 1 minuto
    session = FakeSession([[{"total": limite_segundos - estimado}]])
    ctx = make_ctx(session=session)
    ctx.extras["flags"] = {"limits.voice_minutes_month": 1}

    resultado = await tool.run(ctx, {"texto": texto})

    assert len(tts.llamadas) == 1
    assert resultado.data is not None


async def test_sintetizar_voz_bajo_cupo_registra_usage_event_voice_seconds(make_ctx):
    tts = FakeTTSProvider()
    tool = SintetizarVozTool(tts_provider=tts, uploader=FakeUploader())
    texto = "Hola mundo, esto es una prueba de síntesis de voz."
    session = FakeSession([[{"total": 0}]])  # sin consumo previo este mes
    ctx = make_ctx(session=session)
    ctx.extras["flags"] = {"limits.voice_minutes_month": 100}

    resultado = await tool.run(ctx, {"texto": texto})

    assert resultado.data is not None
    esperado = _estimate_seconds_from_text(texto)
    sql_insert, params = session.llamadas[-1]
    assert "INSERT INTO usage_events" in sql_insert
    assert "voice_seconds" in sql_insert
    assert params["tenant_id"] == str(ctx.tenant_id)
    assert params["quantity"] == esperado


async def test_sintetizar_voz_plan_ilimitado_no_consulta_la_cuota(make_ctx):
    """`limits.voice_minutes_month = -1` (ilimitado, mismo sentinel que
    `voice.py::_check_voice_quota`): ni siquiera consulta `usage_events` para
    decidir, mismo atajo que el router — solo queda la escritura final de
    `_registrar_uso_de_voz`."""
    tts = FakeTTSProvider()
    tool = SintetizarVozTool(tts_provider=tts, uploader=FakeUploader())
    session = FakeSession()  # ninguna respuesta encolada: si se consultara, reventaría el conteo
    ctx = make_ctx(session=session)
    ctx.extras["flags"] = {"limits.voice_minutes_month": -1}

    resultado = await tool.run(ctx, {"texto": "Hola"})

    assert resultado.data is not None
    assert len(session.llamadas) == 1  # solo el INSERT de usage_events, nunca el SELECT SUM


async def test_sintetizar_voz_sin_flags_en_extras_se_trata_como_ilimitado(make_ctx):
    """`ctx.extras["flags"]` ausente (turno sin flags, `_tenant_flags` cae a
    `{}`) — mismo comportamiento fail-open que `voice.py::_check_voice_quota`
    con `tenant.flags.get(LIMIT_VOICE_MINUTES_MONTH, UNLIMITED)`: no bloquea."""
    tts = FakeTTSProvider()
    tool = SintetizarVozTool(tts_provider=tts, uploader=FakeUploader())
    ctx = make_ctx()  # extras={} por defecto, mismo fixture que el resto del archivo

    resultado = await tool.run(ctx, {"texto": "Hola"})

    assert len(tts.llamadas) == 1
    assert resultado.data is not None


# ---------------------------------------------------------------------------
# _subir_archivo (uploader real por defecto) — S3 mockeado con un doble local,
# mismo patrón que `apps/api/tests/api_fakes.py::_FakeAioboto3Session`.
# ---------------------------------------------------------------------------


class _FakeS3Client:
    def __init__(self, calls: list[dict[str, Any]]) -> None:
        self._calls = calls

    async def __aenter__(self) -> _FakeS3Client:
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        return None

    async def put_object(self, **kwargs: Any) -> None:
        self._calls.append(kwargs)


class _FakeAioboto3Session:
    def __init__(self, calls: list[dict[str, Any]]) -> None:
        self._calls = calls

    def client(self, service_name: str, **kwargs: Any) -> _FakeS3Client:
        assert service_name == "s3"
        return _FakeS3Client(self._calls)


async def test_subir_archivo_sube_a_s3_e_inserta_fila_files(make_ctx, monkeypatch):
    import edecan_voice.tools as tools_module

    s3_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(tools_module.aioboto3, "Session", lambda: _FakeAioboto3Session(s3_calls))
    session = FakeSession()
    ctx = make_ctx(session=session, settings=SimpleNamespace(S3_BUCKET="mi-bucket"))

    file_id, filename = await _subir_archivo(
        ctx, data=b"audio-bytes", filename="voz-abc.mp3", mime="audio/mpeg"
    )

    assert filename == "voz-abc.mp3"
    assert len(s3_calls) == 1
    assert s3_calls[0]["Bucket"] == "mi-bucket"
    assert s3_calls[0]["Key"] == f"tenants/{ctx.tenant_id}/files/{file_id}/voz-abc.mp3"
    assert s3_calls[0]["ContentType"] == "audio/mpeg"

    sql_insert, params = session.llamadas[0]
    assert "INSERT INTO files" in sql_insert
    assert params["tenant_id"] == str(ctx.tenant_id)
    assert params["filename"] == "voz-abc.mp3"


async def test_subir_archivo_usa_bucket_por_defecto_sin_settings(make_ctx, monkeypatch):
    import edecan_voice.tools as tools_module

    s3_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(tools_module.aioboto3, "Session", lambda: _FakeAioboto3Session(s3_calls))
    ctx = make_ctx(session=FakeSession(), settings=SimpleNamespace())

    await _subir_archivo(ctx, data=b"x", filename="f.mp3", mime="audio/mpeg")

    assert s3_calls[0]["Bucket"] == "edecan-files"


# ---------------------------------------------------------------------------
# Anti-fuga: `VoiceCloningError` real (no simulado) tampoco se filtra a través
# del content de la tool.
# ---------------------------------------------------------------------------


async def test_listar_voces_error_real_de_voicecloningerror_no_filtra_la_key(make_ctx, monkeypatch):
    import edecan_voice.tools as tools_module

    async def _listar_voces_que_falla(api_key: str) -> list[VozDisponible]:
        raise VoiceCloningError("ElevenLabs rechazó (status 401): mensaje del proveedor")

    monkeypatch.setattr(tools_module.cloning, "listar_voces", _listar_voces_que_falla)
    session = FakeSession([[{"id": CUENTA_ID}]])
    vault = FakeVault(bundle=_bundle({"provider": "elevenlabs", "api_key": FAKE_API_KEY}))
    ctx = make_ctx(session=session, vault=vault)

    resultado = await ListarVocesTool().run(ctx, {})

    assert FAKE_API_KEY not in resultado.content
