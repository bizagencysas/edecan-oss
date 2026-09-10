"""Contrato compartido del harness de Edecán Bots."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from edecan_core.bot_harness import (
    AUTONOMY_LEVELS,
    BOT_GATED_TOOL_NAMES,
    BOT_SANDBOX_TOOL_NAMES,
    BOT_SEND_TOOL_NAMES,
    MCP_OPERATION_READ,
    MCP_OPERATION_SEND,
    MCP_OPERATION_WRITE,
    MCPToolGrant,
    autonomy_allowed_operations,
    autonomy_allows_operation,
    bot_preapproved_tool_calls,
    is_bot_gated_tool,
    mcp_grant_token,
    mcp_preapproved_tokens,
    mcp_tool_definition_version,
    mcp_tool_local_operation,
    parse_mcp_grants,
    tool_local_operation,
)


def test_bot_preapproved_incluye_sandbox_y_no_mcp_por_prefijo() -> None:
    """BOTS-06: las tools MCP ya NO se pre-aprueban por el prefijo `mcp_`."""
    approved = bot_preapproved_tool_calls(
        companion_present=False,
        local_mode=False,
        mcp_tool_names=["mcp_github_search", "otra"],
    )
    assert "acceder_codigo_local" in approved
    assert "avisar_avance" in approved
    assert "mcp_github_search" not in approved
    assert "otra" not in approved
    assert "conectar_mcp" not in approved


def test_bot_preapproved_mac_tools_solo_con_companion_o_local() -> None:
    sin_mac = bot_preapproved_tool_calls(companion_present=False, local_mode=False)
    assert "usar_computadora" not in sin_mac
    assert "delegar_al_ide" not in sin_mac

    con_companion = bot_preapproved_tool_calls(companion_present=True, local_mode=False)
    assert "usar_computadora" in con_companion
    assert "delegar_al_ide" in con_companion

    local = bot_preapproved_tool_calls(companion_present=False, local_mode=True)
    assert "delegar_al_ide" in local


def test_gated_tools_no_estan_en_sandbox() -> None:
    assert BOT_GATED_TOOL_NAMES.isdisjoint(BOT_SANDBOX_TOOL_NAMES)
    assert is_bot_gated_tool("conectar_mcp")
    assert not is_bot_gated_tool("acceder_codigo_local")
    assert not is_bot_gated_tool("gestionar_automatizacion")


def test_sandbox_incluye_browser_code_y_avisar_avance() -> None:
    """Browser/computer + cloud-code fluyen sin tarjeta; narración va aparte."""
    assert "avisar_avance" in BOT_SANDBOX_TOOL_NAMES
    assert "navegar_web" in BOT_SANDBOX_TOOL_NAMES
    assert "navegar_web_interactivo" in BOT_SANDBOX_TOOL_NAMES
    assert "acceder_codigo_local" in BOT_SANDBOX_TOOL_NAMES
    assert "delegar_al_ide" in BOT_SANDBOX_TOOL_NAMES


# ---------------------------------------------------------------------------
# BOTS-06 — grants explícitos de tools MCP (versión de definición)
# ---------------------------------------------------------------------------


def _mcp_tool(
    name: str,
    *,
    server_name: str = "acme",
    remote_name: str = "buscar",
    description: str = "",
    schema: dict[str, Any] | None = None,
) -> Any:
    """Doble sintético de una tool MCP adaptada: `name` (nombre local
    `mcp_*`) y `definition_version` (fingerprint de su definición actual)."""
    schema = schema if schema is not None else {"type": "object", "properties": {}}
    version = mcp_tool_definition_version(
        name=remote_name,
        description=description,
        input_schema=schema,
        server_name=server_name,
    )
    return SimpleNamespace(name=name, definition_version=version, input_schema=schema)


def test_mcp_lectura_con_grant_valido_se_preaprueba() -> None:
    """Aceptación: lectura autorizada sin tarjeta de aprobación."""
    tool = _mcp_tool("mcp_acme_buscar")
    grant = MCPToolGrant(
        tool_name="mcp_acme_buscar",
        operation=MCP_OPERATION_READ,
        definition_version=tool.definition_version,
    )
    tokens = mcp_preapproved_tokens(tools=[tool], grants=[grant])
    assert tokens == {mcp_grant_token(
        tool_name="mcp_acme_buscar",
        operation=MCP_OPERATION_READ,
        definition_version=tool.definition_version,
    )}


def test_mcp_envio_o_borrado_sin_grant_no_se_preaprueba() -> None:
    """Aceptación: envío/borrado no autorizado queda bloqueado (sin token)."""
    enviar = _mcp_tool("mcp_acme_enviar", remote_name="enviar")
    borrar = _mcp_tool("mcp_acme_borrar", remote_name="borrar")
    assert mcp_preapproved_tokens(tools=[enviar, borrar], grants=[]) == set()


def test_mcp_grant_no_se_hereda_al_cambiar_definicion() -> None:
    """Aceptación: una ampliación de schema invalida el grant anterior."""
    schema_original = {"type": "object", "properties": {"q": {"type": "string"}}}
    schema_ampliado = {
        "type": "object",
        "properties": {
            "q": {"type": "string"},
            "op": {"enum": ["read", "delete"]},
        },
    }
    v_original = mcp_tool_definition_version(
        name="buscar", description="", input_schema=schema_original, server_name="acme"
    )
    v_ampliado = mcp_tool_definition_version(
        name="buscar", description="", input_schema=schema_ampliado, server_name="acme"
    )
    assert v_original != v_ampliado

    grant_viejo = MCPToolGrant(
        tool_name="mcp_acme_buscar",
        operation=MCP_OPERATION_READ,
        definition_version=v_original,
    )
    tool_actual = _mcp_tool("mcp_acme_buscar", schema=schema_ampliado)
    assert tool_actual.definition_version == v_ampliado

    # El grant atado a la versión vieja ya no aplica a la definición nueva.
    assert mcp_preapproved_tokens(tools=[tool_actual], grants=[grant_viejo]) == set()


def test_mcp_grant_con_operacion_desconocida_no_autoriza() -> None:
    """Fail-closed: una operación fuera de {read, write, send} no pre-aprueba."""
    tool = _mcp_tool("mcp_acme_buscar")
    grant_invalido = MCPToolGrant(
        tool_name="mcp_acme_buscar",
        operation="borrar_todo",
        definition_version=tool.definition_version,
    )
    assert mcp_preapproved_tokens(tools=[tool], grants=[grant_invalido]) == set()


def test_mcp_grant_version_vacia_no_autoriza() -> None:
    """Un grant sin versión de definición no autoriza nada (fail-closed)."""
    tool = _mcp_tool("mcp_acme_buscar")
    grant = MCPToolGrant(tool_name="mcp_acme_buscar", operation=MCP_OPERATION_SEND, definition_version="")
    assert mcp_preapproved_tokens(tools=[tool], grants=[grant]) == set()


def test_parse_mcp_grants_ignora_entradas_invalidas() -> None:
    """Solo entradas bien formadas se convierten en grants; el resto se descarta."""
    version = mcp_tool_definition_version(name="buscar", description="", input_schema={}, server_name="acme")
    raw = {
        "mcp_grants": [
            {"tool_name": "mcp_acme_buscar", "operation": "read", "definition_version": version},
            {"tool_name": "mcp_acme_enviar", "operation": "desconocida", "definition_version": version},
            {"tool_name": "", "operation": "read", "definition_version": version},
            "no-es-un-dict",
        ]
    }
    grants = parse_mcp_grants(raw)
    assert len(grants) == 1
    assert grants[0].tool_name == "mcp_acme_buscar"
    assert grants[0].operation == "read"


def test_parse_mcp_grants_tolera_json_string_y_none() -> None:
    import json

    version = mcp_tool_definition_version(name="buscar", description="", input_schema={}, server_name="acme")
    payload = {"mcp_grants": [
        {"tool_name": "mcp_acme_buscar", "operation": "read", "definition_version": version}
    ]}
    assert len(parse_mcp_grants(json.dumps(payload))) == 1
    assert parse_mcp_grants(None) == []
    assert parse_mcp_grants("no-json{") == []
    assert parse_mcp_grants({"sin_clave_mcp_grants": True}) == []


# ---------------------------------------------------------------------------
# H3 — el grant `operation` ahora tiene dientes (coincidencia con la
# clasificación LOCAL de la tool).
# ---------------------------------------------------------------------------


def test_h3_grant_read_no_desbloquea_tool_clasificada_send() -> None:
    """Un grant `read` NO pre-aprueba una tool MCP clasificada como `send`."""
    enviar = _mcp_tool("mcp_acme_enviar", remote_name="enviar")
    grant_read = MCPToolGrant(
        tool_name="mcp_acme_enviar",
        operation=MCP_OPERATION_READ,
        definition_version=enviar.definition_version,
    )
    assert mcp_preapproved_tokens(tools=[enviar], grants=[grant_read]) == set()


def test_h3_grant_read_no_desbloquea_tool_clasificada_delete() -> None:
    """Un grant `read` NO pre-aprueba una tool MCP clasificada como `write`/delete."""
    borrar = _mcp_tool("mcp_acme_borrar", remote_name="borrar")
    grant_read = MCPToolGrant(
        tool_name="mcp_acme_borrar",
        operation=MCP_OPERATION_READ,
        definition_version=borrar.definition_version,
    )
    assert mcp_preapproved_tokens(tools=[borrar], grants=[grant_read]) == set()


def test_h3_grant_operation_debe_coincidir_con_clasificacion_local() -> None:
    """Solo se emite token cuando `grant.operation` == clasificación local."""
    buscar = _mcp_tool("mcp_acme_buscar", remote_name="buscar")
    enviar = _mcp_tool("mcp_acme_enviar", remote_name="enviar")

    # read grant sobre tool read → token; read grant sobre tool send → sin token.
    grant_read = MCPToolGrant(
        tool_name="mcp_acme_buscar",
        operation=MCP_OPERATION_READ,
        definition_version=buscar.definition_version,
    )
    assert mcp_preapproved_tokens(tools=[buscar], grants=[grant_read]) == {
        mcp_grant_token(
            tool_name="mcp_acme_buscar",
            operation=MCP_OPERATION_READ,
            definition_version=buscar.definition_version,
        )
    }

    # send grant sobre tool send → token; pero el grant de `enviar` NO aplica a
    # `buscar` (tool distinta).
    grant_send = MCPToolGrant(
        tool_name="mcp_acme_enviar",
        operation=MCP_OPERATION_SEND,
        definition_version=enviar.definition_version,
    )
    assert mcp_preapproved_tokens(tools=[enviar], grants=[grant_send]) == {
        mcp_grant_token(
            tool_name="mcp_acme_enviar",
            operation=MCP_OPERATION_SEND,
            definition_version=enviar.definition_version,
        )
    }


def test_h3_tool_no_clasificable_no_se_preaprueba_por_grant() -> None:
    """Una tool MCP sin clasificación local NUNCA se pre-aprueba (fail-closed)."""
    opaca = _mcp_tool("mcp_acme_xyzzy", remote_name="xyzzy")
    grant_read = MCPToolGrant(
        tool_name="mcp_acme_xyzzy",
        operation=MCP_OPERATION_READ,
        definition_version=opaca.definition_version,
    )
    grant_write = MCPToolGrant(
        tool_name="mcp_acme_xyzzy",
        operation=MCP_OPERATION_WRITE,
        definition_version=opaca.definition_version,
    )
    assert mcp_preapproved_tokens(tools=[opaca], grants=[grant_read, grant_write]) == set()


def test_mcp_tool_local_operation_es_determinista_y_conservadora() -> None:
    assert mcp_tool_local_operation(name="mcp_acme_buscar") == MCP_OPERATION_READ
    assert mcp_tool_local_operation(name="mcp_acme_get_list") == MCP_OPERATION_READ
    assert mcp_tool_local_operation(name="mcp_acme_borrar") == MCP_OPERATION_WRITE
    assert mcp_tool_local_operation(name="mcp_acme_enviar") == MCP_OPERATION_SEND
    # Nombre mixto (read + delete) → conservador: write.
    assert mcp_tool_local_operation(name="mcp_acme_read_and_delete") == MCP_OPERATION_WRITE
    # Sin señal → None (fail-closed).
    assert mcp_tool_local_operation(name="mcp_acme_xyzzy") is None
    # La annotation remota NO se consulta: un schema con property de mutación
    # clasifica aunque el nombre sea opaco.
    assert mcp_tool_local_operation(
        name="mcp_acme_xyzzy",
        input_schema={"type": "object", "properties": {"delete_ids": {"type": "array"}}},
    ) == MCP_OPERATION_WRITE


def test_mcp_fetch_and_store_se_clasifica_write_no_read() -> None:
    """F8: `store` es verbo de escritura interna. Un nombre mixto
    (lectura `fetch` + escritura `store`) se clasifica conservadoramente como
    `write`, jamás como `read` — una tool que persiste no debe sobrevivir a
    un nivel de autonomía de solo lectura."""
    assert mcp_tool_local_operation(name="mcp_fetch_and_store") == MCP_OPERATION_WRITE
    # `persist` y `append` también son mutación local.
    assert mcp_tool_local_operation(name="mcp_persist_notes") == MCP_OPERATION_WRITE
    assert mcp_tool_local_operation(name="mcp_append_log") == MCP_OPERATION_WRITE


def test_tool_local_operation_nativa_legacy_sin_category() -> None:
    """Llamador legacy (sin `category`) conserva la clasificación histórica:
    gated → send, dangerous → write, resto → read. La lista SEND curada manda
    por encima (`enviar_correo`)."""
    assert tool_local_operation(name="enviar_correo") == MCP_OPERATION_SEND
    assert tool_local_operation(name="acceder_codigo_local", dangerous=True) == MCP_OPERATION_WRITE
    assert tool_local_operation(name="listar_mcp") == MCP_OPERATION_READ
    # MCP se clasifica por nombre, no por `dangerous`.
    assert tool_local_operation(name="mcp_acme_buscar", dangerous=True) == MCP_OPERATION_READ


def test_tool_local_operation_nativa_usa_category_como_senal_primaria() -> None:
    """C1: la `category` declarada es la señal primaria. read/vision → read;
    write → write; external_comm → send; desconocida → write (fail-closed)."""
    assert tool_local_operation(name="leer_archivo", category="read") == MCP_OPERATION_READ
    assert tool_local_operation(name="analizar_imagen", category="vision") == MCP_OPERATION_READ
    assert tool_local_operation(name="registrar_transaccion", category="write") == MCP_OPERATION_WRITE
    assert tool_local_operation(name="publicar_social", category="external_comm") == MCP_OPERATION_SEND
    # Fail-closed: categoría no read/vision/write/external_comm → write.
    assert tool_local_operation(name="crear_artefactos", category="creative") == MCP_OPERATION_WRITE
    assert tool_local_operation(name="probar_notificaciones_push", category="admin") == MCP_OPERATION_WRITE
    assert tool_local_operation(name="opaca", category="") == MCP_OPERATION_WRITE


def test_tool_local_operation_send_nativos_por_lista_curada() -> None:
    """C1: los envíos externos nativos son `send` por lista curada, por encima
    de la `category` (`enviar_mensaje` no declara category y es dangerous)."""
    for nombre in BOT_SEND_TOOL_NAMES:
        assert tool_local_operation(name=nombre, dangerous=True) == MCP_OPERATION_SEND
    assert BOT_SEND_TOOL_NAMES == {
        "enviar_mensaje",
        "enviar_mensaje_bot",
        "publicar_social",
        "enviar_correo",
        "enviar_mensaje_personal",
    }


def test_matriz_c1_escritura_no_esta_en_read_only() -> None:
    """C1: tools de escritura no peligrosas NO corren en read_only (antes
    clasificaban `read` por no ser `dangerous`)."""
    for nombre in ("registrar_transaccion", "crear_evento", "guardar_memoria"):
        operacion = tool_local_operation(name=nombre, category="write", dangerous=False)
        assert operacion == MCP_OPERATION_WRITE
        assert not autonomy_allows_operation("read_only", operacion)
        assert autonomy_allows_operation("draft", operacion)


def test_matriz_c1_enviar_mensaje_no_esta_en_draft() -> None:
    """C1: `enviar_mensaje` (envío real) es `send` → NO corre en `draft` (que
    promete "sin envío externo")."""
    operacion = tool_local_operation(name="enviar_mensaje", dangerous=True)
    assert operacion == MCP_OPERATION_SEND
    assert not autonomy_allows_operation("draft", operacion)
    assert not autonomy_allows_operation("read_only", operacion)


def test_matriz_c1_leer_archivo_si_esta_en_read_only() -> None:
    """C1: `leer_archivo` (category read) SÍ corre en read_only."""
    operacion = tool_local_operation(name="leer_archivo", category="read")
    assert operacion == MCP_OPERATION_READ
    assert autonomy_allows_operation("read_only", operacion)


def test_enviar_mensaje_bot_es_gated_y_send() -> None:
    """F1-ALTA (laundering): `enviar_mensaje_bot` manda mensajes inter-agente y
    encola trabajo del receptor — es un `send`, jamás lectura. Un bot read_only
    no debe poder delegar a un bot full."""
    assert "enviar_mensaje_bot" in BOT_GATED_TOOL_NAMES
    assert is_bot_gated_tool("enviar_mensaje_bot")
    assert tool_local_operation(name="enviar_mensaje_bot") == MCP_OPERATION_SEND
    # Sigue sin colisionar con la lista sandbox (no se pre-aprueba por prefijo).
    assert "enviar_mensaje_bot" not in BOT_SANDBOX_TOOL_NAMES
    assert not autonomy_allows_operation("read_only", MCP_OPERATION_SEND)
    assert not autonomy_allows_operation("draft", MCP_OPERATION_SEND)


# ---------------------------------------------------------------------------
# BOTS-02 — matriz de autonomía
# ---------------------------------------------------------------------------


def test_autonomy_matrix_restrictiva_y_full() -> None:
    assert autonomy_allowed_operations("full") is None
    assert autonomy_allowed_operations("draft") == {MCP_OPERATION_READ, MCP_OPERATION_WRITE}
    assert autonomy_allowed_operations("read_only") == {MCP_OPERATION_READ}
    # ask = disponibilidad sin restricción; el freno son las confirmaciones de
    # las tools peligrosas (dangerous/gated), no la matriz de disponibilidad.
    assert autonomy_allowed_operations("ask") is None
    # Desconocido → solo lectura (fail-closed).
    assert autonomy_allowed_operations("superadmin") == {MCP_OPERATION_READ}


def test_autonomy_allows_operation_por_nivel() -> None:
    assert autonomy_allows_operation("full", MCP_OPERATION_SEND)
    assert autonomy_allows_operation("full", None)  # full no restringe
    assert autonomy_allows_operation("draft", MCP_OPERATION_WRITE)
    assert not autonomy_allows_operation("draft", MCP_OPERATION_SEND)
    assert not autonomy_allows_operation("draft", None)  # no clasificable → rechazado
    assert autonomy_allows_operation("read_only", MCP_OPERATION_READ)
    assert not autonomy_allows_operation("read_only", MCP_OPERATION_WRITE)
    assert not autonomy_allows_operation("read_only", MCP_OPERATION_SEND)
    assert not autonomy_allows_operation("read_only", None)
    assert AUTONOMY_LEVELS == ("ask", "read_only", "draft", "full")
