"""Tests de `/v1/perfil` (WP-V2-13, ver el docstring de
`edecan_api/routers/perfil.py`).

`perfil.router` YA viene montado por `create_app()` (`perfil` está en
`edecan_api.main.V2_ROUTER_NAMES` desde WP-V2-01) — a diferencia de
`test_commerce_router.py`/`test_remote_router.py` (escritos cuando sus
routers TODAVÍA no estaban en esa lista), no hace falta incluir el router a
mano; solo hay que reemplazar `get_tenant_session` (el `app` base de
`conftest.py` lo deja en `lambda: None`) por un `FakeSession` local que
soporte `.execute()` — mismo patrón `FakeSession`/`FakeResult` que
`test_commerce_router.py` (duplicado a propósito, ARCHITECTURE.md §10.1).
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
from httpx import AsyncClient

import edecan_api.deps as edecan_deps
from edecan_api.routers import perfil as perfil_module


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
    respuestas: list[list[dict[str, Any]]] = field(default_factory=list)
    llamadas: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> FakeResult:
        self.llamadas.append((str(stmt), dict(params or {})))
        filas = self.respuestas.pop(0) if self.respuestas else []
        return FakeResult(filas)


@pytest.fixture
def fake_session() -> FakeSession:
    return FakeSession()


@pytest.fixture
async def client(app, fake_session: FakeSession) -> AsyncIterator[AsyncClient]:
    """Sombrea el `client` de `conftest.py`: agrega `get_tenant_session` ->
    `fake_session`. `perfil.router` ya viene montado (ver docstring del
    módulo), así que no hace falta `app.include_router(...)` a mano."""
    from httpx import ASGITransport

    app.dependency_overrides[edecan_deps.get_tenant_session] = lambda: fake_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _headers(**kw: Any) -> dict[str, str]:
    kw.setdefault("user_id", uuid.uuid4())
    kw.setdefault("tenant_id", uuid.uuid4())
    return auth_headers(**kw)


def _fila_perfil(
    *,
    tenant_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    resumen: str = "Prefieres respuestas breves.",
    datos: dict[str, list[str]] | None = None,
    version: int = 3,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "id": uuid.uuid4(),
        "tenant_id": tenant_id or uuid.uuid4(),
        "user_id": user_id or uuid.uuid4(),
        "resumen": resumen,
        "datos": datos
        or {
            "gustos": ["Le gusta el café"],
            "proyectos": [],
            "metas": [],
            "relaciones": [],
            "empresas": ["Trabaja en Acme"],
            "habitos": [],
        },
        "version": version,
        "created_at": now,
        "updated_at": now,
    }


# ---------------------------------------------------------------------------
# Autenticación
# ---------------------------------------------------------------------------


async def test_get_perfil_requiere_autenticacion(client: AsyncClient) -> None:
    response = await client.get("/v1/perfil")
    assert response.status_code == 401


async def test_put_perfil_requiere_autenticacion(client: AsyncClient) -> None:
    response = await client.put("/v1/perfil", json={"resumen": "x"})
    assert response.status_code == 401


async def test_delete_perfil_requiere_autenticacion(client: AsyncClient) -> None:
    response = await client.delete("/v1/perfil")
    assert response.status_code == 401


async def test_rebuild_requiere_autenticacion(client: AsyncClient) -> None:
    response = await client.post("/v1/perfil/rebuild")
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# GET — esqueleto vacío vs. fila real
# ---------------------------------------------------------------------------


async def test_get_perfil_sin_fila_devuelve_esqueleto_vacio(
    client: AsyncClient, fake_session: FakeSession
) -> None:
    fake_session.respuestas = [[]]  # SELECT -> sin fila

    response = await client.get("/v1/perfil", headers=_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["resumen"] == ""
    assert body["version"] == 0
    assert body["updated_at"] is None
    assert set(body["datos"].keys()) == {
        "gustos",
        "proyectos",
        "metas",
        "relaciones",
        "empresas",
        "habitos",
    }
    assert all(v == [] for v in body["datos"].values())


async def test_get_perfil_con_fila_devuelve_su_contenido(
    client: AsyncClient, fake_session: FakeSession
) -> None:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    fila = _fila_perfil(tenant_id=tenant_id, user_id=user_id)
    fake_session.respuestas = [[fila]]

    response = await client.get(
        "/v1/perfil", headers=_headers(tenant_id=tenant_id, user_id=user_id)
    )

    assert response.status_code == 200
    body = response.json()
    assert body["resumen"] == "Prefieres respuestas breves."
    assert body["version"] == 3
    assert body["datos"]["gustos"] == ["Le gusta el café"]
    assert body["datos"]["empresas"] == ["Trabaja en Acme"]

    sql, params = fake_session.llamadas[0]
    assert "SELECT * FROM user_profiles" in sql
    assert params["tenant_id"] == tenant_id
    assert params["user_id"] == user_id


async def test_get_perfil_decodifica_datos_jsonb_texto_crudo(
    client: AsyncClient, fake_session: FakeSession
) -> None:
    fila = _fila_perfil()
    fila["datos"] = json.dumps(fila["datos"])  # texto crudo, no dict
    fake_session.respuestas = [[fila]]

    response = await client.get("/v1/perfil", headers=_headers())

    assert response.status_code == 200
    assert response.json()["datos"]["gustos"] == ["Le gusta el café"]


# ---------------------------------------------------------------------------
# PUT — validación de shape/caps, patch parcial, version++
# ---------------------------------------------------------------------------


async def test_put_perfil_crea_version_1_cuando_no_habia_fila(
    client: AsyncClient, fake_session: FakeSession
) -> None:
    creada = _fila_perfil(resumen="Prefieres respuestas breves.", version=1)
    fake_session.respuestas = [[], [creada]]  # SELECT vacío + INSERT RETURNING

    response = await client.put(
        "/v1/perfil",
        json={"resumen": "Prefieres respuestas breves."},
        headers=_headers(),
    )

    assert response.status_code == 200
    assert response.json()["version"] == 1

    sql_insert, params = fake_session.llamadas[1]
    assert "INSERT INTO user_profiles" in sql_insert
    assert "ON CONFLICT (tenant_id, user_id) DO UPDATE" in sql_insert
    assert params["version"] == 1
    assert params["resumen"] == "Prefieres respuestas breves."


async def test_put_perfil_incrementa_version_sobre_fila_existente(
    client: AsyncClient, fake_session: FakeSession
) -> None:
    existente = _fila_perfil(version=3)
    actualizada = {**existente, "version": 4}
    fake_session.respuestas = [[existente], [actualizada]]

    response = await client.put(
        "/v1/perfil", json={"resumen": "Nuevo resumen."}, headers=_headers()
    )

    assert response.status_code == 200
    _, params = fake_session.llamadas[1]
    assert params["version"] == 4


async def test_put_perfil_patch_parcial_de_datos_no_toca_otras_categorias(
    client: AsyncClient, fake_session: FakeSession
) -> None:
    existente = _fila_perfil(
        datos={
            "gustos": ["Le gusta el café"],
            "proyectos": ["Proyecto X"],
            "metas": [],
            "relaciones": [],
            "empresas": [],
            "habitos": [],
        }
    )
    actualizada = dict(existente)
    fake_session.respuestas = [[existente], [actualizada]]

    response = await client.put(
        "/v1/perfil",
        json={"datos": {"gustos": ["Le gusta el té"]}},
        headers=_headers(),
    )

    assert response.status_code == 200
    _, params = fake_session.llamadas[1]
    datos_guardados = json.loads(params["datos"])
    assert datos_guardados["gustos"] == ["Le gusta el té"]
    assert datos_guardados["proyectos"] == ["Proyecto X"]  # intacto, no vino en el body


async def test_put_perfil_sin_body_conserva_todo_pero_incrementa_version(
    client: AsyncClient, fake_session: FakeSession
) -> None:
    existente = _fila_perfil(resumen="Original", version=1)
    actualizada = {**existente, "version": 2}
    fake_session.respuestas = [[existente], [actualizada]]

    response = await client.put("/v1/perfil", json={}, headers=_headers())

    assert response.status_code == 200
    _, params = fake_session.llamadas[1]
    assert params["resumen"] == "Original"
    assert params["version"] == 2


async def test_put_perfil_rechaza_resumen_demasiado_largo(client: AsyncClient) -> None:
    response = await client.put("/v1/perfil", json={"resumen": "x" * 501}, headers=_headers())
    assert response.status_code == 422


async def test_put_perfil_rechaza_lista_con_mas_de_20_items(client: AsyncClient) -> None:
    response = await client.put(
        "/v1/perfil",
        json={"datos": {"gustos": [f"gusto {i}" for i in range(21)]}},
        headers=_headers(),
    )
    assert response.status_code == 422


async def test_put_perfil_acepta_exactamente_20_items(
    client: AsyncClient, fake_session: FakeSession
) -> None:
    existente = _fila_perfil()
    fake_session.respuestas = [[existente], [existente]]
    response = await client.put(
        "/v1/perfil",
        json={"datos": {"gustos": [f"gusto {i}" for i in range(20)]}},
        headers=_headers(),
    )
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# DELETE — borra la fila y el espejo en memory_items
# ---------------------------------------------------------------------------


async def test_delete_perfil_borra_fila_y_espejo(
    client: AsyncClient, fake_session: FakeSession
) -> None:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    fake_session.respuestas = [[], []]

    response = await client.delete(
        "/v1/perfil", headers=_headers(tenant_id=tenant_id, user_id=user_id)
    )

    assert response.status_code == 204
    assert len(fake_session.llamadas) == 2
    sql_perfil, params_perfil = fake_session.llamadas[0]
    assert "DELETE FROM user_profiles" in sql_perfil
    assert params_perfil["tenant_id"] == tenant_id
    assert params_perfil["user_id"] == user_id

    sql_espejo, params_espejo = fake_session.llamadas[1]
    assert "DELETE FROM memory_items" in sql_espejo
    assert "source = 'perfil_vivo'" in sql_espejo
    assert params_espejo["tenant_id"] == tenant_id


# ---------------------------------------------------------------------------
# POST /rebuild — encola memory_consolidate, 202
# ---------------------------------------------------------------------------


async def test_rebuild_encola_memory_consolidate_y_responde_202(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    enqueue_calls: list[dict[str, Any]] = []

    async def fake_enqueue(settings, job_type, payload, tenant_id):
        enqueue_calls.append({"job_type": job_type, "payload": payload, "tenant_id": tenant_id})
        return uuid.uuid4()

    monkeypatch.setattr(perfil_module, "enqueue", fake_enqueue)

    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    response = await client.post(
        "/v1/perfil/rebuild", headers=_headers(tenant_id=tenant_id, user_id=user_id)
    )

    assert response.status_code == 202
    body = response.json()
    assert "job_id" in body
    assert "mensaje" in body

    assert len(enqueue_calls) == 1
    assert enqueue_calls[0]["job_type"] == "memory_consolidate"
    assert enqueue_calls[0]["payload"] == {"user_id": str(user_id)}
    assert enqueue_calls[0]["tenant_id"] == tenant_id
