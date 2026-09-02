"""Contrato iOS: colaboración multi-bot en TeamConversationView (harness real, no simulado)."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


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
