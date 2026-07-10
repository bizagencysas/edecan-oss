"""`edecan_api.routers.voz_avanzada` — `/v1/voz/podcasts*` (WP-V6-04; ver el
docstring del propio router, secciones "Podcasts" y "Tabla `podcasts`", para
el contrato completo).

Archivo dedicado y separado de `test_voz_avanzada.py` (que cubre `/voces` y
`/clones`, sin tocarlos aquí) — mismo router (`voz_avanzada.router`), pero un
"vertical" nuevo (podcasts, `ARCHITECTURE.md` §15.b/§15.e — la tabla y sus
endpoints aterrizaron durante este mismo WP): estos tests usan `FakeSession`
igual que `test_voz_avanzada.py`/`test_erp_router.py`, así que NO dependen de
que la tabla `podcasts` exista de verdad — corren 100% offline incluso
después de que la migración real aterrizó.

`edecan_core.queue.enqueue` se monkeypatchea sobre el símbolo importado en el
propio módulo del router (`voz_avanzada.enqueue`), mismo patrón EXACTO que
`test_files.py::_patch_s3_and_queue` para `POST /v1/files`.

Modelo de precio de pago único (2026-07-09, `edecan_schemas.plans`
docstring): `tools.podcast` (y cualquier otro flag) ya está en `True` en las
4 entradas de `PLANES` por igual — no hay más "flag apagado" que probar. Los
tests usan `_headers_con_podcast` con cualquier `plan_key` válido (mismo
criterio que `test_ads_router.py`/`test_erp_router.py`), sin necesitar el
truco de `dependency_overrides[get_current_user]` que usa `test_voz_avanzada.py`.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest
from conftest import auth_headers
from httpx import ASGITransport, AsyncClient

from edecan_api import deps as edecan_deps
from edecan_api.routers import voz_avanzada

PLAN_CON_TOOLS_PODCAST = "hosted_pro"


class FakeResult:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self._rows = rows or []

    def mappings(self) -> FakeResult:
        return self

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def all(self) -> list[dict[str, Any]]:
        return list(self._rows)


@dataclass
class FakeSession:
    """Duplicada a propósito de `test_voz_avanzada.py::FakeSession` (mismo
    criterio "ARCHITECTURE.md §10.1", ver docstring del módulo): cola de
    respuestas programadas en el ORDEN EXACTO en que el router las pide."""

    respuestas: list[list[dict[str, Any]]] = field(default_factory=list)
    llamadas: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    commits: int = 0

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> FakeResult:
        self.llamadas.append((str(stmt), dict(params or {})))
        filas = self.respuestas.pop(0) if self.respuestas else []
        return FakeResult(filas)

    async def flush(self) -> None:
        pass

    async def commit(self) -> None:
        self.commits += 1


@pytest.fixture
def fake_session() -> FakeSession:
    return FakeSession()


@pytest.fixture
def enqueue_calls() -> list[dict[str, Any]]:
    return []


@pytest.fixture
def _mounted_app(app, fake_session: FakeSession, enqueue_calls: list[dict[str, Any]], monkeypatch):
    """`app` (de `conftest.py`) + `voz_avanzada.router` montado +
    `get_tenant_session` reemplazado por `fake_session` (mismo patrón que
    `test_voz_avanzada.py`/`test_erp_router.py`), más `enqueue` mockeado
    (mismo patrón que `test_files.py::_patch_s3_and_queue`)."""
    app.include_router(voz_avanzada.router)
    app.dependency_overrides[edecan_deps.get_tenant_session] = lambda: fake_session

    async def _fake_enqueue(
        settings: Any,
        job_type: str,
        payload: dict[str, Any],
        tenant_id: uuid.UUID | None,
        *,
        delay_seconds: int | None = None,
    ) -> uuid.UUID:
        enqueue_calls.append({"job_type": job_type, "payload": payload, "tenant_id": tenant_id})
        return uuid.uuid4()

    monkeypatch.setattr(voz_avanzada, "enqueue", _fake_enqueue)
    return app


@pytest.fixture
async def client(_mounted_app) -> AsyncIterator[AsyncClient]:
    # Sombrea a propósito el fixture `client` de `conftest.py` (mismo criterio
    # que `test_voz_avanzada.py`): ese vive sobre `app` sin `voz_avanzada.router`.
    transport = ASGITransport(app=_mounted_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _headers_con_podcast(**kw: Any) -> dict[str, str]:
    kw.setdefault("user_id", uuid.uuid4())
    kw.setdefault("tenant_id", uuid.uuid4())
    return auth_headers(plan_key=PLAN_CON_TOOLS_PODCAST, **kw)


def _podcast_row(**overrides: Any) -> dict[str, Any]:
    now = datetime.now(UTC)
    base = {
        "id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "titulo": "Mi Podcast",
        "guion": [{"texto": "Hola a todos", "voz": None}],
        "status": "pending",
        "file_id": None,
        "error": None,
        "created_at": now,
        "updated_at": now,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# POST /v1/voz/podcasts
# ---------------------------------------------------------------------------


async def test_crear_podcast_requires_authentication(client) -> None:
    response = await client.post(
        "/v1/voz/podcasts", json={"titulo": "X", "guion": [{"texto": "hola"}]}
    )
    assert response.status_code == 401


async def test_crear_podcast_titulo_vacio_returns_400(
    client, fake_session: FakeSession
) -> None:
    response = await client.post(
        "/v1/voz/podcasts",
        json={"titulo": "   ", "guion": [{"texto": "hola"}]},
        headers=_headers_con_podcast(),
    )
    assert response.status_code == 400
    assert "titulo" in response.json()["detail"].lower()
    assert fake_session.llamadas == []


async def test_crear_podcast_guion_vacio_returns_400_sin_tocar_sesion(
    client, fake_session: FakeSession
) -> None:
    response = await client.post(
        "/v1/voz/podcasts",
        json={"titulo": "X", "guion": []},
        headers=_headers_con_podcast(),
    )
    assert response.status_code == 400
    assert fake_session.llamadas == []


async def test_crear_podcast_segmento_sin_texto_returns_400(
    client, fake_session: FakeSession
) -> None:
    response = await client.post(
        "/v1/voz/podcasts",
        json={"titulo": "X", "guion": [{"texto": "   "}]},
        headers=_headers_con_podcast(),
    )
    assert response.status_code == 400
    assert fake_session.llamadas == []


async def test_crear_podcast_happy_path_returns_201_inserta_y_encola(
    client, fake_session: FakeSession, enqueue_calls: list[dict[str, Any]]
) -> None:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    headers = _headers_con_podcast(tenant_id=tenant_id, user_id=user_id)
    fila_insertada = _podcast_row(
        tenant_id=tenant_id,
        user_id=user_id,
        titulo="Mi Primer Podcast",
        guion=[{"texto": "Hola a todos", "voz": "voz-1"}, {"texto": "Y bienvenidos", "voz": None}],
    )
    fake_session.respuestas = [[fila_insertada]]

    response = await client.post(
        "/v1/voz/podcasts",
        json={
            "titulo": "  Mi Primer Podcast  ",
            "guion": [
                {"texto": "Hola a todos", "voz": "voz-1"},
                {"texto": "Y bienvenidos"},
            ],
        },
        headers=headers,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "pending"
    assert body["guion"] == [
        {"texto": "Hola a todos", "voz": "voz-1"},
        {"texto": "Y bienvenidos", "voz": None},
    ]

    sql_insert, params = fake_session.llamadas[0]
    assert "INSERT INTO podcasts" in sql_insert
    assert params["titulo"] == "Mi Primer Podcast"  # se recortó el whitespace
    assert params["tenant_id"] == str(tenant_id)
    assert params["user_id"] == str(user_id)
    guion_enviado = json.loads(params["guion"])
    assert guion_enviado == [
        {"texto": "Hola a todos", "voz": "voz-1"},
        {"texto": "Y bienvenidos", "voz": None},
    ]

    assert len(enqueue_calls) == 1
    assert enqueue_calls[0]["job_type"] == "generate_podcast"
    assert enqueue_calls[0]["payload"] == {"podcast_id": str(fila_insertada["id"])}
    assert enqueue_calls[0]["tenant_id"] == tenant_id


# ---------------------------------------------------------------------------
# GET /v1/voz/podcasts
# ---------------------------------------------------------------------------


async def test_listar_podcasts_requires_authentication(client) -> None:
    response = await client.get("/v1/voz/podcasts")
    assert response.status_code == 401


async def test_listar_podcasts_vacio(client, fake_session: FakeSession) -> None:
    fake_session.respuestas = [[]]
    response = await client.get("/v1/voz/podcasts", headers=_headers_con_podcast())
    assert response.status_code == 200
    assert response.json() == []


async def test_listar_podcasts_returns_filas_del_tenant_orden_desc(
    client, fake_session: FakeSession
) -> None:
    tenant_id = uuid.uuid4()
    headers = _headers_con_podcast(tenant_id=tenant_id)
    fila_reciente = _podcast_row(tenant_id=tenant_id, titulo="Reciente", status="done")
    fila_vieja = _podcast_row(tenant_id=tenant_id, titulo="Vieja", status="error")
    fake_session.respuestas = [[fila_reciente, fila_vieja]]

    response = await client.get("/v1/voz/podcasts", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert [p["titulo"] for p in body] == ["Reciente", "Vieja"]
    sql, params = fake_session.llamadas[0]
    assert "SELECT * FROM podcasts" in sql
    assert "ORDER BY created_at DESC" in sql
    assert params["tenant_id"] == str(tenant_id)


async def test_listar_podcasts_decodifica_guion_como_string_json(
    client, fake_session: FakeSession
) -> None:
    """`guion` puede volver como texto JSON crudo según el driver (ver
    docstring de `_guion_desde_jsonb` en el router) — no solo como lista ya
    decodificada."""
    fila = _podcast_row(guion=json.dumps([{"texto": "hola", "voz": None}]))
    fake_session.respuestas = [[fila]]

    response = await client.get("/v1/voz/podcasts", headers=_headers_con_podcast())

    assert response.status_code == 200
    assert response.json()[0]["guion"] == [{"texto": "hola", "voz": None}]


# ---------------------------------------------------------------------------
# GET /v1/voz/podcasts/{podcast_id}
# ---------------------------------------------------------------------------


async def test_obtener_podcast_requires_authentication(client) -> None:
    response = await client.get(f"/v1/voz/podcasts/{uuid.uuid4()}")
    assert response.status_code == 401


async def test_obtener_podcast_not_found_returns_404(client, fake_session: FakeSession) -> None:
    fake_session.respuestas = [[]]
    response = await client.get(f"/v1/voz/podcasts/{uuid.uuid4()}", headers=_headers_con_podcast())
    assert response.status_code == 404


async def test_obtener_podcast_done_incluye_file_id(client, fake_session: FakeSession) -> None:
    tenant_id, podcast_id, file_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    headers = _headers_con_podcast(tenant_id=tenant_id)
    fila = _podcast_row(
        id=podcast_id, tenant_id=tenant_id, status="done", file_id=file_id, error=None
    )
    fake_session.respuestas = [[fila]]

    response = await client.get(f"/v1/voz/podcasts/{podcast_id}", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "done"
    assert body["file_id"] == str(file_id)

    sql, params = fake_session.llamadas[0]
    assert "SELECT * FROM podcasts" in sql
    assert params["id"] == str(podcast_id)
    assert params["tenant_id"] == str(tenant_id)


async def test_obtener_podcast_error_incluye_mensaje(client, fake_session: FakeSession) -> None:
    podcast_id = uuid.uuid4()
    fila = _podcast_row(id=podcast_id, status="error", error="ffmpeg no está instalado")
    fake_session.respuestas = [[fila]]

    response = await client.get(f"/v1/voz/podcasts/{podcast_id}", headers=_headers_con_podcast())

    assert response.status_code == 200
    assert response.json()["error"] == "ffmpeg no está instalado"
