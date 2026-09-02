from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_root_tab_view_unifica_bots_y_equipos_en_un_solo_tab() -> None:
    root = (ROOT / "EdecanApp/RootTabView.swift").read_text(encoding="utf-8")
    bots = (ROOT / "EdecanApp/Screens/BotsChatsView.swift").read_text(encoding="utf-8")

    assert "BotsChatsView" in root
    assert "NavigationStack { BotsChatsView() }" in root
    assert 'case "bots", "equipo", "workers", "team", "teams", "equipos", "chats"' in root
    assert "vistos.contains(tab.destino)" in root
    tabs_section = root.split("private var tabsVisibles")[1].split("@ViewBuilder")[0]
    assert "destino: .teams" not in tabs_section
    assert 'destino: .equipo' in tabs_section
    # El título dejó de fijarse con `.navigationTitle("Bots")` dentro de la
    # vista: la lista lleva cabecera propia (búsqueda + filtros) y el título
    # lo pone el NavigationStack del tab.
    assert "private var cabecera" in bots
    assert "TeamConversationView" in bots
    assert "BotChatView" in bots


def test_mobile_config_fallback_ios_usa_un_solo_tab_bots() -> None:
    models = (ROOT / "EdecanKit/Sources/EdecanKit/MobileConfigModels.swift").read_text(
        encoding="utf-8"
    )
    assert 'MobileTabConfig(id: "equipo", title: "Bots", systemIcon: "sparkles"' in models
    assert 'MobileTabConfig(id: "teams"' not in models
