"""Contratos iOS para la ola 3 de auditoría (rama codex/audit-fixesC).

Cubre: AUD-12a (paginación por cursor), F5 (turno interrupted vía
`x-run-state`), F3 (race del snapshot tras `/clear`) y la deduplicación del
parse del header `x-conversation-epoch`.

Patrón de los tests existentes: verificación estática de que el cliente y las
vistas exponen los ganchos del contrato, no de la ejecución en un iPhone.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APPS = ROOT.parents[1]


def _leer(ruta_relativa: str) -> str:
    return (ROOT / ruta_relativa).read_text(encoding="utf-8")


def _leer_api(ruta_relativa: str) -> str:
    return (APPS / "api" / "edecan_api" / ruta_relativa).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# AUD-12a — paginación por cursor (no limit creciente)
# ---------------------------------------------------------------------------


def test_teams_api_acepta_before_con_misma_semantica_que_workers() -> None:
    """AUD-12a: `GET /v1/teams/{id}/messages` expone `before` y lo pasa a
    `list_normalized_messages` (misma semántica que persistent_agents)."""
    teams = _leer_api("routers/teams.py")
    agents = _leer_api("routers/persistent_agents.py")

    assert "before: str | None = Query(default=None)" in teams
    assert "before=before" in teams
    # Paridad con el endpoint de workers (que ya exponía el cursor).
    assert "before: str | None = Query(default=None)" in agents
    assert "before=before" in agents


def test_api_client_expone_pagina_por_cursor() -> None:
    """AUD-12a: APIClient gana métodos que devuelven mensajes + cursor + has_more."""
    api = _leer("EdecanKit/Sources/EdecanKit/APIClient.swift")
    assert "public func listWorkerMessagesPage" in api
    assert "public func listTeamMessagesPage" in api
    assert "public struct WorkerMessagesPage" in api
    assert "public let nextCursor: String?" in api
    assert "public let hasMore: Bool" in api
    assert "before: String? = nil" in api
    assert "cursorPrimeraPagina" in api
    # El cursor opaco del backend se reenvía tal cual; la forma cruda es la de
    # `HistoryPage` (`messages`, `next_cursor`, `has_more`).
    assert "next_cursor" in api
    assert "has_more" in api


def test_ambas_vistas_usan_cursor_no_limit_creciente() -> None:
    """AUD-12a: las dos vistas consumen `cursorHistorial` y ya NO ensanchan
    `limit` (la variable `limiteNuevo` de ventana creciente desapareció)."""
    chat = _leer("EdecanApp/Screens/BotChatView.swift")
    team = _leer("EdecanApp/Screens/TeamConversationView.swift")

    for vista in (chat, team):
        assert "cursorHistorial" in vista
        assert "before: cursor" in vista
        assert "pagina.nextCursor" in vista
        assert "pagina.hasMore" in vista
        # La paginación por ventana creciente quedó eliminada.
        assert "limiteNuevo" not in vista
        assert "min(limiteHistorial + 50" not in vista
        assert "limiteHistorial < 200" not in vista


def test_preview_de_lista_conserva_limit_1() -> None:
    """AUD-12a: el preview de lista sigue usando limit=1 (no baja el hilo entero)."""
    lista = _leer("EdecanApp/Screens/BotsChatsView.swift")
    assert "listWorkerMessages(workerId: bot.id, limit: 1)" in lista
    assert "listTeamMessages(teamId: equipo.id, limit: 1)" in lista


# ---------------------------------------------------------------------------
# F5 — turno interrupted (x-run-state) distinguido del 409 «en vuelo»
# ---------------------------------------------------------------------------


def test_sse_lee_x_run_state_y_distingue_interrupted() -> None:
    """F5: el cliente SSE lee el header `x-run-state` y traduce
    `interrupted` a un error propio (no un 409 genérico «sigue en vuelo»)."""
    sse = _leer("EdecanKit/Sources/EdecanKit/SSEClient.swift")
    assert 'value(forHTTPHeaderField: "x-run-state")' in sse
    assert '"interrupted"' in sse
    assert "case interrumpido" in sse


def test_bot_reconexion_marca_interrumpido_y_reenvia_con_clave_nueva() -> None:
    """F5: al recibir `interrupted`, BotChat NO reintenta: marca la burbuja
    «Interrumpido — tócala para reenviar» y el toque reenvía con clave NUEVA."""
    chat = _leer("EdecanApp/Screens/BotChatView.swift")
    assert "if case .interrumpido = sseError" in chat
    assert "marcarBurbujaInterrumpida" in chat
    assert "resenviarInterrumpido" in chat
    assert "Interrumpido — tócala para reenviar" in chat
    # El reenvío pasa por `hacerEnvio`, que genera una clave NUEVA por turno.
    assert "await hacerEnvio(client: client, texto: texto, adjuntos: adjuntos)" in chat


def test_equipo_reconexion_marca_interrumpido_y_reenvia_con_clave_nueva() -> None:
    """F5: paridad en TeamConversationView."""
    team = _leer("EdecanApp/Screens/TeamConversationView.swift")
    assert "if case .interrumpido = sseError" in team
    assert "marcarBurbujaEquipoInterrumpida" in team
    assert "resenviarEquipoInterrumpido" in team
    assert "Interrumpido — tócala para reenviar" in team
    # El reenvío genera una clave NUEVA (no la interrumpida).
    assert "let clave = UUID().uuidString" in team


def test_sin_header_comportamiento_actual_retry() -> None:
    """F5 (parse tolerante): sin `x-run-state`, el 409 «en vuelo» sigue
    reintentando (comportamiento actual)."""
    chat = _leer("EdecanApp/Screens/BotChatView.swift")
    team = _leer("EdecanApp/Screens/TeamConversationView.swift")
    # Bot: 202/409 (en vuelo) continúa el bucle de reconexión.
    assert "status == 202 || status == 409" in chat
    # Equipo: 409/408/429 siguen reconectando (no son rechazo definitivo).
    assert "status != 409, status != 408, status != 429" in team


# ---------------------------------------------------------------------------
# F3 — race del snapshot tras /clear
# ---------------------------------------------------------------------------


def test_cargar_tardio_tras_clear_no_persiste_snapshot() -> None:
    """F3: en `cargar()`, una respuesta tardía (snapshot invalidado en esta
    sesión) se descarta ANTES de guardar el snapshot y asignar `items`."""
    chat = _leer("EdecanApp/Screens/BotChatView.swift")
    idx_guard = chat.index("if snapshotInvalidado { return }")
    idx_save = chat.index("botSnapshotStore.save")
    assert idx_guard < idx_save
    # También descarta una respuesta con epoch más viejo que el ya conocido.
    assert "epoch < ultimo" in chat


def test_epoch_viejo_no_pisa_el_ultimo_conocido() -> None:
    """F3: el epoch de la respuesta se compara contra el último visto en la
    sesión y una respuesta fuera de orden se descarta."""
    chat = _leer("EdecanApp/Screens/BotChatView.swift")
    assert "if let epoch = pagina.conversationEpoch" in chat
    assert "let ultimo = epochConversacion" in chat
    assert "epoch < ultimo" in chat


# ---------------------------------------------------------------------------
# Deduplicación del parse de `x-conversation-epoch`
# ---------------------------------------------------------------------------


def test_parse_del_header_epoch_no_duplicado() -> None:
    """El parse del header `x-conversation-epoch` vive en UNA sola función
    compartida por `listWorkerMessagesConEpoch` y las páginas por cursor."""
    api = _leer("EdecanKit/Sources/EdecanKit/APIClient.swift")
    assert "listWorkerMessagesConEpoch" in api
    assert "extraerConversationEpoch" in api
    # El parse crudo del header (`flatMap(Int64.init)`) ocurre exactamente una
    # vez (dentro del helper compartido), no duplicado en dos métodos.
    assert api.count("flatMap(Int64.init)") == 1