"""Contratos iOS para BOTS-10..13 (recuperación idempotente, approvals,
paginación de historial y deeplink conversation_id vs worker_id).

Patrón de los tests existentes: verificación estática de que el cliente y las
vistas exponen los ganchos del contrato, no de la ejecución en un iPhone.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_equipo_envia_idempotency_key_una_vez_y_reutiliza() -> None:
    """BOTS-10: identidad del envío generada UNA vez y reutilizada hasta terminal."""
    api = (ROOT / "EdecanKit/Sources/EdecanKit/APIClient.swift").read_text(encoding="utf-8")
    team = (ROOT / "EdecanApp/Screens/TeamConversationView.swift").read_text(encoding="utf-8")
    # El builder de la URLRequest acepta la clave y la pone en el header
    # (mismo mecanismo que teams.py::send_team_message consume).
    assert "idempotencyKey: String? = nil" in api
    assert 'request.setValue(idempotencyKey, forHTTPHeaderField: "Idempotency-Key")' in api
    # La vista genera la identidad UNA vez y la conserva hasta el estado terminal.
    assert "let clave = UUID().uuidString" in team
    assert "idempotencyKey: idempotencyKey" in team
    assert "reconectarEnvioEquipo" in team
    # Distingue rechazo definitivo (4xx) de resultado desconocido (red/5xx/409/429).
    assert "marcarEnvioEquipoRechazado" in team
    assert "marcarBurbujaEquipoAtascada" in team
    # Reintento reutiliza la MISMA clave (no genera una nueva por reconexión).
    assert 'teamId: equipo.id, text: texto, idempotencyKey: idempotencyKey' in team


def test_equipo_restaura_aprobaciones_pendientes_desde_servidor() -> None:
    """BOTS-11: al cargar/foreground se consulta listApprovals por conversation_id."""
    team = (ROOT / "EdecanApp/Screens/TeamConversationView.swift").read_text(encoding="utf-8")
    assert "cargarAprobacionesPendientes" in team
    assert "listApprovals(conversationId:" in team
    # Se invoca al cargar y al volver a primer plano (cold reopen incluido).
    assert "await cargarAprobacionesPendientes()" in team
    # Reconciliar tras cada decisión (no decidir dos veces la misma aprobación).
    assert "await cargarAprobacionesPendientes(client: client)" in team
    assert "toolCallsDecididos" in team


def test_historial_paginado_por_cursor_y_previews_limit_1() -> None:
    """BOTS-12/AUD-12a: paginación por cursor (`before`/`next_cursor`), no por
    ventana creciente; el preview de lista conserva limit=1."""
    api = (ROOT / "EdecanKit/Sources/EdecanKit/APIClient.swift").read_text(encoding="utf-8")
    chat = (ROOT / "EdecanApp/Screens/BotChatView.swift").read_text(encoding="utf-8")
    team = (ROOT / "EdecanApp/Screens/TeamConversationView.swift").read_text(encoding="utf-8")
    assert "func listWorkerMessages(workerId: String, limit: Int = 50)" in api
    assert "func listTeamMessages(teamId: String, limit: Int = 50)" in api
    assert "listWorkerMessagesPage" in api
    assert "listTeamMessagesPage" in api
    # Ambas vistas pagan por cursor y exponen el botón de "más".
    for vista in (chat, team):
        assert "limiteHistorial" in vista
        assert "cursorHistorial" in vista
        assert "cargarMasHistorial" in vista
        assert "hayMasHistorial" in vista
        assert "Cargar mensajes anteriores" in vista
        assert "limit: limiteHistorial" in vista
        assert "before: cursor" in vista
    # El preview de lista sigue usando limit=1 (no baja el hilo entero).
    lista = (ROOT / "EdecanApp/Screens/BotsChatsView.swift").read_text(encoding="utf-8")
    assert "listWorkerMessages(workerId: bot.id, limit: 1)" in lista
    assert "listTeamMessages(teamId: equipo.id, limit: 1)" in lista


def test_deeplink_bot_compara_conversation_id_no_worker_id() -> None:
    """BOTS-13: el refresh de push compara conversation_id resuelto, no worker_id."""
    chat = (ROOT / "EdecanApp/Screens/BotChatView.swift").read_text(encoding="utf-8")
    models = (ROOT / "EdecanKit/Sources/EdecanKit/MissionsModels.swift").read_text(encoding="utf-8")
    # El worker resuelve conversation_id del listado (payload del servidor).
    assert "conversationId" in models
    assert 'case conversationId = "conversation_id"' in models
    # El onReceive compara contra el conversation_id del bot, no contra bot.id.
    assert "conversationDelBot" in chat
    assert "bot.conversationId ?? bot.id" in chat
    assert "chatId == conversationDelBot" in chat