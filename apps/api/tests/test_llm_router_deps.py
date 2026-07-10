"""Regresión (riesgo-legal-tos): `get_llm_router`/`load_tenant_llm_config`
(`apps/api/edecan_api/deps.py`) NUNCA deben degradar a la credencial LLM de
PLATAFORMA (`ANTHROPIC_API_KEY`/`.env`) cuando el tenant actual no conectó su
propio proveedor — deben cortar la request, mismo criterio que
`premium/edecan_premium/telephony.py::for_tenant` (`TwilioNotConnectedError`,
sin fallback a ninguna cuenta compartida). Ver `docs/credenciales.md` y
`DIRECCION_ACTUAL.md` "Modelo de credenciales: TODO lo trae el cliente,
siempre".

No usa el `client`/`app` de `conftest.py` (que sobreescribe `get_llm_router`
directo a `lambda: None` para no acoplar el resto de la suite HTTP a esta
lógica — ver `apps/api/tests/conftest.py`): llama a `get_llm_router`/
`load_tenant_llm_config` como funciones async normales, con `session=None`
(mismo truco que ya usa `apps/api/tests/conftest.py` al sobreescribir
`get_tenant_session`: `SqlRepo(None).list_connector_accounts` revienta con
`AttributeError`, que `load_tenant_llm_config` atrapa con su `except
Exception` amplio y trata como "tenant sin nada conectado" — no hace falta
una base de datos real para ejercitar este camino).
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException

from edecan_api import deps as edecan_deps
from edecan_api.config import Settings


def _current_user(tenant_id: uuid.UUID) -> edecan_deps.CurrentUser:
    tenant = edecan_deps.TenantCtx(tenant_id=tenant_id, plan_key="hosted_basic", flags={})
    return edecan_deps.CurrentUser(user_id=uuid.uuid4(), tenant=tenant)


async def test_load_tenant_llm_config_sin_sesion_devuelve_none() -> None:
    """`session=None` (tenant sin nada resoluble) -> `None`, nunca lanza."""
    settings = Settings(JWT_SECRET="x" * 32)
    resultado = await edecan_deps.load_tenant_llm_config(None, settings, uuid.uuid4())
    assert resultado is None


async def test_get_llm_router_sin_credencial_de_tenant_corta_con_400() -> None:
    """Aunque `Settings.ANTHROPIC_API_KEY` esté configurada (simula una
    plataforma hosted con su propia cuenta de Anthropic), un tenant sin
    `PUT /v1/credentials/llm` propio NUNCA debe recibir un `LLMRouter`
    construido con esa key compartida: `get_llm_router` debe cortar con
    `HTTPException(400)` en vez de degradar en silencio (hallazgo
    riesgo-legal-tos: bring-your-own no era real para LLM)."""
    settings = Settings(
        JWT_SECRET="x" * 32,
        ANTHROPIC_API_KEY="sk-ant-credencial-compartida-de-plataforma-NUNCA-SE-USA",
    )
    current_user = _current_user(uuid.uuid4())

    with pytest.raises(HTTPException) as exc_info:
        await edecan_deps.get_llm_router(
            current_user=current_user, session=None, settings=settings
        )

    assert exc_info.value.status_code == 400
    assert "conectado" in exc_info.value.detail.lower()
