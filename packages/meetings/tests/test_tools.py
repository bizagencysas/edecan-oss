"""Tests de `edecan_meetings.tools.ResumirReunionTool` — offline: `ctx.session`
falso, `enqueue` monkeypatcheado sobre el nombre importado en el módulo bajo
prueba (mismo patrón que `packages/creative/tests/test_tools.py`)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest
from edecan_core import ToolContext
from edecan_meetings import tools as tools_module
from edecan_meetings.tools import DISCLAIMER_CONSENTIMIENTO, ResumirReunionTool


class _FakeResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> _FakeResult:
        return self

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None


@dataclass
class _FakeSession:
    respuestas: list[list[dict[str, Any]]] = field(default_factory=list)
    llamadas: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _FakeResult:
        self.llamadas.append((str(stmt), dict(params or {})))
        filas = self.respuestas.pop(0) if self.respuestas else []
        return _FakeResult(filas)


def _install_fake_enqueue(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, str, dict, Any]]:
    llamadas: list[tuple[Any, str, dict, Any]] = []

    async def fake_enqueue(
        settings: Any, job_type: str, payload: dict, tenant_id: Any
    ) -> uuid.UUID:
        llamadas.append((settings, job_type, payload, tenant_id))
        return uuid.uuid4()

    monkeypatch.setattr(tools_module, "enqueue", fake_enqueue)
    return llamadas


def _make_ctx(session: _FakeSession, *, tenant_id: uuid.UUID, user_id: uuid.UUID) -> ToolContext:
    return ToolContext(
        tenant_id=tenant_id,
        user_id=user_id,
        session=session,
        settings=object(),
        llm=None,
        vault=None,
        extras={},
    )


# ---------------------------------------------------------------------------
# Metadatos de la tool
# ---------------------------------------------------------------------------


def test_metadatos_de_la_tool() -> None:
    tool = ResumirReunionTool()
    assert tool.name == "resumir_reunion"
    assert tool.requires_flags == frozenset({"tools.meetings"})
    assert tool.dangerous is False
    assert "archivo" in tool.input_schema["required"]


# ---------------------------------------------------------------------------
# Validación de 'archivo'
# ---------------------------------------------------------------------------


async def test_archivo_no_es_uuid_no_encola(monkeypatch: pytest.MonkeyPatch) -> None:
    llamadas = _install_fake_enqueue(monkeypatch)
    session = _FakeSession()
    ctx = _make_ctx(session, tenant_id=uuid.uuid4(), user_id=uuid.uuid4())

    resultado = await ResumirReunionTool().run(ctx, {"archivo": "no-es-un-uuid"})

    assert "identificador válido" in resultado.content
    assert llamadas == []
    assert session.llamadas == []  # ni siquiera consulta la DB


async def test_archivo_no_encontrado_no_encola(monkeypatch: pytest.MonkeyPatch) -> None:
    llamadas = _install_fake_enqueue(monkeypatch)
    session = _FakeSession(respuestas=[[]])  # SELECT files -> nada
    ctx = _make_ctx(session, tenant_id=uuid.uuid4(), user_id=uuid.uuid4())

    resultado = await ResumirReunionTool().run(ctx, {"archivo": str(uuid.uuid4())})

    assert "No encontré ese archivo" in resultado.content
    assert llamadas == []


async def test_archivo_no_es_audio_ni_video_no_encola(monkeypatch: pytest.MonkeyPatch) -> None:
    llamadas = _install_fake_enqueue(monkeypatch)
    file_id = uuid.uuid4()
    session = _FakeSession(
        respuestas=[[{"id": file_id, "filename": "contrato.pdf", "mime": "application/pdf"}]]
    )
    ctx = _make_ctx(session, tenant_id=uuid.uuid4(), user_id=uuid.uuid4())

    resultado = await ResumirReunionTool().run(ctx, {"archivo": str(file_id)})

    assert "no parece un audio o video" in resultado.content
    assert llamadas == []


async def test_query_de_archivo_filtra_por_tenant_actual(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_enqueue(monkeypatch)
    tenant_id = uuid.uuid4()
    file_id = uuid.uuid4()
    session = _FakeSession(
        respuestas=[[{"id": file_id, "filename": "reunion.mp3", "mime": "audio/mpeg"}]]
    )
    ctx = _make_ctx(session, tenant_id=tenant_id, user_id=uuid.uuid4())

    await ResumirReunionTool().run(ctx, {"archivo": str(file_id)})

    sql, params = session.llamadas[0]
    assert "files" in sql
    assert params["tenant_id"] == str(tenant_id)
    assert params["id"] == str(file_id)


# ---------------------------------------------------------------------------
# Camino feliz
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mime", ["audio/mpeg", "audio/wav", "video/mp4", "video/quicktime"])
async def test_camino_feliz_encola_process_meeting(
    monkeypatch: pytest.MonkeyPatch, mime: str
) -> None:
    llamadas = _install_fake_enqueue(monkeypatch)
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    file_id = uuid.uuid4()
    session = _FakeSession(respuestas=[[{"id": file_id, "filename": "reunion.dat", "mime": mime}]])
    ctx = _make_ctx(session, tenant_id=tenant_id, user_id=user_id)

    resultado = await ResumirReunionTool().run(
        ctx, {"archivo": str(file_id), "titulo": "Kickoff del proyecto"}
    )

    assert len(llamadas) == 1
    settings, job_type, payload, enq_tenant_id = llamadas[0]
    assert job_type == "process_meeting"
    assert payload == {
        "file_id": str(file_id),
        "titulo": "Kickoff del proyecto",
        "user_id": str(user_id),
    }
    assert enq_tenant_id == tenant_id
    assert DISCLAIMER_CONSENTIMIENTO in resultado.content
    assert resultado.data == {
        "file_id": str(file_id),
        "filename": "reunion.dat",
        "mime": mime,
        "titulo": "Kickoff del proyecto",
    }


async def test_sin_titulo_usa_el_nombre_del_archivo(monkeypatch: pytest.MonkeyPatch) -> None:
    llamadas = _install_fake_enqueue(monkeypatch)
    file_id = uuid.uuid4()
    session = _FakeSession(
        respuestas=[[{"id": file_id, "filename": "standup-lunes.m4a", "mime": "audio/mp4"}]]
    )
    ctx = _make_ctx(session, tenant_id=uuid.uuid4(), user_id=uuid.uuid4())

    await ResumirReunionTool().run(ctx, {"archivo": str(file_id)})

    _settings, _job_type, payload, _tenant_id = llamadas[0]
    assert payload["titulo"] == "standup-lunes.m4a"


async def test_titulo_se_acota_a_200_caracteres(monkeypatch: pytest.MonkeyPatch) -> None:
    llamadas = _install_fake_enqueue(monkeypatch)
    file_id = uuid.uuid4()
    session = _FakeSession(
        respuestas=[[{"id": file_id, "filename": "reunion.mp3", "mime": "audio/mpeg"}]]
    )
    ctx = _make_ctx(session, tenant_id=uuid.uuid4(), user_id=uuid.uuid4())

    titulo_largo = "x" * 500
    await ResumirReunionTool().run(ctx, {"archivo": str(file_id), "titulo": titulo_largo})

    _settings, _job_type, payload, _tenant_id = llamadas[0]
    assert len(payload["titulo"]) == 200


def test_disclaimer_es_exactamente_el_string_pinned() -> None:
    assert DISCLAIMER_CONSENTIMIENTO == (
        "Recuerda: asegúrate de contar con el consentimiento de todos los "
        "participantes para grabar y transcribir esta reunión."
    )
