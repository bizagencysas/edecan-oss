from __future__ import annotations

import json

from conftest import auth_headers
from edecan_schemas import default_mobile_server_config


def test_default_mobile_config_un_solo_tab_bots_sin_teams() -> None:
    config = default_mobile_server_config()
    tab_ids = [tab.id for tab in config.tabs]
    assert tab_ids == ["assistant", "equipo", "activity", "ide", "profile"]
    assert "teams" not in tab_ids
    bots_tab = config.tabs[1]
    assert bots_tab.id == "equipo"
    assert bots_tab.title == "Bots"
    assert bots_tab.system_icon == "sparkles"


async def test_mobile_config_serves_default_contract(client, fake_repo) -> None:
    tenant = await fake_repo.create_tenant(name="Test", slug="test", plan_key="hosted_basic")
    user = await fake_repo.create_user(email="user@example.com", password_hash="hash")
    await fake_repo.create_membership(tenant_id=tenant["id"], user_id=user["id"], role="owner")

    response = await client.get(
        "/v1/mobile/config?platform=ios&build=20",
        headers=auth_headers(user_id=user["id"], tenant_id=tenant["id"]),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == 1
    assert payload["platform"] == "ios"
    assert [tab["id"] for tab in payload["tabs"]] == [
        "assistant",
        "equipo",
        "activity",
        "ide",
        "profile",
    ]
    bots_tab = payload["tabs"][1]
    assert bots_tab["id"] == "equipo"
    assert bots_tab["title"] == "Bots"
    assert bots_tab["system_icon"] == "sparkles"
    assert payload["flags"]["server_driven_ui"] is True


async def test_mobile_config_uses_valid_env_override(client, fake_repo, test_settings) -> None:
    tenant = await fake_repo.create_tenant(name="Test", slug="test", plan_key="hosted_basic")
    user = await fake_repo.create_user(email="user@example.com", password_hash="hash")
    await fake_repo.create_membership(tenant_id=tenant["id"], user_id=user["id"], role="owner")
    test_settings.MOBILE_SERVER_CONFIG_JSON = json.dumps(
        {
            "schema_version": 1,
            "config_version": 7,
            "updated_at": "2026-07-28T00:00:00Z",
            "platform": "all",
            "tabs": [
                {
                    "id": "assistant",
                    "title": "Chat",
                    "system_icon": "bubble.left.fill",
                    "enabled": True,
                    "order": 0,
                }
            ],
            "copy": {"assistant_title": "Edecán privado"},
            "flags": {"server_driven_ui": True, "ide_remote": False},
            "quick_actions": [],
        }
    )

    response = await client.get(
        "/v1/mobile/config?platform=android",
        headers=auth_headers(user_id=user["id"], tenant_id=tenant["id"]),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["config_version"] == 7
    assert payload["platform"] == "android"
    assert payload["copy"]["assistant_title"] == "Edecán privado"
    assert payload["flags"]["ide_remote"] is False
