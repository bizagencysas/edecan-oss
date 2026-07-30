"""Criterio de `edecan-api-usage-desglose`.

`GET /v1/usage` solo sabe decir el total del mes por tipo. Para ver en qué día
se disparó el consumo hay que entrar a la base a mano. Este criterio ejercita
el endpoint nuevo `GET /v1/usage/desglose` contra la app real, con los mismos
dobles en memoria que usa la suite de `apps/api` (sin Postgres, sin red).

Falla hoy: la ruta no existe (404).
"""

from __future__ import annotations

import asyncio
import logging
import sys
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

logging.disable(logging.CRITICAL)

_RAIZ = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(_RAIZ / "apps/api/tests"))

import _stub_siblings  # noqa: E402,F401  (efecto secundario: puebla sys.path/sys.modules)
import httpx  # noqa: E402
from api_fakes import FakeRedis, FakeRepo  # noqa: E402
from edecan_api import deps as edecan_deps  # noqa: E402
from edecan_api.config import Settings, get_settings  # noqa: E402
from edecan_api.main import create_app  # noqa: E402
from edecan_api.security import create_access_token  # noqa: E402

_SECRETO = "test-jwt-secret-solo-para-tests-32-bytes-o-mas"
_INICIO_MES = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _app(repo: FakeRepo):
    settings = Settings(
        ENV="dev",
        JWT_SECRET=_SECRETO,
        WEB_BASE_URL="http://localhost:3000",
        PUBLIC_BASE_URL="http://localhost:8000",
        LOCAL_DESKTOP_CAPABILITY="test-desktop-capability",
    )
    application = create_app()
    redis = FakeRedis()
    application.dependency_overrides[get_settings] = lambda: settings
    for dependencia in (
        edecan_deps.get_platform_repo,
        edecan_deps.get_repo,
        edecan_deps.get_streaming_repo,
    ):
        application.dependency_overrides[dependencia] = lambda: repo
    application.dependency_overrides[edecan_deps.get_redis] = lambda: redis
    for dependencia in (
        edecan_deps.get_tenant_session,
        edecan_deps.get_vault,
        edecan_deps.get_platform_vault,
        edecan_deps.get_streaming_vault,
        edecan_deps.get_llm_router,
    ):
        application.dependency_overrides[dependencia] = lambda: None

    @asynccontextmanager
    async def _tx(_tenant_id: uuid.UUID):
        yield repo

    application.state.phone_repo_transaction_factory = _tx
    return application


def _evento(tenant_id: uuid.UUID, kind: str, cantidad: float, cuando: datetime) -> dict[str, Any]:
    return {
        "id": uuid.uuid4(),
        "tenant_id": tenant_id,
        "kind": kind,
        "quantity": cantidad,
        "meta": {},
        "created_at": cuando,
    }


async def _pedir() -> tuple[int, dict[str, Any], int]:
    repo = FakeRepo()
    tenant = uuid.uuid4()
    otro = uuid.uuid4()
    dia_1 = _INICIO_MES + timedelta(hours=9)
    dia_3 = _INICIO_MES + timedelta(days=2, hours=4)
    mes_pasado = _INICIO_MES - timedelta(days=2)
    repo.usage_events.extend(
        [
            _evento(tenant, "messages", 2.0, dia_1),
            _evento(tenant, "messages", 3.0, dia_1),
            _evento(tenant, "llm_tokens", 120.0, dia_3),
            _evento(tenant, "messages", 99.0, mes_pasado),
            _evento(otro, "messages", 7.0, dia_1),
        ]
    )
    token = create_access_token(
        user_id=uuid.uuid4(), tenant_id=tenant, plan_key="hosted_basic", secret=_SECRETO
    )
    transporte = httpx.ASGITransport(app=_app(repo))
    async with httpx.AsyncClient(transport=transporte, base_url="http://test") as cliente:
        sin_auth = await cliente.get("/v1/usage/desglose")
        respuesta = await cliente.get(
            "/v1/usage/desglose", headers={"Authorization": f"Bearer {token}"}
        )
    cuerpo = (
        respuesta.json()
        if respuesta.headers.get("content-type", "").startswith("application/json")
        else {}
    )
    return respuesta.status_code, cuerpo, sin_auth.status_code


def main() -> int:
    estado, cuerpo, sin_auth = asyncio.run(_pedir())
    if sin_auth != 401:
        print(f"sin token el endpoint devolvió {sin_auth}, esperado 401")
        return 1
    if estado != 200:
        print(f"el endpoint devolvió {estado}: {cuerpo}")
        return 1

    if cuerpo.get("plan_key") != "hosted_basic":
        print(f"plan_key inesperado: {cuerpo.get('plan_key')!r}")
        return 1
    if cuerpo.get("period_start") != _INICIO_MES.date().isoformat():
        print(f"period_start inesperado: {cuerpo.get('period_start')!r}")
        return 1

    por_dia = cuerpo.get("por_dia")
    if not isinstance(por_dia, list):
        print(f"por_dia no es una lista: {por_dia!r}")
        return 1
    fechas = [d.get("fecha") for d in por_dia]
    if fechas != sorted(fechas):
        print(f"los días no vienen en orden ascendente: {fechas}")
        return 1

    esperado = {
        (_INICIO_MES + timedelta(hours=9)).date().isoformat(): {"messages": 5.0},
        (_INICIO_MES + timedelta(days=2, hours=4)).date().isoformat(): {"llm_tokens": 120.0},
    }
    obtenido = {d.get("fecha"): d.get("kinds") for d in por_dia}
    if obtenido != esperado:
        print(f"desglose obtenido {obtenido}, esperado {esperado}")
        return 1

    print("ok: GET /v1/usage/desglose agrupa por día, respeta el mes y aísla el tenant")
    return 0


if __name__ == "__main__":
    sys.exit(main())
