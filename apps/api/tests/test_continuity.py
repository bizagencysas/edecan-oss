from __future__ import annotations


class FakeContinuityState:
    def snapshot(self) -> dict[str, object]:
        return {
            "configured": True,
            "enabled": True,
            "connected": True,
            "last_heartbeat_at": "2026-07-27T12:00:00+00:00",
            "last_claim_at": None,
            "last_job_at": None,
            "last_error": None,
            "completed_jobs": 4,
            "failed_jobs": 1,
        }


async def test_continuity_status_is_authenticated_and_never_exposes_configuration(
    app,
    client,
    test_settings,
) -> None:
    test_settings.EDECAN_LOCAL_MODE = True
    local = await client.post(
        "/v1/auth/local",
        headers={"X-Edecan-Desktop-Capability": "test-desktop-capability"},
    )
    assert local.status_code == 200
    app.state.edge_continuity_state = FakeContinuityState()

    anonymous = await client.get("/v1/continuity/status")
    response = await client.get(
        "/v1/continuity/status",
        headers={"Authorization": f"Bearer {local.json()['access_token']}"},
    )

    assert anonymous.status_code == 401
    assert response.status_code == 200
    assert response.json()["connected"] is True
    assert response.json()["completed_jobs"] == 4
    serialized = response.text.lower()
    assert "base_url" not in serialized
    assert "installation_id" not in serialized
    assert "shared_secret" not in serialized


async def test_offline_turn_reconciles_once_into_original_conversation(
    client,
    fake_repo,
    test_settings,
) -> None:
    test_settings.EDECAN_LOCAL_MODE = True
    local = await client.post(
        "/v1/auth/local",
        headers={"X-Edecan-Desktop-Capability": "test-desktop-capability"},
    )
    headers = {"Authorization": f"Bearer {local.json()['access_token']}"}
    conversation = await client.post(
        "/v1/conversations",
        json={"channel": "api"},
        headers=headers,
    )
    conversation_id = conversation.json()["id"]
    payload = {
        "event_id": "d63bacb4-7157-44f8-a731-a172cab09d2e",
        "conversation_id": conversation_id,
        "user_message": "Continúa este trabajo",
        "assistant_message": "Lo dejé preparado para cuando vuelva tu computadora.",
    }

    first = await client.post("/v1/continuity/sync", json=payload, headers=headers)
    second = await client.post("/v1/continuity/sync", json=payload, headers=headers)

    assert first.status_code == 200
    assert first.json()["deduplicated"] is False
    assert second.status_code == 200
    assert second.json()["deduplicated"] is True
    stored = fake_repo.messages[next(iter(fake_repo.messages))]
    assert [row["role"] for row in stored] == ["user", "assistant"]
    assert all(
        row["content"]["continuity_event_id"] == payload["event_id"] for row in stored
    )
