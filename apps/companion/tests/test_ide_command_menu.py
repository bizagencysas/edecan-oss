"""Cableado del menú de comandos "/" del IDE dentro de ``ide_runtime.py``.

No repite lo que ya prueban ``test_ide_comandos.py``/``test_ide_contexto.py``/
``test_ide_modos.py``/``test_ide_sesion_extras.py``/``test_ide_acciones_codigo.py``
(el comportamiento puro de cada módulo de capacidad): este archivo verifica
que ``IDERuntime._ejecutar_comando`` / ``_despachar_comando`` de verdad
conecta cada comando resuelto con la llamada real correspondiente, a través
de ``execute_ide_action`` -- el mismo camino que usa el resto del IDE.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from edecan_companion.config import CompanionConfig
from edecan_companion.ide_runtime import execute_ide_action


async def _approve(_action: str, _params: dict[str, Any], _config: CompanionConfig) -> bool:
    return True


async def _call(config: CompanionConfig, action: str, params: dict[str, Any]) -> dict[str, Any]:
    result = await execute_ide_action(action, params, config, _approve)
    assert result["ok"], result
    return result["result"]


async def _command(
    config: CompanionConfig, text: str, **extra: Any
) -> dict[str, Any]:
    return await _call(config, "ide_command_execute", {"text": text, **extra})


async def _authorize(config: CompanionConfig, path: Path) -> dict[str, Any]:
    result = await _call(
        config, "ide_workspace_authorize", {"path": str(path), "name": "Proyecto"}
    )
    return result["workspace"]


@pytest.fixture
def workspace_path(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    return root


@pytest.mark.asyncio
async def test_command_list_incluye_help_y_viene_del_registro(
    companion_config: CompanionConfig,
) -> None:
    result = await _call(companion_config, "ide_command_list", {})
    nombres = {fila["nombre"] for fila in result["comandos"]}
    assert "help" in nombres
    assert "rewind" in nombres
    assert "/help" in result["ayuda"]


@pytest.mark.asyncio
async def test_help_genera_texto_desde_el_registro(companion_config: CompanionConfig) -> None:
    result = await _command(companion_config, "/help")
    assert result["ok"] is True
    assert "/rewind" in result["mensaje"]


@pytest.mark.asyncio
async def test_comando_desconocido_trae_sugerencia(companion_config: CompanionConfig) -> None:
    result = await _command(companion_config, "/hepl")
    assert result["ok"] is False
    assert result["sugerencia"] == "/help"


@pytest.mark.asyncio
async def test_comando_destructivo_exige_confirmacion(companion_config: CompanionConfig) -> None:
    result = await _command(companion_config, "/rewind ultimo")
    assert result["ok"] is False
    assert result["requiere_confirmacion"] is True
    assert result["destructivo"] is True


@pytest.mark.asyncio
async def test_rewind_ejecuta_rollback_real_del_ultimo_checkpoint(
    companion_config: CompanionConfig, workspace_path: Path
) -> None:
    workspace = await _authorize(companion_config, workspace_path)
    target = workspace_path / "cambio.py"
    target.write_text("estado original\n", encoding="utf-8")

    from edecan_companion.ide_runtime import _runtime_for

    runtime = _runtime_for(companion_config)
    checkpoint = runtime.sessions.checkpoints.create(workspace["id"], label="turno de prueba")
    runtime.sessions.checkpoints.track(checkpoint["id"], "cambio.py")
    target.write_text("cambio del agente\n", encoding="utf-8")
    runtime.sessions.checkpoints.seal(checkpoint["id"])

    result = await _command(
        companion_config,
        "/rewind ultimo",
        workspace_id=workspace["id"],
        confirmed=True,
    )

    assert result["ok"] is True
    assert result["data"]["restored"] == ["cambio.py"]
    assert target.read_text(encoding="utf-8") == "estado original\n"


@pytest.mark.asyncio
async def test_clear_crea_conversacion_nueva(companion_config: CompanionConfig) -> None:
    result = await _command(companion_config, "/clear Charla nueva", confirmed=True)
    assert result["ok"] is True
    assert result["data"]["conversation"]["title"] == "Charla nueva"
    assert result["nueva_conversacion_id"]


@pytest.mark.asyncio
async def test_rename_sin_conversacion_activa_falla_con_sugerencia_de_uso(
    companion_config: CompanionConfig,
) -> None:
    result = await _command(companion_config, "/rename Otro título")
    assert result["ok"] is False
    assert "conversación activa" in result["error"]


@pytest.mark.asyncio
async def test_rename_con_conversacion_activa(companion_config: CompanionConfig) -> None:
    creada = await _command(companion_config, "/clear", confirmed=True)
    conversation_id = creada["nueva_conversacion_id"]
    result = await _command(
        companion_config, "/rename Nuevo título", conversation_id=conversation_id
    )
    assert result["ok"] is True
    assert result["data"]["conversation"]["title"] == "Nuevo título"


async def _wait_agent_done(config: CompanionConfig, session_id: str) -> dict[str, Any]:
    import asyncio

    for _ in range(200):
        result = await _call(config, "ide_agent_read", {"session_id": session_id, "cursor": 0})
        if result["session"]["status"] not in {"starting", "running"}:
            return result
        await asyncio.sleep(0.02)
    pytest.fail(f"La sesión {session_id} nunca dejó de estar 'running'.")


@pytest.mark.asyncio
async def test_branch_sin_conversacion_activa_falla(companion_config: CompanionConfig) -> None:
    result = await _command(companion_config, "/branch")
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_branch_sin_historial_no_copia_nada(companion_config: CompanionConfig) -> None:
    original = await _command(companion_config, "/clear Charla vacía", confirmed=True)
    conversation_id = original["nueva_conversacion_id"]

    result = await _command(companion_config, "/branch", conversation_id=conversation_id)

    assert result["ok"] is True
    assert result["data"]["historial_copiado"] is False
    assert "prefill_prompt" not in result
    assert result["nueva_conversacion_id"] != conversation_id


@pytest.mark.asyncio
async def test_branch_copia_el_historial_real_de_la_conversacion(
    companion_config: CompanionConfig, workspace_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El cableado real (cabo 2): ``/branch`` ya no crea una conversación
    vacía con otro nombre -- junta el pedido y la respuesta reales de la
    conversación original (vía ``ide_agent_start``/``ide_agent_read``, el
    mismo camino que usa el compositor) y los deja listos para retomar."""
    workspace = await _authorize(companion_config, workspace_path)

    async def fake_run(_self, **kwargs: Any) -> None:
        kwargs["write_event"]("assistant_final", "Listo, ya arreglé el bug del login.")

    monkeypatch.setattr("edecan_companion.ide_workers_agent.WorkersIDEAgent.run", fake_run)

    creada = await _call(companion_config, "ide_conversation_create", {})
    conversation_id = creada["conversation"]["id"]

    inicio = await _call(
        companion_config,
        "ide_agent_start",
        {
            "workspace_id": workspace["id"],
            "prompt": "Arregla el bug del login",
            "conversation_id": conversation_id,
        },
    )
    await _wait_agent_done(companion_config, inicio["session"]["id"])

    result = await _command(companion_config, "/branch", conversation_id=conversation_id)

    assert result["ok"] is True
    assert result["data"]["historial_copiado"] is True
    assert result["nueva_conversacion_id"] != conversation_id
    assert "Arregla el bug del login" in result["prefill_prompt"]
    assert "Listo, ya arreglé el bug del login." in result["prefill_prompt"]


@pytest.mark.asyncio
async def test_btw_responde_sin_escribir_en_el_historial_principal(
    companion_config: CompanionConfig, workspace_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El cableado real (cabo 2): ``/btw`` ya hace una llamada efímera de
    verdad (parcheada acá para no depender de red) y la pregunta/respuesta
    NUNCA quedan en los eventos de la sesión -- solo en el resultado del
    comando."""
    workspace = await _authorize(companion_config, workspace_path)

    async def fake_run(_self, **kwargs: Any) -> None:
        kwargs["write_event"]("assistant_final", "Usé bcrypt para el hash de contraseñas.")

    monkeypatch.setattr("edecan_companion.ide_workers_agent.WorkersIDEAgent.run", fake_run)

    inicio = await _call(
        companion_config,
        "ide_agent_start",
        {"workspace_id": workspace["id"], "prompt": "Implementa el login"},
    )
    session_id = inicio["session"]["id"]
    await _wait_agent_done(companion_config, session_id)

    async def fake_stream(_self, _request: Any):
        from edecan_llm.base import StreamChunk

        yield StreamChunk(type="text", text="bcrypt (ver el evento final del turno).")

    monkeypatch.setattr("edecan_llm.workers_ai.WorkersAIProvider.stream", fake_stream)

    result = await _command(
        companion_config,
        "/btw ¿qué librería usaste para el hash?",
        session_id=session_id,
    )

    assert result["ok"] is True
    assert result["mensaje"] == "bcrypt (ver el evento final del turno)."
    assert result["data"]["respuesta"] == result["mensaje"]

    leido = await _call(
        companion_config, "ide_agent_read", {"session_id": session_id, "cursor": 0}
    )
    textos = [str(evento.get("text") or "") for evento in leido["events"]]
    assert not any("qué librería usaste" in texto for texto in textos)
    assert not any(texto == result["mensaje"] for texto in textos)


@pytest.mark.asyncio
async def test_goal_definir_y_mostrar(companion_config: CompanionConfig) -> None:
    definido = await _command(
        companion_config,
        "/goal Migrar el login | pytest pasa en verde | lint sin errores",
        conversation_id="conv-1",
    )
    assert definido["ok"] is True
    assert definido["data"]["progreso"] == {"cumplidos": 0, "total": 2}

    mostrado = await _command(companion_config, "/goal", conversation_id="conv-1")
    assert mostrado["ok"] is True
    assert "Migrar el login" in mostrado["mensaje"]


@pytest.mark.asyncio
async def test_goal_sin_criterio_falla(companion_config: CompanionConfig) -> None:
    result = await _command(
        companion_config, "/goal solo descripción sin separador", conversation_id="conv-2"
    )
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_effort_sin_sesion_activa_falla_con_mensaje_claro(
    companion_config: CompanionConfig,
) -> None:
    """``/effort`` necesita un ``session_id`` real -- ver el docstring del
    bug en ``ide_runtime._despachar_comando`` (rama "effort")."""
    result = await _command(companion_config, "/effort alto", conversation_id="conv-3")
    assert result["ok"] is False
    assert "sesión de agente activa" in result["error"]


@pytest.mark.asyncio
async def test_effort_fijar_y_leer_usa_la_sesion_real_no_la_conversacion(
    companion_config: CompanionConfig, workspace_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bug real encontrado corriendo el comando contra un ``opencode serve``
    de verdad (no leyendo el código): ``/effort`` usaba ``clave``
    (``conversation_id``/``workspace_id``) como si fuera el ``session_id`` de
    un agente. ``ide_sessions.SessionManager`` SIEMPRE lee/escribe
    ``effort_store`` con el ``session.id`` real -- al arrancar cada turno
    (``effort_store.obtener(session.id)``, ver ``_turno_opencode``) y en
    ``fijar_esfuerzo`` (busca la sesión viva en ``self._sessions.get(...)``
    para aplicar el cambio EN VIVO). Como ``clave`` es un id de
    CONVERSACIÓN, nunca coincidía con ninguna llave real: el comando
    devolvía ``ok: true`` (parecía funcionar, leía de vuelta lo que acababa
    de escribir) pero el nivel jamás llegaba a ningún turno real, ni en vivo
    ni en el siguiente mensaje. Esta prueba fija que ahora sí usa la llave
    real -- y que la llave vieja (rota) queda intacta."""
    workspace = await _authorize(companion_config, workspace_path)

    async def fake_run(_self, **kwargs: Any) -> None:
        kwargs["write_event"]("assistant_final", "listo")

    monkeypatch.setattr("edecan_companion.ide_workers_agent.WorkersIDEAgent.run", fake_run)

    inicio = await _call(
        companion_config,
        "ide_agent_start",
        {"workspace_id": workspace["id"], "prompt": "hola", "conversation_id": "conv-real"},
    )
    session_id = inicio["session"]["id"]
    await _wait_agent_done(companion_config, session_id)

    fijado = await _command(
        companion_config,
        "/effort bajo",
        conversation_id="conv-real",
        session_id=session_id,
    )
    assert fijado["ok"] is True
    assert fijado["data"]["nivel"] == "bajo"

    leido = await _command(
        companion_config, "/effort", conversation_id="conv-real", session_id=session_id
    )
    assert leido["data"]["nivel"] == "bajo"

    from edecan_companion.ide_runtime import _runtime_for

    runtime = _runtime_for(companion_config)
    # La MISMA llave que ``ide_sessions.py`` lee al arrancar un turno real.
    assert runtime.sessions.effort_store.obtener(session_id).nombre == "bajo"
    # La llave vieja y rota (la conversación) nunca se tocó -- sigue en el
    # default, no en "bajo".
    assert runtime.sessions.effort_store.obtener("conv-real").nombre != "bajo"


@pytest.mark.asyncio
async def test_effort_invalido_falla(
    companion_config: CompanionConfig, workspace_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = await _authorize(companion_config, workspace_path)

    async def fake_run(_self, **kwargs: Any) -> None:
        kwargs["write_event"]("assistant_final", "listo")

    monkeypatch.setattr("edecan_companion.ide_workers_agent.WorkersIDEAgent.run", fake_run)

    inicio = await _call(
        companion_config,
        "ide_agent_start",
        {"workspace_id": workspace["id"], "prompt": "hola"},
    )
    session_id = inicio["session"]["id"]
    await _wait_agent_done(companion_config, session_id)

    result = await _command(
        companion_config, "/effort turbo", conversation_id="conv-4", session_id=session_id
    )
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_plan_proponer_y_aprobar(companion_config: CompanionConfig) -> None:
    propuesto = await _command(
        companion_config,
        "/plan Migrar auth | paso 1 | paso 2",
        conversation_id="conv-plan",
    )
    assert propuesto["ok"] is True
    assert propuesto["data"]["status"] == "proposed"

    aprobado = await _command(companion_config, "/plan aprobar", conversation_id="conv-plan")
    assert aprobado["ok"] is True
    assert aprobado["data"]["status"] == "executing"


@pytest.mark.asyncio
async def test_plan_sin_plan_activo_muestra_mensaje(companion_config: CompanionConfig) -> None:
    result = await _command(companion_config, "/plan", conversation_id="conv-sin-plan")
    assert result["ok"] is True
    assert "No hay ningún plan" in result["mensaje"]


@pytest.mark.asyncio
async def test_permissions_mostrar_y_aflojar(companion_config: CompanionConfig) -> None:
    inicial = await _command(companion_config, "/permissions")
    assert inicial["data"]["politica"]["ejecutar_comandos"] is False

    cambiado = await _command(companion_config, "/permissions ejecutar_comandos on")
    assert cambiado["ok"] is True
    assert cambiado["data"]["politica"]["ejecutar_comandos"] is True


@pytest.mark.asyncio
async def test_config_es_de_solo_lectura(companion_config: CompanionConfig) -> None:
    result = await _command(companion_config, "/config")
    assert result["ok"] is True
    assert result["limitado"] is True
    assert result["data"]["ide_habilitado"] is True


@pytest.mark.asyncio
async def test_doctor_reporta_salud_del_workspace(
    companion_config: CompanionConfig, workspace_path: Path
) -> None:
    workspace = await _authorize(companion_config, workspace_path)
    result = await _command(companion_config, "/doctor", workspace_id=workspace["id"])
    assert result["ok"] is True
    assert result["data"]["workspace_existe"] is True


@pytest.mark.asyncio
async def test_init_escribe_agents_md(
    companion_config: CompanionConfig, workspace_path: Path
) -> None:
    workspace = await _authorize(companion_config, workspace_path)
    result = await _command(companion_config, "/init", workspace_id=workspace["id"])
    assert result["ok"] is True
    assert result["data"]["escrito"] is True
    assert (workspace_path / "AGENTS.md").is_file()

    segunda_vez = await _command(companion_config, "/init", workspace_id=workspace["id"])
    assert segunda_vez["data"]["escrito"] is False


@pytest.mark.asyncio
async def test_batch_sin_workspace_activo_falla(companion_config: CompanionConfig) -> None:
    result = await _command(
        companion_config,
        "/batch archivo_a.py: hacer A; archivo_b.py: hacer B",
    )
    assert result["ok"] is False
    assert "workspace activo" in result["error"]


@pytest.mark.asyncio
async def test_batch_ejecuta_las_subtareas_en_paralelo_de_verdad(
    companion_config: CompanionConfig, workspace_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El cableado real (cabo 2): ``/batch`` ya no solo valida el reparto --
    lanza un turno real y acotado de ``WorkersIDEAgent`` por sub-tarea (aquí,
    parcheado igual que ``test_ide_sessions_plan.py``) y junta sus resultados."""
    workspace = await _authorize(companion_config, workspace_path)

    async def fake_run(_self, **kwargs: Any) -> None:
        prompt = kwargs["prompt"]
        write_event = kwargs["write_event"]
        if "archivo_a.py" in prompt:
            write_event("assistant_final", "A listo")
        elif "archivo_b.py" in prompt:
            write_event("assistant_final", "B listo")
        else:
            pytest.fail(f"prompt de sub-tarea inesperado: {prompt}")

    monkeypatch.setattr("edecan_companion.ide_workers_agent.WorkersIDEAgent.run", fake_run)

    result = await _command(
        companion_config,
        "/batch archivo_a.py: hacer A; archivo_b.py: hacer B",
        workspace_id=workspace["id"],
    )

    assert result["ok"] is True
    estados = result["data"]["estados"]
    assert len(estados) == 2
    assert all(e["estado"] == "completada" for e in estados.values())
    salidas = {e["salida"] for e in estados.values()}
    assert salidas == {"A listo", "B listo"}


@pytest.mark.asyncio
async def test_batch_rechaza_reparto_solapado(
    companion_config: CompanionConfig, workspace_path: Path
) -> None:
    workspace = await _authorize(companion_config, workspace_path)
    result = await _command(
        companion_config,
        "/batch archivo_a.py: hacer A; archivo_a.py: hacer B otra vez",
        workspace_id=workspace["id"],
    )
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_agents_devuelve_especificacion(companion_config: CompanionConfig) -> None:
    result = await _command(companion_config, "/agents")
    assert result["ok"] is True
    assert result["data"]["name"] == "repartir_equipo"


@pytest.mark.asyncio
async def test_memory_sin_workspace_activo_falla(companion_config: CompanionConfig) -> None:
    result = await _command(companion_config, "/memory")
    assert result["ok"] is False
    assert "workspace activo" in result["error"]


@pytest.mark.asyncio
async def test_memory_sin_recuerdos_devuelve_lista_vacia_no_error(
    companion_config: CompanionConfig, workspace_path: Path
) -> None:
    """A diferencia de ``/usage``/``/mcp``/``/voice``/``/remote-control``/
    ``/workflows``, ``/memory`` SÍ está cableado de verdad en este companion
    (``self.sessions.memoria``, la misma ``ide_memoria.MemoriaStore`` que
    ``ide_memory_list``): un workspace sin recuerdos todavía es un resultado
    normal (lista vacía), no un fallo -- ``result["limitado"]`` ni siquiera
    aparece en la respuesta."""
    workspace = await _authorize(companion_config, workspace_path)
    result = await _command(companion_config, "/memory", workspace_id=workspace["id"])
    assert result["ok"] is True
    assert "limitado" not in result
    assert result["data"]["notas"] == []


@pytest.mark.asyncio
async def test_memory_lista_y_olvida_un_recuerdo_real(
    companion_config: CompanionConfig, workspace_path: Path
) -> None:
    """Prueba de punta a punta contra el mismo almacén que usa el botón
    "Memoria y conocimiento" del IDE en la web (``ide_memory_list``/
    ``ide_memory_forget``): ``/memory`` lista lo que el agente guardó de
    verdad y ``/memory olvidar <id>`` lo borra."""
    from edecan_companion.ide_runtime import _runtime_for

    workspace = await _authorize(companion_config, workspace_path)
    runtime = _runtime_for(companion_config)
    # Sembrado directo contra el mismo ``MemoriaStore`` que usaría un turno
    # real del agente (``ide_sessions.py`` llama a ``self.memoria.remember``
    # tras cada turno) -- no hay acción "/memory agregar", por diseño: solo
    # el propio agente decide qué vale la pena recordar (ver el docstring de
    # ``MemoriaStore.remember``).
    runtime.sessions.memoria.remember(
        workspace["id"], "El intérprete de tests vive en .venv/bin/python.", "ubicacion"
    )

    listado = await _command(companion_config, "/memory", workspace_id=workspace["id"])
    assert listado["ok"] is True
    notas = listado["data"]["notas"]
    assert len(notas) == 1
    assert "intérprete de tests" in notas[0]["content"]
    nota_id = notas[0]["id"]

    olvidado = await _command(
        companion_config, f"/memory olvidar {nota_id}", workspace_id=workspace["id"]
    )
    assert olvidado["ok"] is True
    assert olvidado["data"]["deleted_note_id"] == nota_id

    vacio_de_nuevo = await _command(companion_config, "/memory", workspace_id=workspace["id"])
    assert vacio_de_nuevo["data"]["notas"] == []


@pytest.mark.asyncio
async def test_memory_olvidar_sin_id_o_desconocido_falla_con_mensaje_claro(
    companion_config: CompanionConfig, workspace_path: Path
) -> None:
    workspace = await _authorize(companion_config, workspace_path)

    sin_id = await _command(companion_config, "/memory olvidar", workspace_id=workspace["id"])
    assert sin_id["ok"] is False
    assert "se usa como" in sin_id["error"]

    id_desconocido = await _command(
        companion_config, "/memory olvidar no-existe", workspace_id=workspace["id"]
    )
    assert id_desconocido["ok"] is False


@pytest.mark.asyncio
async def test_informativo_no_inventa_datos(companion_config: CompanionConfig) -> None:
    result = await _command(companion_config, "/usage")
    assert result["ok"] is True
    assert result["limitado"] is True
    assert "servidor" in result["mensaje"]


@pytest.mark.asyncio
async def test_informativos_apuntan_a_ubicacion_real_no_generica(
    companion_config: CompanionConfig,
) -> None:
    """``/mcp``/``/voice``/``/remote-control``/``/workflows`` siguen siendo
    informativos (su capacidad real vive fuera de este companion), pero cada
    mensaje debe señalar la pantalla/ruta EXACTA -- verificada contra el
    propio código de ``apps/web``/``apps/api`` -- no un genérico tipo "no
    disponible aquí"."""
    mcp = await _command(companion_config, "/mcp")
    assert mcp["ok"] is True and mcp["limitado"] is True
    assert "/app/ajustes#conexiones" in mcp["mensaje"]

    voice = await _command(companion_config, "/voice")
    assert voice["ok"] is True and voice["limitado"] is True
    assert "/app/ajustes#conexiones" in voice["mensaje"]

    remote = await _command(companion_config, "/remote-control")
    assert remote["ok"] is True and remote["limitado"] is True
    assert "/app/remoto" in remote["mensaje"]

    workflows = await _command(companion_config, "/workflows")
    assert workflows["ok"] is True and workflows["limitado"] is True
    assert "/app/automatizaciones" in workflows["mensaje"]
