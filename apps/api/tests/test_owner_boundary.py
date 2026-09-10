from __future__ import annotations

import uuid
from types import SimpleNamespace

from edecan_core.tools import ToolContext
from edecan_toolkit.codigo_local import AccederCodigoLocalTool

from edecan_api.main import (
    _REQUEST_TENANT_ID,
    _REQUEST_USER_ID,
    InstallationToolRegistry,
)
from edecan_api.persona_tools import ConectarMCPServerTool, CrearHerramientaTool

REGISTER_PAYLOAD = {
    "email": "local-owner@example.com",
    "password": "local-owner-password",
    "tenant_name": "Local owner",
}


async def _create_owner(fake_repo, *, email: str) -> tuple[uuid.UUID, uuid.UUID]:
    user = await fake_repo.create_user(email=email, password_hash="not-used")
    tenant = await fake_repo.create_tenant(
        name=email,
        slug=f"tenant-{uuid.uuid4().hex}",
        plan_key="free_selfhost",
    )
    await fake_repo.create_membership(
        user_id=user["id"], tenant_id=tenant["id"], role="owner"
    )
    return user["id"], tenant["id"]


def _plugin_context(fake_repo, *, user_id: uuid.UUID, tenant_id: uuid.UUID) -> ToolContext:
    return ToolContext(
        tenant_id=tenant_id,
        user_id=user_id,
        session=fake_repo,
        settings=SimpleNamespace(EDECAN_LOCAL_MODE=True, LOCAL_OWNER_USER_ID=None),
        llm=None,
        vault=None,
        extras={},
    )


async def test_local_register_without_registration_code_is_forbidden(
    client, fake_repo, test_settings, monkeypatch
) -> None:
    test_settings.EDECAN_LOCAL_MODE = True
    monkeypatch.delenv("LOCAL_REGISTRATION_CODE", raising=False)

    response = await client.post("/v1/auth/register", json=REGISTER_PAYLOAD)

    assert response.status_code == 403
    assert fake_repo.users == {}
    assert fake_repo.tenants == {}


async def test_local_registration_code_is_consumed_by_persisted_owner(
    client, fake_repo, test_settings, monkeypatch
) -> None:
    test_settings.EDECAN_LOCAL_MODE = True
    monkeypatch.setenv("LOCAL_REGISTRATION_CODE", "one-time-install-code")
    headers = {"X-Edecan-Registration-Code": "one-time-install-code"}

    first = await client.post("/v1/auth/register", json=REGISTER_PAYLOAD, headers=headers)
    second = await client.post(
        "/v1/auth/register",
        json={
            "email": "second-owner@example.com",
            "password": "second-owner-password",
            "tenant_name": "Second owner",
        },
        headers=headers,
    )

    assert first.status_code == 201
    assert second.status_code == 403
    assert fake_repo.local_owner is not None
    assert len(fake_repo.users) == 1
    assert len(fake_repo.tenants) == 1


async def test_non_owner_cannot_create_plugin(
    fake_repo, monkeypatch, tmp_path
) -> None:
    monkeypatch.delenv("LOCAL_OWNER_USER_ID", raising=False)
    owner_user_id, owner_tenant_id = await _create_owner(
        fake_repo, email="installation-owner@example.com"
    )
    await fake_repo.set_local_owner(
        user_id=owner_user_id, tenant_id=owner_tenant_id
    )
    other_user_id, other_tenant_id = await _create_owner(
        fake_repo, email="other-tenant@example.com"
    )
    monkeypatch.setenv("EDECAN_PLUGINS_DIR", str(tmp_path / "plugins"))

    result = await CrearHerramientaTool().run(
        _plugin_context(
            fake_repo,
            user_id=other_user_id,
            tenant_id=other_tenant_id,
        ),
        {"nombre": "forbidden_tool", "codigo": "raise RuntimeError('must not run')"},
    )

    assert result.is_error is True
    assert result.data == {"authorized": False}
    assert not (tmp_path / "plugins").exists()


async def test_non_owner_cannot_connect_mcp(fake_repo, monkeypatch) -> None:
    monkeypatch.delenv("LOCAL_OWNER_USER_ID", raising=False)
    owner_user_id, owner_tenant_id = await _create_owner(
        fake_repo, email="mcp-installation-owner@example.com"
    )
    await fake_repo.set_local_owner(
        user_id=owner_user_id, tenant_id=owner_tenant_id
    )
    other_user_id, other_tenant_id = await _create_owner(
        fake_repo, email="mcp-other-tenant@example.com"
    )

    result = await ConectarMCPServerTool().run(
        _plugin_context(
            fake_repo,
            user_id=other_user_id,
            tenant_id=other_tenant_id,
        ),
        {
            "nombre": "must-not-connect",
            "transporte": "http",
            "url": "https://example.com",
        },
    )

    assert result.is_error is True
    assert result.data == {"authorized": False}


async def test_non_owner_cannot_access_local_code(tmp_path) -> None:
    owner_user_id = uuid.uuid4()
    ctx = ToolContext(
        tenant_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        session=None,
        settings=SimpleNamespace(
            EDECAN_LOCAL_MODE=True,
            EDECAN_LOCAL_REPO_PATH=str(tmp_path),
            LOCAL_OWNER_USER_ID=str(owner_user_id),
        ),
        llm=None,
        vault=None,
        extras={},
    )

    result = await AccederCodigoLocalTool().run(
        ctx, {"accion": "listar_directorio"}
    )

    assert result.is_error is True
    assert result.data == {"authorized": False}


async def test_plugin_manifest_is_not_executed_and_is_written_under_tenant(
    fake_repo, monkeypatch, tmp_path
) -> None:
    monkeypatch.delenv("LOCAL_OWNER_USER_ID", raising=False)
    owner_user_id, owner_tenant_id = await _create_owner(
        fake_repo, email="plugin-owner@example.com"
    )
    await fake_repo.set_local_owner(
        user_id=owner_user_id, tenant_id=owner_tenant_id
    )
    plugins_root = tmp_path / "plugins"
    marker = tmp_path / "manifest-executed"
    monkeypatch.setenv("EDECAN_PLUGINS_DIR", str(plugins_root))
    code = f'''
from pathlib import Path
from edecan_core.tools import Tool, ToolResult

Path({str(marker)!r}).write_text("executed")

class TenantOnlyTool(Tool):
    name = "tenant_only"
    description = "Tenant-only test tool."
    input_schema = {{"type": "object", "properties": {{}}}}

    async def run(self, ctx, args):
        return ToolResult(content="ok")

def get_all_tools():
    return [TenantOnlyTool()]
'''

    result = await CrearHerramientaTool().run(
        _plugin_context(
            fake_repo,
            user_id=owner_user_id,
            tenant_id=owner_tenant_id,
        ),
        {"nombre": "tenant_only", "codigo": code},
    )

    assert result.data and result.data["creada"] is True
    assert not marker.exists()
    assert (plugins_root / str(owner_tenant_id) / "tenant_only.py").is_file()
    assert not (plugins_root / "tenant_only.py").exists()


def test_plugin_registry_does_not_cross_tenant_directories(tmp_path) -> None:
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    user_a = uuid.uuid4()
    user_b = uuid.uuid4()
    root = tmp_path / "plugins"
    (root / str(tenant_a)).mkdir(parents=True)
    (root / str(tenant_b)).mkdir(parents=True)
    template = '''
from edecan_core.tools import Tool, ToolResult

class {class_name}(Tool):
    name = "{tool_name}"
    description = "isolated"
    input_schema = {{"type": "object", "properties": {{}}}}

    async def run(self, ctx, args):
        return ToolResult(content="ok")

def get_all_tools():
    return [{class_name}()]
'''
    (root / str(tenant_a) / "tenant_a_tool.py").write_text(
        template.format(class_name="TenantATool", tool_name="tenant_a_tool"),
        encoding="utf-8",
    )
    (root / str(tenant_b) / "tenant_b_tool.py").write_text(
        template.format(class_name="TenantBTool", tool_name="tenant_b_tool"),
        encoding="utf-8",
    )
    registry = InstallationToolRegistry(local_mode=False)

    user_token = _REQUEST_USER_ID.set(user_a)
    tenant_token = _REQUEST_TENANT_ID.set(tenant_a)
    try:
        registry.load_plugin_dir(root)
        assert registry.get("tenant_a_tool") is not None
        assert registry.get("tenant_b_tool") is None
    finally:
        _REQUEST_TENANT_ID.reset(tenant_token)
        _REQUEST_USER_ID.reset(user_token)

    user_token = _REQUEST_USER_ID.set(user_b)
    tenant_token = _REQUEST_TENANT_ID.set(tenant_b)
    try:
        registry.load_plugin_dir(root)
        assert registry.get("tenant_b_tool") is not None
        assert registry.get("tenant_a_tool") is None
    finally:
        _REQUEST_TENANT_ID.reset(tenant_token)
        _REQUEST_USER_ID.reset(user_token)


def test_local_complete_catalog_is_only_visible_to_installation_owner() -> None:
    owner_user_id = uuid.uuid4()
    owner_tenant_id = uuid.uuid4()
    other_user_id = uuid.uuid4()
    other_tenant_id = uuid.uuid4()
    registry = InstallationToolRegistry(local_mode=True)
    registry.remember_local_owner(
        user_id=owner_user_id,
        tenant_id=owner_tenant_id,
    )
    registry.load_entry_points()

    user_token = _REQUEST_USER_ID.set(other_user_id)
    tenant_token = _REQUEST_TENANT_ID.set(other_tenant_id)
    try:
        assert registry.all() == []
        assert registry.specs({}) == []
    finally:
        _REQUEST_TENANT_ID.reset(tenant_token)
        _REQUEST_USER_ID.reset(user_token)

    user_token = _REQUEST_USER_ID.set(owner_user_id)
    tenant_token = _REQUEST_TENANT_ID.set(owner_tenant_id)
    try:
        assert registry.all()
    finally:
        _REQUEST_TENANT_ID.reset(tenant_token)
        _REQUEST_USER_ID.reset(user_token)
