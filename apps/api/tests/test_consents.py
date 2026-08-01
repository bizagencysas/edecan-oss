"""Consentimiento telefónico OSS: autenticado, aislado por tenant y auditado."""

from __future__ import annotations

import uuid

from conftest import auth_headers


def _payload(**overrides):
    payload = {"phone_e164": "+525512345678", "kind": "sms", "source": "formulario_web"}
    payload.update(overrides)
    return payload


async def test_create_consent_requires_authentication(client) -> None:
    response = await client.post("/v1/consents", json=_payload())
    assert response.status_code == 401


async def test_create_consent_success_persists_and_audits(client, fake_repo) -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    response = await client.post(
        "/v1/consents",
        json=_payload(phone_e164=" +525512345678 ", source=" formulario_web "),
        headers=auth_headers(user_id=user_id, tenant_id=tenant_id),
    )

    assert response.status_code == 201
    assert response.json() == {
        "phone_e164": "+525512345678",
        "kind": "sms",
        "source": "formulario_web",
    }
    assert await fake_repo.has_phone_consent(
        tenant_id=tenant_id, phone_e164="+525512345678", kind="sms"
    )
    assert fake_repo.audit_log[-1]["action"] == "phone.consent_granted"
    assert fake_repo.audit_log[-1]["actor_user_id"] == user_id


async def test_create_consent_rejects_invalid_kind(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4())
    response = await client.post("/v1/consents", json=_payload(kind="whatsapp"), headers=headers)
    assert response.status_code == 422


async def test_create_consent_rejects_blank_source_without_writing(client, fake_repo) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4())
    response = await client.post("/v1/consents", json=_payload(source="   "), headers=headers)
    assert response.status_code == 400
    assert fake_repo.phone_consents == []


async def test_create_consent_rejects_invalid_phone_format(client, fake_repo) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4())
    response = await client.post(
        "/v1/consents", json=_payload(phone_e164="5512345678"), headers=headers
    )
    assert response.status_code == 400
    assert fake_repo.phone_consents == []
