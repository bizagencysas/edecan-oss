"""`POST /v1/consents` (ARCHITECTURE.md §10.10, §10.12) — único invocador de
`edecan_premium.compliance.grant_consent` (ver el docstring de
`edecan_api.routers.consents`).

Este router solo se monta si `edecan_premium` está instalado
(`edecan_api.main`, mismo guard que `twilio_router`) — `pytest.importorskip`
salta todo el archivo, en vez de fallar, en un entorno parcial donde `premium/`
no se sincronizó (p. ej. `cd apps/api && pytest` sin `uv sync` a nivel de todo
el workspace, ver `apps/api/tests/_stub_siblings.py`); `make test`/`uv run
pytest` desde la raíz (el flujo oficial, `CONTRIBUTING.md`) sí lo trae, porque
`premium` es miembro del workspace uv (`pyproject.toml` raíz).

`conftest.app` deja `get_tenant_session` apuntando a `None` (ningún otro
router lo necesita como sesión real: usan `Repo`/`get_repo` con `FakeRepo`, o
`get_vault` sobreescrito directo en sus propios tests) — aquí sí hace falta un
doble que entienda el SQL de `compliance.grant_consent`, así que cada test se
lo asigna con `app.dependency_overrides[...]`, igual que `test_connectors.py`
hace con `get_vault`. `_FakeSession`/`_FakeResult` son el mismo doble mínimo
que `premium/tests/test_compliance.py` (duplicado a propósito: los tests de
`apps/api` no importan paquetes hermanos, ARCHITECTURE.md §10.1).
"""

from __future__ import annotations

import uuid

import pytest

pytest.importorskip("edecan_premium")

from conftest import auth_headers  # noqa: E402

import edecan_api.deps as edecan_deps  # noqa: E402


class _FakeResult:
    def __init__(self, row=None):
        self._row = row

    def first(self):
        return self._row


class _FakeSession:
    def __init__(self) -> None:
        self.executed: list[tuple[str, dict]] = []

    async def execute(self, clause, params=None):
        self.executed.append((str(clause), params or {}))
        return _FakeResult(None)

    async def flush(self) -> None:
        pass


@pytest.fixture
def fake_session() -> _FakeSession:
    return _FakeSession()


@pytest.fixture(autouse=True)
def _wire_fake_session(app, fake_session: _FakeSession):
    app.dependency_overrides[edecan_deps.get_tenant_session] = lambda: fake_session


def _payload(**overrides):
    payload = {"phone_e164": "+525512345678", "kind": "sms", "source": "formulario_web"}
    payload.update(overrides)
    return payload


async def test_create_consent_requires_authentication(client) -> None:
    response = await client.post("/v1/consents", json=_payload())
    assert response.status_code == 401


async def test_create_consent_success_inserts_row_and_audits(client, fake_session) -> None:
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_pro")

    response = await client.post(
        "/v1/consents",
        json=_payload(phone_e164=" +525512345678 ", source=" formulario_web "),
        headers=headers,
    )

    assert response.status_code == 201
    assert response.json() == {
        "phone_e164": "+525512345678",
        "kind": "sms",
        "source": "formulario_web",
    }

    statements = [sql for sql, _ in fake_session.executed]
    assert any("INSERT INTO consents" in sql for sql in statements)
    assert any("INSERT INTO audit_log" in sql for sql in statements)

    _, consent_params = next(p for p in fake_session.executed if "INSERT INTO consents" in p[0])
    assert consent_params["tenant_id"] == str(tenant_id)
    assert consent_params["phone_e164"] == "+525512345678"
    assert consent_params["kind"] == "sms"
    assert consent_params["source"] == "formulario_web"


async def test_create_consent_rejects_invalid_kind(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_pro")
    response = await client.post(
        "/v1/consents", json=_payload(kind="whatsapp"), headers=headers
    )
    assert response.status_code == 422  # Literal["sms", "voice"] lo rechaza antes del handler


async def test_create_consent_rejects_blank_source_without_writing(
    client, fake_session
) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_pro")
    response = await client.post(
        "/v1/consents", json=_payload(source="   "), headers=headers
    )
    assert response.status_code == 400
    assert fake_session.executed == []  # grant_consent no toca la sesión si `source` es inválido


async def test_create_consent_rejects_invalid_phone_format(client, fake_session) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_pro")
    response = await client.post(
        "/v1/consents",
        json=_payload(phone_e164="5512345678"),  # falta el "+"
        headers=headers,
    )
    assert response.status_code == 400
    assert fake_session.executed == []  # grant_consent no toca la sesión si el teléfono es inválido
