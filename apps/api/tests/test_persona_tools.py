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
        "conectar_mcp",
        "listar_mcp",
        "desconectar_mcp",
        "crear_herramienta",
        "reiniciar_servicio",
    ]
    assert tools[0].dangerous is False
    assert tools[1].dangerous is True
    assert tools[2].dangerous is False
    # F1-ALTA: `crear_herramienta` escribe un plugin Python PERMANENTE a disco;
    # debe ser `dangerous` para que la matriz de autonomía la clasifique `write`
    # y la descarte en `read_only`/`ask`.
    assert tools[6].name == "crear_herramienta"
    assert tools[6].dangerous is True


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


async def test_conectar_mcp_permitido_fuera_de_modo_local() -> None:
    from types import SimpleNamespace

    from edecan_api.persona_tools import is_local_installation_owner

    ctx = ToolContext(
        tenant_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        session=None,
        settings=SimpleNamespace(EDECAN_LOCAL_MODE=False),
        llm=None,
        vault=None,
        extras={},
    )
    assert await is_local_installation_owner(ctx) is True


async def test_conectar_mcp_denegado_si_no_es_dueno_en_modo_local() -> None:
    from types import SimpleNamespace

    from edecan_api.persona_tools import is_local_installation_owner

    owner_id = uuid.uuid4()
    ctx = ToolContext(
        tenant_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        session=None,
        settings=SimpleNamespace(EDECAN_LOCAL_MODE=True, LOCAL_OWNER_USER_ID=str(owner_id)),
        llm=None,
        vault=None,
        extras={},
    )
    assert await is_local_installation_owner(ctx) is False


def _ctx_plugin(tmp_path) -> ToolContext:
    return ToolContext(
        tenant_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        session=None,
        settings=None,
        llm=None,
        vault=None,
        extras={},
    )


async def test_crear_herramienta_escribe_y_valida_codigo_valido(
    monkeypatch, tmp_path
) -> None:
    from edecan_api.persona_tools import CrearHerramientaTool

    monkeypatch.setenv("EDECAN_PLUGINS_DIR", str(tmp_path / "plugins"))
    tool = CrearHerramientaTool()
    codigo = '''
from edecan_core.tools import Tool, ToolResult

class BuscarPreciosTool(Tool):
    name = "buscar_precios"
    description = "Busca precios."

    async def run(self, ctx, args):
        return ToolResult(content="ok")


def get_all_tools():
    return [BuscarPreciosTool()]
'''
    resultado = await tool.run(_ctx_plugin(tmp_path), {"nombre": "buscar_precios", "codigo": codigo})
    assert resultado.data and resultado.data["creada"] is True
    archivo = tmp_path / "plugins" / "buscar_precios.py"
    assert archivo.is_file()
    assert "BuscarPreciosTool" in archivo.read_text()


async def test_crear_herramienta_rechaza_codigo_roto_sin_escribir(
    monkeypatch, tmp_path
) -> None:
    from edecan_api.persona_tools import CrearHerramientaTool

    monkeypatch.setenv("EDECAN_PLUGINS_DIR", str(tmp_path / "plugins"))
    tool = CrearHerramientaTool()

    roto = await tool.run(
        _ctx_plugin(tmp_path), {"nombre": "rota", "codigo": "def roto(:  # sintaxis inválida"}
    )
    assert "compila" in (roto.content or "").lower()

    sin_contrato = await tool.run(
        _ctx_plugin(tmp_path), {"nombre": "sin_contrato", "codigo": "x = 1"}
    )
    assert "get_all_tools" in (sin_contrato.content or "")

    nombre_distinto = await tool.run(
        _ctx_plugin(tmp_path),
        {
            "nombre": "otro_nombre",
            "codigo": (
                "from edecan_core.tools import Tool\n"
                "class T(Tool):\n    name = 'nombre_distinto'\n"
                "    async def run(self, ctx, args): pass\n"
                "def get_all_tools():\n    return [T()]\n"
            ),
        },
    )
    assert "debe llamarse" in (nombre_distinto.content or "")

    directorio = tmp_path / "plugins"
    if directorio.is_dir():
        assert list(directorio.glob("*.py")) == []


async def test_reiniciar_servicio_escribe_flag_y_encola_wake(
    monkeypatch, tmp_path
) -> None:
    from edecan_api.persona_tools import ReiniciarServicioTool

    monkeypatch.setattr(
        "edecan_api.persona_tools._RESTART_FLAG", str(tmp_path / "restart-requested")
    )
    encolados: list[tuple] = []

    async def fake_enqueue(settings, job_type, payload, tid):
        encolados.append((job_type, payload, tid))
        return uuid.uuid4()

    monkeypatch.setattr("edecan_core.queue.enqueue", fake_enqueue)

    tool = ReiniciarServicioTool()
    ctx = _ctx([])
    resultado = await tool.run(
        ctx, {"motivo": "instalar X", "resumen": "estaba construyendo Y; sigue Z"}
    )
    assert resultado.data and resultado.data["reinicio_programado"] is True
    assert (tmp_path / "restart-requested").is_file()
    assert len(encolados) == 1
    job_type, payload, tid = encolados[0]
    assert job_type == "run_companion_turn"
    assert payload["restart_pending"] is True
    assert "estaba construyendo Y" in payload["instruction"]
    assert tid == ctx.tenant_id


async def test_reiniciar_servicio_sin_resumen_no_escribe_nada(tmp_path, monkeypatch) -> None:
    from edecan_api.persona_tools import ReiniciarServicioTool

    monkeypatch.setattr(
        "edecan_api.persona_tools._RESTART_FLAG", str(tmp_path / "restart-requested")
    )
    tool = ReiniciarServicioTool()
    resultado = await tool.run(_ctx([]), {"motivo": "x", "resumen": ""})
    assert "resumen" in (resultado.content or "").lower()
    assert not (tmp_path / "restart-requested").exists()
