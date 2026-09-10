"""Contrato backend harness para BotChat (P0 widgets/needs-you/approvals).

BotAlpha posee SwiftUI; acá solo verificamos que el API y el cliente iOS
expongan los ganchos que el harness backend entrega.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APPS = ROOT.parents[1]


def test_bot_messages_api_expone_tool_calls_en_historial() -> None:
    servicio = (APPS / "api" / "edecan_api" / "bot_turn_service.py").read_text(
        encoding="utf-8"
    )
    assert "tool_calls" in servicio
    assert "normalize_stored_message" in servicio
    assert '"tool_calls"' in servicio or "'tool_calls'" in servicio


def test_automations_suggestions_acepta_worker_id() -> None:
    router = (APPS / "api" / "edecan_api" / "routers" / "automations.py").read_text(
        encoding="utf-8"
    )
    assert "worker_id: uuid.UUID | None = None" in router


def test_approvals_filtra_por_conversation_y_worker() -> None:
    router = (APPS / "api" / "edecan_api" / "routers" / "approvals.py").read_text(
        encoding="utf-8"
    )
    assert "conversation_id: uuid.UUID | None = None" in router
    assert "worker_id: uuid.UUID | None = None" in router
    assert "agent_snapshot->>'worker_id'" in router


def test_api_client_tiene_ganchos_bot_chat() -> None:
    api = (ROOT / "EdecanKit/Sources/EdecanKit/APIClient.swift").read_text(encoding="utf-8")
    assert "listWorkerMessages" in api
    assert "listApprovals" in api
    assert "approveApproval" in api
    assert "listAutomationSuggestions" in api
    assert "peticionConfirmarConversacion" in api


def test_bot_turn_expone_kind_aviso_en_historial() -> None:
    servicio = (APPS / "api" / "edecan_api" / "bot_turn_service.py").read_text(
        encoding="utf-8"
    )
    assert '"kind": "aviso"' in servicio or "'kind': 'aviso'" in servicio


def test_bot_chat_view_full_power_ios() -> None:
    chat = (ROOT / "EdecanApp/Screens/BotChatView.swift").read_text(encoding="utf-8")
    assert "BotConectoresSheet" in chat or "mostrandoConectores" in chat
    assert "BloqueChatView" in chat
    assert "herramientasEnvioExterno" in chat
    assert "colaBorradoresSociales" in chat
    assert "messageStart" in chat
    assert "mostrarRemoto()" in chat
    assert "textoInicial" in chat


def test_bot_poderes_sheet_usa_api_real() -> None:
    sheet = (ROOT / "EdecanApp/Screens/BotPoderesSheets.swift").read_text(encoding="utf-8")
    assert "listSkills()" in sheet
    assert "listMCPServers()" in sheet
    assert "SocialConnectorsSection" in sheet


def test_computer_viewer_hook_vps_first() -> None:
    """CONTINUAR §6: Ver Mac abre RemotoView/ComputerView, no un backend fake."""
    poderes = (ROOT / "EdecanApp/Screens/BotPoderesSheets.swift").read_text(
        encoding="utf-8"
    )
    chat = (ROOT / "EdecanApp/Screens/BotChatView.swift").read_text(encoding="utf-8")
    computer = (ROOT / "EdecanApp/Screens/ComputerView.swift").read_text(encoding="utf-8")
    root = (ROOT / "EdecanApp/RootTabView.swift").read_text(encoding="utf-8")
    assert "func mostrarRemoto()" in root
    assert "presentacion = .remote" in root
    assert "RemotoView()" in root
    assert "tabRouter.mostrarRemoto()" in chat
    assert "RemotoView()" in poderes
    assert "ComputerView()" in poderes
    assert "RemotoView()" in computer
    assert "GET /v1/computer/sessions" in computer


def test_pending_approval_expone_worker_id() -> None:
    models = (ROOT / "EdecanKit/Sources/EdecanKit/WorkforceModels.swift").read_text(
        encoding="utf-8"
    )
    assert "workerId" in models
    assert "worker_id" in models


def test_needs_you_filtra_por_agent_id() -> None:
    ui = (ROOT / "EdecanApp/Componentes/TeamCollaborationUI.swift").read_text(encoding="utf-8")
    assert "sugerenciasAccionables" in ui
    assert "agentId" in ui
    assert "promptParaBot" in ui


def test_bot_chat_needs_you_pasa_worker_id() -> None:
    """CONTINUAR §6: Needs you 1:1 filtra en API, no solo en cliente."""
    chat = (ROOT / "EdecanApp/Screens/BotChatView.swift").read_text(encoding="utf-8")
    api = (ROOT / "EdecanKit/Sources/EdecanKit/APIClient.swift").read_text(encoding="utf-8")
    assert "listAutomationSuggestions(workerId: bot.id)" in chat
    assert '("worker_id", workerId)' in api


def test_questionblock_sobrevive_reload_desde_tool_calls() -> None:
    """CONTINUAR §6: al reabrir BotChat, preguntar_al_usuario pinta widget."""
    chat = (ROOT / "EdecanApp/Screens/BotChatView.swift").read_text(encoding="utf-8")
    models = (ROOT / "EdecanKit/Sources/EdecanKit/CollaborationModels.swift").read_text(
        encoding="utf-8"
    )
    kit_test = (
        ROOT / "EdecanKit/Tests/EdecanKitTests/TeamMessageReloadQuestionTests.swift"
    ).read_text(encoding="utf-8")
    assert "bloquesDesdeToolCalls" in chat
    assert "preguntasDe" in chat
    assert "TeamQuestionCardView" in chat
    assert "bloquesDesdeToolCalls" in models
    assert "preguntar_al_usuario" in kit_test
    assert "¿A qué cuenta publico este post?" in kit_test


def test_bots_chats_lista_needs_you() -> None:
    lista = (ROOT / "EdecanApp/Screens/BotsChatsView.swift").read_text(encoding="utf-8")
    assert "TeamNeedsYouPanel" in lista
    assert "textoInicial" in lista
    assert "delegarNeedsYou" in lista


def test_api_client_tiene_ganchos_conectores_oauth_vps() -> None:
    api = (ROOT / "EdecanKit/Sources/EdecanKit/APIClient.swift").read_text(encoding="utf-8")
    models_path = ROOT / "EdecanKit/Sources/EdecanKit/ConnectorModels.swift"
    models = models_path.read_text(encoding="utf-8")
    router = (APPS / "api" / "edecan_api" / "routers" / "connectors.py").read_text(encoding="utf-8")
    social = (ROOT / "EdecanApp/Screens/SocialConnectorsSection.swift").read_text(encoding="utf-8")
    assert "listConnectors" in api
    assert "getConnectorAuthorizeUrl" in api
    assert "disconnectConnector" in api
    assert 'returnTo: "mobile"' in api or "return_to" in api
    assert "SocialOAuthConnectorKey" in models
    assert "SocialOAuthSession" in social
    assert "confirmationDialog" in social
    assert "return_to: Literal" in router
    assert "edecan://conectores" in router


def test_conectores_view_sin_proximamente() -> None:
    view = (ROOT / "EdecanApp/Screens/ConectoresView.swift").read_text(encoding="utf-8")
    assert "Próximamente" not in view
    assert "SocialConnectorsSection" in view


def test_perfil_embebe_oauth_redes_y_refresca_deep_link() -> None:
    perfil = (ROOT / "EdecanApp/Screens/PerfilView.swift").read_text(encoding="utf-8")
    redes = (ROOT / "EdecanApp/Screens/RedesView.swift").read_text(encoding="utf-8")
    social = (ROOT / "EdecanApp/Screens/SocialConnectorsSection.swift").read_text(encoding="utf-8")
    assert "RedesView" in perfil
    assert "edecanConectoresOAuth" in redes
    assert "profileGlass" in social
    assert "Próximamente" not in perfil


def test_bot_chat_beats_humanos_sin_nombres_crudos_de_tool() -> None:
    chat = (ROOT / "EdecanApp/Screens/BotChatView.swift").read_text(encoding="utf-8")
    ui = (ROOT / "EdecanApp/Componentes/TeamCollaborationUI.swift").read_text(encoding="utf-8")
    team = (ROOT / "EdecanApp/Screens/TeamConversationView.swift").read_text(encoding="utf-8")
    assert "BeatHerramientaCopy" in chat
    assert "BeatHerramientaCopy" in ui
    assert "Revisando el código" in ui
    assert "TeamToolActivityRow" in chat
    assert "herramientaActiva" in chat
    assert "registrarToolStart" in chat
    assert "registrarToolEnd" in chat
    assert "beatParaToolStart" in ui
    assert "beatParaToolEnd" in ui
    assert "avisar_avance" in ui
    assert "anexarBeatEquipoSiNuevo" in team
    assert 'kind == "aviso"' in team


def test_evento_asignacion_historial_y_chip_ios() -> None:
    servicio = (APPS / "api" / "edecan_api" / "bot_turn_service.py").read_text(encoding="utf-8")
    models = (ROOT / "EdecanKit/Sources/EdecanKit/CollaborationModels.swift").read_text(
        encoding="utf-8"
    )
    team = (ROOT / "EdecanApp/Screens/TeamConversationView.swift").read_text(encoding="utf-8")
    assert "persist_team_assignment_event" in servicio
    assert 'evento == "asignacion"' in servicio or "evento\") == \"asignacion\"" in servicio
    assert "assigned_worker_id" in servicio
    assert "esEventoAsignacion" in models
    assert "AsignadoAChipView" in team
    assert "nonEmpty(assignedWorkerName)" in models or "assignedWorkerName" in models
    assert "workerDisplayName" in models or "display_name" in models
    assert "worker_assigned" not in servicio
    assert "persist_worker_assignment" not in servicio


def test_social_connectors_cero_mocks_catalogo_y_reintentar() -> None:
    social = (ROOT / "EdecanApp/Screens/SocialConnectorsSection.swift").read_text(encoding="utf-8")
    models = (ROOT / "EdecanKit/Sources/EdecanKit/ConnectorModels.swift").read_text(encoding="utf-8")
    assert "filasSociales" in social
    assert "SocialOAuthConnectorKey.allCases" in social
    assert "errorCarga" in social
    assert "Reintentar" in social
    assert "filaCatalogo" in models
    assert "Sin redes en el catálogo" not in social
    assert "No hay conectores sociales en el catálogo" not in social
    assert 'conectada ? "Conectada"' not in social
    assert "status ?? \"active\"" not in models
    assert "has_access_token" in models
    assert "hasAccessToken == true" in models
    assert '"Conectada"' not in social.split("estadoBadge")[1][:600]
    assert "Pendiente" in social


def test_oauth_conectada_solo_has_access_token_no_status() -> None:
    models = (ROOT / "EdecanKit/Sources/EdecanKit/ConnectorModels.swift").read_text(encoding="utf-8")
    conectada_block = models.split("var conectada")[1].split("var esPendiente")[0]
    assert "hasAccessToken == true" in conectada_block
    assert "status" not in conectada_block
    assert '"active"' not in conectada_block


def test_hard_rule_vps_only_status_y_reintentar() -> None:
    social = (ROOT / "EdecanApp/Screens/SocialConnectorsSection.swift").read_text(encoding="utf-8")
    conectores = (ROOT / "EdecanApp/Screens/ConectoresView.swift").read_text(encoding="utf-8")
    computer = (ROOT / "EdecanApp/Screens/ComputerView.swift").read_text(encoding="utf-8")
    mcp_models = (ROOT / "EdecanKit/Sources/EdecanKit/CapabilityModels.swift").read_text(encoding="utf-8")
    assert "listConnectors()" in social
    assert "listMCPServers()" in conectores
    assert "listRemoteMachines()" in computer
    assert '?? "active"' not in mcp_models.split("MCPServerSummary")[1][:800]
    assert "Reintentar" in conectores
    assert "Reintentar" in computer
    assert "Conectada" not in social.split("estadoBadge")[1][:800]
    assert "Pendiente" in social


def test_bot_registry_thin_path_automations_y_mcp() -> None:
    repo = APPS.parent
    registry = (repo / "packages/core/edecan_core/bot_registry.py").read_text(encoding="utf-8")
    persona = (repo / "packages/core/edecan_core/bot_persona.py").read_text(encoding="utf-8")
    harness = (repo / "packages/core/edecan_core/bot_harness.py").read_text(encoding="utf-8")
    assert "BOT_CHAT_AUTOMATIONS_TOOL_NAMES" in persona
    assert "gestionar_automatizacion" in persona
    assert "BOT_CHAT_AUTOMATIONS_TOOL_NAMES" in registry
    assert "navegar_web" in harness
    assert "avisar_avance" in harness


def test_comm_notifications_layout_lock_grok() -> None:
    """Grande = cara del bot; pequeño = badge AppIcon. Sin inversión logo-only."""
    renderer = (ROOT / "EdecanKit/Sources/EdecanKit/BotPushAvatarRenderer.swift").read_text(
        encoding="utf-8"
    )
    nse = (ROOT / "EdecanNotificationService/NotificationService.swift").read_text(encoding="utf-8")
    assert "Layout lock" in renderer
    assert "compositePNG" in renderer
    assert "dibujarBadge" in renderer
    assert "Nunca usa el logo como imagen grande" in renderer or "Nunca invertir" in renderer
    assert "senderPNGData" in renderer
    assert "intent.setImage(avatarImage, forParameterNamed: \\.sender)" in nse
    assert "BotPushAvatarRenderer.serviceDisplayName" in nse


def test_mcp_install_contract_api_client() -> None:
    """BotBeta MCP backend contract — install-from-iOS (tip be6d5424)."""
    api = (ROOT / "EdecanKit/Sources/EdecanKit/APIClient.swift").read_text(encoding="utf-8")
    models = (ROOT / "EdecanKit/Sources/EdecanKit/CapabilityModels.swift").read_text(encoding="utf-8")
    router = (APPS / "api" / "edecan_api" / "routers" / "mcp.py").read_text(encoding="utf-8")
    assert "getMCPHealth()" in api
    assert "putMCPServer" in api
    assert "deleteMCPServer" in api
    assert "listMCPServerTools" in api
    assert "/v1/mcp/health" in api
    assert "MCPHealthSummary" in models
    assert "MCPServerInput" in models
    assert '"/v1/mcp/health"' in router or "@router.get(\"/health\"" in router


def test_conectores_view_mcp_health_y_delete_kit() -> None:
    view = (ROOT / "EdecanApp/Screens/ConectoresView.swift").read_text(encoding="utf-8")
    banner = (ROOT / "EdecanApp/Componentes/MCPHealthBanner.swift").read_text(encoding="utf-8")
    assert "getMCPHealth()" in view
    assert "deleteMCPServer" in view
    assert "MCPHealthBanner" in view
    assert "MCPHealthSummary" in banner
    assert "RegistrarMCPSheet" in view


def test_bot_conectores_sheet_quita_mcp_via_kit() -> None:
    sheet = (ROOT / "EdecanApp/Screens/BotPoderesSheets.swift").read_text(encoding="utf-8")
    registrar = (ROOT / "EdecanApp/Screens/RegistrarMCPSheet.swift").read_text(encoding="utf-8")
    assert "deleteMCPServer" in sheet
    assert "getMCPHealth()" in sheet
    assert "MCPHealthBanner" in sheet
    assert "Quitar del VPS" in sheet
    assert "desconectar_mcp si ya no lo necesito" not in sheet
    assert "putMCPServer" in registrar
    assert "validar = true" in registrar


def test_chat_frio_pinta_snapshot_sin_esperar_lista() -> None:
    """Colombia↔Virginia: no encadenar GET lista + /main + hilo antes de pintar."""
    vm = (ROOT / "EdecanApp/Componentes/ChatViewModel.swift").read_text(encoding="utf-8")
    view = (ROOT / "EdecanApp/Screens/ChatView.swift").read_text(encoding="utf-8")
    api = (ROOT / "EdecanKit/Sources/EdecanKit/APIClient.swift").read_text(encoding="utf-8")
    store = (ROOT / "EdecanKit/Sources/EdecanKit/InteractionState.swift").read_text(
        encoding="utf-8"
    )
    assert "pintarHiloCacheadoSiAunNoInicio" in vm
    assert "pintarSnapshotSiExiste" in vm
    assert "principalConversationId" in vm
    assert "sincronizandoEnSilencio" in vm
    assert "Task { await cargarConversaciones(client: client) }" in vm
    assert "await cargarConversaciones(client: client)\n\n        let pending" not in vm
    assert 'ProgressView("Cargando conversacion…")' not in view
    assert "cargandoConversacion && viewModel.mensajes.isEmpty" in view
    assert "limit: Int = 50" in api
    assert '("limit", String(limit))' in api
    assert "CachedConversationSnapshot" in store
    assert "BotChatSnapshotStore" in store


def test_bots_lista_preview_no_baja_el_hilo_entero() -> None:
    chats = (ROOT / "EdecanApp/Screens/BotsChatsView.swift").read_text(encoding="utf-8")
    assert "listWorkerMessages(workerId: bot.id, limit: 1)" in chats
    assert "listTeamMessages(teamId: equipo.id, limit: 1)" in chats
