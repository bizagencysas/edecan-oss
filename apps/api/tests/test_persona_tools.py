from __future__ import annotations

import uuid
from typing import Any

from edecan_core.tools import ToolContext

from edecan_api.persona_tools import (
    ActivarEstiloRomanticoTool,
    ConfigurarEstiloRelacionTool,
    SalirEstiloRomanticoTool,
    conversation_persona_tools,
)


def _ctx(writes: list[dict[str, Any]], *, with_updater: bool = True) -> ToolContext:
    async def updater(*, fields: dict[str, Any]) -> dict[str, Any]:
        writes.append(fields)
        return fields

    return ToolContext(
        tenant_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        session=None,
        settings=None,
        llm=None,
        vault=None,
        extras={"persona_updater": updater} if with_updater else {},
    )


def test_conversation_persona_tools_son_locales_y_salida_no_es_peligrosa() -> None:
    tools = conversation_persona_tools()
    assert [tool.name for tool in tools] == [
        "configurar_estilo_relacion",
        "activar_estilo_romantico",
        "salir_estilo_romantico",
    ]
    assert tools[0].dangerous is False
    assert tools[1].dangerous is True
    assert tools[2].dangerous is False


async def test_configurar_no_romantico_limpia_consentimiento_previo() -> None:
    writes: list[dict[str, Any]] = []
    result = await ConfigurarEstiloRelacionTool().run(_ctx(writes), {"estilo": "amigo"})

    assert result.data == {"updated": True, "estilo_relacion": "amigo"}
    assert writes == [
        {
            "estilo_relacion": "amigo",
            "adulto_confirmado": False,
            "consentimiento_romantico": False,
        }
    ]


async def test_romantico_no_escribe_sin_ambas_confirmaciones() -> None:
    writes: list[dict[str, Any]] = []
    tool = ActivarEstiloRomanticoTool()

    missing_age = await tool.run(
        _ctx(writes),
        {"adulto_confirmado": False, "consentimiento_explicito": True},
    )
    missing_consent = await tool.run(
        _ctx(writes),
        {"adulto_confirmado": True, "consentimiento_explicito": False},
    )

    assert missing_age.data == {"updated": False}
    assert missing_consent.data == {"updated": False}
    assert writes == []


async def test_romantico_persiste_solo_con_adulto_y_consentimiento() -> None:
    writes: list[dict[str, Any]] = []
    result = await ActivarEstiloRomanticoTool().run(
        _ctx(writes),
        {"adulto_confirmado": True, "consentimiento_explicito": True},
    )

    assert result.data == {"updated": True, "estilo_relacion": "romantico"}
    assert writes == [
        {
            "estilo_relacion": "romantico",
            "adulto_confirmado": True,
            "consentimiento_romantico": True,
        }
    ]


async def test_salir_es_inmediato_y_borra_las_confirmaciones() -> None:
    writes: list[dict[str, Any]] = []
    result = await SalirEstiloRomanticoTool().run(_ctx(writes), {})

    assert "terminó" in result.content
    assert writes == [
        {
            "estilo_relacion": "profesional",
            "adulto_confirmado": False,
            "consentimiento_romantico": False,
        }
    ]


async def test_tools_fallan_amablemente_fuera_del_chat_personal() -> None:
    result = await SalirEstiloRomanticoTool().run(_ctx([], with_updater=False), {})
    assert result.data == {"updated": False}
    assert "Ajustes" in result.content
