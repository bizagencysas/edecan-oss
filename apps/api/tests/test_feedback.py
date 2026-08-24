from __future__ import annotations

import uuid

from conftest import auth_headers


async def test_feedback_guarda_referencia_hash_sin_texto_crudo(client, fake_repo) -> None:
    detail = "La respuesta reveló mi información privada"
    user_id, tenant_id, conversation_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    fake_repo.conversations[conversation_id] = {
        "id": conversation_id,
        "tenant_id": tenant_id,
        "user_id": user_id,
    }
    response = await client.post(
        "/v1/feedback",
        headers=auth_headers(user_id=user_id, tenant_id=tenant_id),
        json={
            "kind": "correction",
            "category": "accuracy",
            "detail": detail,
            "conversation_id": str(conversation_id),
        },
    )

    assert response.status_code == 202
    assert response.json()["accepted"] is True
    audit = fake_repo.audit_log[-1]
    assert audit["action"] == "quality.feedback_received"
    assert detail not in repr(audit)
    assert "detail_ref" in audit["meta"]


async def test_feedback_rechaza_tipo_desconocido(client) -> None:
    response = await client.post(
        "/v1/feedback",
        headers=auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4()),
        json={"kind": "inventado"},
    )
    assert response.status_code == 422


async def test_feedback_no_cruza_conversaciones_de_otro_tenant(client, fake_repo) -> None:
    response = await client.post(
        "/v1/feedback",
        headers=auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4()),
        json={"kind": "thumb_down", "conversation_id": str(uuid.uuid4())},
    )

    assert response.status_code == 404
    assert fake_repo.audit_log == []
