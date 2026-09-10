"""Contrato iOS: colaboración multi-bot en TeamConversationView (harness real, no simulado)."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APPS = ROOT.parents[1]


def test_team_conversation_usa_sse_client_y_no_bloquea_input() -> None:
    team = (ROOT / "EdecanApp/Screens/TeamConversationView.swift").read_text(encoding="utf-8")
    assert "SSEClient" in team
    assert "TeamMessageStreamClient" not in team
    assert "turnosEnVuelo" in team
    assert "tareaDetenible" in team
    assert "private let sseClient = SSEClient()" in team
    # Input no se deshabilita por envío en curso (patrón BotChatView).
    assert "TextField(" in team
    assert ".disabled(enviando" not in team
    # app.md L149–151: envío inmediato sin cancelar turno anterior.
    assert "Envío INMEDIATO" in team
    encolar = team.split("private func encolarEnvio", 1)[1].split("private func detenerTurno", 1)[0]
    assert "?.cancel()" not in encolar


def test_team_conversation_superficie_colaboracion_grok() -> None:
    team = (ROOT / "EdecanApp/Screens/TeamConversationView.swift").read_text(encoding="utf-8")
    ui = (ROOT / "EdecanApp/Componentes/TeamCollaborationUI.swift").read_text(encoding="utf-8")
    assert "TeamParallelMacBar" in team
    assert "TeamNarracionRow" in team
    assert "TeamQuestionCardView" in team
    assert "TeamToolActivityRow" in team
    assert "confirmationRequired" in team
    assert "delegar_mision" in team
    assert "enviar_mensaje_bot" in team
    assert "followUpTurn" in team
    assert "registrarNarracionEntreBots" in team
    assert "asignacion" in team
    assert "Se lo pasé a" in ui
    assert "responderDelEquipo" in team
    assert "tu Mac" in ui.lower() or "en tu Mac" in ui
    assert "desktopcomputer" in ui


def test_api_client_expone_peticiones_equipo_y_confirmacion() -> None:
    api = (ROOT / "EdecanKit/Sources/EdecanKit/APIClient.swift").read_text(encoding="utf-8")
    assert "func peticionMensajeEquipo(" in api
    assert "func peticionConfirmarConversacion(" in api
    assert r'"/v1/teams/\(teamId)/message"' in api
    assert r'"/v1/conversations/\(conversationId)/confirm"' in api


def test_team_model_expone_conversation_id() -> None:
    models = (ROOT / "EdecanKit/Sources/EdecanKit/CollaborationModels.swift").read_text(
        encoding="utf-8"
    )
    assert "conversationId" in models
    assert "conversation_id" in models


def test_team_conversation_proactividad_needs_you() -> None:
    team = (ROOT / "EdecanApp/Screens/TeamConversationView.swift").read_text(encoding="utf-8")
    ui = (ROOT / "EdecanApp/Componentes/TeamCollaborationUI.swift").read_text(encoding="utf-8")
    assert "listAutomationSuggestions" in team
    assert "TeamNeedsYouPanel" in team
    assert "sugerenciasProactivas" in team
    assert "cargarSugerenciasProactivas" in team
    assert "promptDelegacion" in ui
    assert "Needs you" in ui
    assert "proactive_scan" in team or "automations/suggestions" in team.lower()


def test_team_asignacion_chip_evento_sin_handoff() -> None:
    team = (ROOT / "EdecanApp/Screens/TeamConversationView.swift").read_text(encoding="utf-8")
    ui = (ROOT / "EdecanApp/Componentes/TeamCollaborationUI.swift").read_text(encoding="utf-8")
    models = (ROOT / "EdecanKit/Sources/EdecanKit/CollaborationModels.swift").read_text(
        encoding="utf-8"
    )
    servicio = (APPS / "api" / "edecan_api" / "bot_turn_service.py").read_text(encoding="utf-8")
    teams = (APPS / "api" / "edecan_api" / "routers" / "teams.py").read_text(encoding="utf-8")
    assert "AsignadoAChipView" in team
    assert "Asignado a" in ui
    assert "esEventoAsignacion" in models
    assert "assigned_worker_id" in models
    assert "assigned_worker_name" in models
    assert "persist_team_assignment_event" in teams
    assert 'evento == "asignacion"' in models or 'evento == \"asignacion\"' in models
    assert "destination_worker_id" not in team
    assert "workerAssigned" not in team
    assert "emit_routing_assignment" not in teams
    assert "persist_worker_assignment" not in servicio
    assert "assigned_worker_id" in servicio


def test_fronti_asignacion_metadata_sin_tool_routing() -> None:
    """BotAlpha (#49) consume auto-assign bc-c294fe7e; no pisa bc-cd8fac1c tool-intent."""
    team = (ROOT / "EdecanApp/Screens/TeamConversationView.swift").read_text(encoding="utf-8")
    models = (ROOT / "EdecanKit/Sources/EdecanKit/CollaborationModels.swift").read_text(
        encoding="utf-8"
    )
    assert "resolverAsignacionAuto" in models
    assert "assignedWorkerId" in models
    assert "workerId" in models
    assert "workerDisplayName" in models
    assert "display_name" in models
    assert "assigned_worker_id" in models or "worker_id" in models
    assert "destination_worker_id" not in team
    assert "destinationWorkerId" not in models
    assert "resolverAsignacionAuto" in team or "esEventoAsignacion" in team
    assert "select_responder_for_team" not in team
    assert "capability_routing" not in team
    assert "bot_persona" not in team
    assert "TeamQuestionCardView" in team


def test_team_conversation_detener_turno_paridad_chat() -> None:
    team = (ROOT / "EdecanApp/Screens/TeamConversationView.swift").read_text(encoding="utf-8")
    ui = (ROOT / "EdecanApp/Componentes/TeamCollaborationUI.swift").read_text(encoding="utf-8")
    assert "detenerTurno" in team
    assert "detenidoPorUsuario" in team
    assert "TeamSendStopButton" in ui
    assert "stop.circle.fill" in ui
    assert "CancellationError" in team
    assert "cerrarBurbujaPorDetencion" in team
    assert "tareaDetenible?.cancel()" in team
    assert "turnoEnCurso: turnoEnCurso" in team
    assert "esConfirmacionExpirada" in team
