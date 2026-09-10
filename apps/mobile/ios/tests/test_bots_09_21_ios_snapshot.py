"""Contratos iOS para BOTS-09 (clear/DELETE invalida snapshot + epoch) y
BOTS-21 (snapshot v2 con referencias de adjuntos y flag de recorte).

Patrón de los tests existentes: verificación estática de que el cliente y las
vistas exponen los ganchos del contrato, no de la ejecución en un iPhone.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_clear_invalida_snapshot_de_disco_y_marca_la_sesion() -> None:
    """BOTS-09: tras `/clear` exitoso se borra el snapshot ANTES de limpiar la
    memoria, y `pintarSnapshotLocalSiVacio` no repinta un snapshot invalidado."""
    chat = (ROOT / "EdecanApp/Screens/BotChatView.swift").read_text(encoding="utf-8")
    state = (ROOT / "EdecanKit/Sources/EdecanKit/InteractionState.swift").read_text(encoding="utf-8")

    # El store expone la invalidación de UN worker (no solo clearAll).
    assert "func remove(workerId:" in state
    # Tras el clear exitoso se invalida el snapshot ANTES de `items.removeAll()`.
    idx_clear = chat.index("clearWorkerMessages(workerId: bot.id)")
    idx_remove = chat.index("botSnapshotStore.remove(workerId: bot.id)")
    idx_items = chat.index("items.removeAll()")
    assert idx_clear < idx_remove < idx_items
    # La sesión queda marcada y el repintado local la respeta.
    assert "snapshotInvalidado" in chat
    assert "guard items.isEmpty, !snapshotInvalidado else { return }" in chat


def test_delete_worker_invalida_snapshot_local() -> None:
    """BOTS-09: DELETE exitoso de un bot invalida el snapshot de ese worker."""
    api = (ROOT / "EdecanKit/Sources/EdecanKit/APIClient.swift").read_text(encoding="utf-8")
    # El único punto que atraviesa todo borrado de bot invalida el snapshot.
    assert "func deleteWorker(id: String) async throws" in api
    assert "BotChatSnapshotStore().remove(workerId: id)" in api


def test_snapshot_v2_guarda_adjuntos_y_flag_de_recorte() -> None:
    """BOTS-21: el snapshot v2 preserva referencias de adjuntos (sin contenido)
    y un flag cuando el texto se recortó a 8_000 caracteres."""
    state = (ROOT / "EdecanKit/Sources/EdecanKit/InteractionState.swift").read_text(encoding="utf-8")

    assert "struct CachedBotAttachment: Codable" in state
    assert "public let fileId: String" in state
    assert "public let adjuntos: [CachedBotAttachment]" in state
    assert "public let recortado: Bool" in state
    # El recorte se marca en el init (no se finge un entregable completo).
    assert "texto.count > 8_000" in state
    # La vista persiste las referencias al guardar el snapshot.
    chat = (ROOT / "EdecanApp/Screens/BotChatView.swift").read_text(encoding="utf-8")
    assert "CachedBotAttachment(fileId:" in chat


def test_v1_legacy_se_lee_sin_romperse() -> None:
    """BOTS-21: los snapshots v1 (sin adjuntos/recortado) se decodifican con
    defaults; el schema 1 sigue siendo usable."""
    state = (ROOT / "EdecanKit/Sources/EdecanKit/InteractionState.swift").read_text(encoding="utf-8")

    # Decodificación tolerante de los campos nuevos (v1 no los trae).
    assert "adjuntos = (try? container.decode([CachedBotAttachment].self, forKey: .adjuntos)) ?? []" in state
    assert "recortado = (try? container.decode(Bool.self, forKey: .recortado)) ?? false" in state
    # El schema mínimo aceptado es 1; el actual es 2.
    assert "currentSchemaVersion = 2" in state
    assert "minimumSchemaVersion = 1" in state
    assert "(Self.minimumSchemaVersion...Self.currentSchemaVersion).contains(schemaVersion)" in state


def test_epoch_viejo_no_se_repinta() -> None:
    """BOTS-09: el snapshot guarda `conversation_epoch`; al pintar, un epoch
    más viejo que el conocido por el servidor no se repinta."""
    state = (ROOT / "EdecanKit/Sources/EdecanKit/InteractionState.swift").read_text(encoding="utf-8")
    api = (ROOT / "EdecanKit/Sources/EdecanKit/APIClient.swift").read_text(encoding="utf-8")
    chat = (ROOT / "EdecanApp/Screens/BotChatView.swift").read_text(encoding="utf-8")

    # El snapshot modela el epoch y lo decodifica tolerante (si no viene, nil).
    assert "public let conversationEpoch: Int64?" in state
    assert 'conversationEpoch = try container.decodeIfPresent(Int64.self, forKey: .conversationEpoch)' in state
    # El cliente lee el epoch del header de la respuesta (opcional).
    assert "listWorkerMessagesConEpoch" in api
    assert 'value(forHTTPHeaderField: "x-conversation-epoch")' in api
    # La vista compara epoch del snapshot contra el más reciente conocido.
    assert "epochConversacion" in chat
    assert "epochSnap < epochActual" in chat


def test_offline_aviso_honesto_para_adjuntos_no_cacheados() -> None:
    """BOTS-21: pintar offline un mensaje con adjuntos no cacheados muestra un
    aviso honesto, nunca aparenta que el entregable está completo."""
    chat = (ROOT / "EdecanApp/Screens/BotChatView.swift").read_text(encoding="utf-8")

    assert "adjuntosSoloReferencia" in chat
    assert "Adjuntos disponibles al reconectar" in chat
    assert "func indicadorAdjuntosPendientes" in chat