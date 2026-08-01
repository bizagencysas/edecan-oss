from __future__ import annotations

from typing import Any

import pytest
from edecan_toolkit.notificaciones import ProbarNotificacionesPushTool


@pytest.mark.asyncio
async def test_prueba_push_usa_dispatcher_scoped(make_ctx):
    llamadas = 0

    async def dispatch() -> dict[str, Any]:
        nonlocal llamadas
        llamadas += 1
        return {"queued": True, "event_id": "event-1", "job_id": "job-1"}

    tool = ProbarNotificacionesPushTool()
    result = await tool.run(make_ctx(extras={"push_test_dispatcher": dispatch}), {})

    assert llamadas == 1
    assert result.data == {
        "queued": True,
        "event_id": "event-1",
        "job_id": "job-1",
    }
    assert "Envié una notificación push de prueba" in result.content


@pytest.mark.asyncio
async def test_prueba_push_falla_cerrado_sin_dispatcher(make_ctx):
    result = await ProbarNotificacionesPushTool().run(make_ctx(), {})

    assert result.data == {"queued": False}
    assert "no está disponible" in result.content
