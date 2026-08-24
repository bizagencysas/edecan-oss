import json
import uuid
from datetime import date

from conftest import auth_headers

from edecan_api.security import hash_password


async def test_exportacion_de_usuario_devuelve_formato_y_no_secrets(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")

    response = await client.get("/v1/privacy/export", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["format"] == "edecan-user-export.v1"
    assert "connector_credentials" in body["excluded_operational_secrets"]
    assert "embeddings" not in body


async def test_centro_de_privacidad_expone_borrado_reforzado(client) -> None:
    response = await client.get(
        "/v1/privacy",
        headers=auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic"),
    )

    assert response.status_code == 200
    controls = response.json()["controls"]
    assert controls["export"]["available"] is True
    assert controls["erase_memory"]["available"] is True
    assert controls["erase_account"]["available"] is True
    assert controls["erase_account"]["requires_reauthentication"] is True


async def test_exportacion_usa_nombres_reales_de_transacciones(client, fake_repo) -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    await fake_repo.create_transaction(
        tenant_id=tenant_id,
        user_id=user_id,
        fields={
            "fecha": date(2026, 8, 20),
            "monto": 12.5,
            "moneda": "USD",
            "categoria": "comida",
            "descripcion": "café",
            "cuenta": "personal",
        },
    )
    response = await client.get(
        "/v1/privacy/export",
        headers=auth_headers(user_id=user_id, tenant_id=tenant_id, plan_key="hosted_basic"),
    )

    assert response.status_code == 200
    assert response.json()["transactions"][0]["monto"] == 12.5


async def test_exportacion_incluye_persona_sin_credenciales(client, fake_repo) -> None:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    await fake_repo.upsert_persona(
        tenant_id=tenant_id,
        user_id=user_id,
        fields={"nombre_asistente": "Luna", "idioma": "es", "memoria_activada": True},
    )

    response = await client.get(
        "/v1/privacy/export",
        headers=auth_headers(user_id=user_id, tenant_id=tenant_id, plan_key="hosted_basic"),
    )

    assert response.status_code == 200
    assert response.json()["persona"]["nombre_asistente"] == "Luna"
    assert "token" not in json.dumps(response.json()["persona"]).lower()


async def test_exportacion_incluye_operacion_sin_args_ni_tokens(client, fake_repo) -> None:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    mission_id, automation_id, device_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    fake_repo.missions[mission_id] = {
        "id": mission_id,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "objetivo": "Revisar ventas",
        "status": "done",
        "resultado": "Listo",
        "plan": [{"agente": "research", "instruccion": "Investiga"}],
    }
    fake_repo.automations[automation_id] = {
        "id": automation_id,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "nombre": "Brief diario",
        "descripcion": "Resumen",
        "enabled": True,
        "accion": {"webhook_secret": "secret-no-exportar"},
    }
    fake_repo.devices[device_id] = {
        "id": device_id,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "nombre": "iPhone",
        "plataforma": "ios",
        "kind": "mobile",
        "status": "active",
        "push_token": "push-token-no-exportar",
    }
    connection_id = uuid.uuid4()
    fake_repo.connector_accounts[connection_id] = {
        "id": connection_id,
        "tenant_id": tenant_id,
        "connector_key": "google",
        "display_name": "Google Calendar",
        "status": "active",
        "scopes": ["calendar.read"],
        "external_account_id": "external-no-exportar",
    }

    response = await client.get(
        "/v1/privacy/export",
        headers=auth_headers(user_id=user_id, tenant_id=tenant_id, plan_key="hosted_basic"),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["missions"][0]["objetivo"] == "Revisar ventas"
    assert body["automations"][0]["nombre"] == "Brief diario"
    assert body["devices"][0]["nombre"] == "iPhone"
    assert body["connections"][0]["display_name"] == "Google Calendar"
    assert "secret-no-exportar" not in response.text
    assert "push-token-no-exportar" not in response.text
    assert "external-no-exportar" not in response.text


async def test_exportacion_incluye_feedback_sin_detalle_crudo(client, fake_repo) -> None:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    raw_detail = "No quiero que guardes este detalle privado"
    await fake_repo.add_audit_log(
        tenant_id=tenant_id,
        actor_user_id=user_id,
        action="quality.feedback_received",
        target="session",
        meta={
            "kind": "correction",
            "category": "accuracy",
            "feedback_ref": "abc123",
            "detail_ref": "hash-only",
            "detail": raw_detail,
        },
    )

    response = await client.get(
        "/v1/privacy/export",
        headers=auth_headers(user_id=user_id, tenant_id=tenant_id, plan_key="hosted_basic"),
    )

    assert response.status_code == 200
    assert response.json()["feedback"][0]["feedback_ref"] == "abc123"
    assert raw_detail not in response.text


async def _cuenta_para_borrado(fake_repo):
    tenant = await fake_repo.create_tenant(
        name="Privacidad", slug="privacidad-test", plan_key="hosted_basic"
    )
    user = await fake_repo.create_user(
        email="borrado@example.com", password_hash=hash_password("clave-segura-123")
    )
    await fake_repo.create_membership(user_id=user["id"], tenant_id=tenant["id"], role="owner")
    return tenant, user


async def test_borrado_de_cuenta_exige_confirmacion_y_reautenticacion(
    client, fake_repo, fake_redis
) -> None:
    tenant, user = await _cuenta_para_borrado(fake_repo)
    headers = auth_headers(user_id=user["id"], tenant_id=tenant["id"])

    incorrecta = await client.request(
        "DELETE",
        "/v1/privacy/account",
        headers=headers,
        json={"password": "clave-segura-123", "confirmation": "borrar"},
    )
    assert incorrecta.status_code == 400
    assert user["id"] in fake_repo.users

    borrado = await client.request(
        "DELETE",
        "/v1/privacy/account",
        headers=headers,
        json={"password": "clave-segura-123", "confirmation": "ELIMINAR MI CUENTA"},
    )
    assert borrado.status_code == 200
    assert borrado.json()["deleted"] is True
    assert user["id"] not in fake_repo.users
    assert await fake_redis.get(f"auth:deleted-user:{user['id']}") == "1"
    # La identidad borrada no puede reutilizar el access token que ya tenía.
    posterior = await client.get("/v1/privacy", headers=headers)
    assert posterior.status_code == 401


async def test_borrado_de_cuenta_falla_cerrado_si_queda_archivo_externo(
    client, fake_repo, fake_redis
) -> None:
    tenant, user = await _cuenta_para_borrado(fake_repo)
    file_id = uuid.uuid4()
    fake_repo.files[file_id] = {
        "id": file_id,
        "tenant_id": tenant["id"],
        "user_id": user["id"],
        "s3_key": "tenants/private/file",
        "filename": "private.txt",
    }
    response = await client.request(
        "DELETE",
        "/v1/privacy/account",
        headers=auth_headers(user_id=user["id"], tenant_id=tenant["id"]),
        json={"password": "clave-segura-123", "confirmation": "ELIMINAR MI CUENTA"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "account_deletion_blocked"
    assert "files_s3" in response.json()["detail"]["blockers"]
    assert user["id"] in fake_repo.users
    assert await fake_redis.get(f"auth:deleted-user:{user['id']}") is None


async def test_preflight_de_borrado_muestra_blockers_sin_mutar(client, fake_repo) -> None:
    tenant, user = await _cuenta_para_borrado(fake_repo)
    file_id = uuid.uuid4()
    fake_repo.files[file_id] = {
        "id": file_id,
        "tenant_id": tenant["id"],
        "user_id": user["id"],
        "s3_key": "private/key",
    }

    response = await client.get(
        "/v1/privacy/account/preflight",
        headers=auth_headers(user_id=user["id"], tenant_id=tenant["id"]),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["format"] == "edecan-account-deletion-preflight.v1"
    assert body["ready"] is False
    assert body["blockers"][0]["code"] == "files_s3"
    assert body["mutated"] is False
    assert user["id"] in fake_repo.users


async def test_preflight_miembro_no_bloquea_recursos_compartidos(client, fake_repo) -> None:
    tenant, owner = await _cuenta_para_borrado(fake_repo)
    member = await fake_repo.create_user(
        email="miembro@example.com", password_hash=hash_password("clave-miembro")
    )
    await fake_repo.create_membership(user_id=member["id"], tenant_id=tenant["id"], role="member")
    connector_id = uuid.uuid4()
    fake_repo.connector_accounts[connector_id] = {
        "id": connector_id,
        "tenant_id": tenant["id"],
        "status": "active",
    }

    response = await client.get(
        "/v1/privacy/account/preflight",
        headers=auth_headers(user_id=member["id"], tenant_id=tenant["id"]),
    )

    assert response.status_code == 200
    assert response.json()["ready"] is True
    assert response.json()["blockers"] == []
    assert owner["id"] in fake_repo.users


async def test_preflight_owner_con_otros_miembros_exige_transferencia(client, fake_repo) -> None:
    tenant, owner = await _cuenta_para_borrado(fake_repo)
    member = await fake_repo.create_user(
        email="otro-miembro@example.com", password_hash=hash_password("clave-miembro")
    )
    await fake_repo.create_membership(user_id=member["id"], tenant_id=tenant["id"], role="member")

    response = await client.get(
        "/v1/privacy/account/preflight",
        headers=auth_headers(user_id=owner["id"], tenant_id=tenant["id"]),
    )

    assert response.status_code == 200
    assert response.json()["ready"] is False
    assert response.json()["blockers"][0]["code"] == "tenant_ownership"
