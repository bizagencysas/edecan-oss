from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_share_extension_declara_texto_y_url_y_no_finge_archivos_binarios() -> None:
    project = (ROOT / "project.yml").read_text(encoding="utf-8")
    source = (ROOT / "EdecanShareExtension/ShareViewController.swift").read_text(encoding="utf-8")

    assert "EdecanShareExtension:" in project
    assert "NSExtensionActivationSupportsText: true" in project
    assert "NSExtensionActivationSupportsWebURL: true" in project
    assert 'components.host = "share"' in source
    assert "App Group" in source
    assert "SharePayloadStore" in source
    entitlements = (ROOT / "EdecanShareExtension/EdecanShareExtension.entitlements").read_text(
        encoding="utf-8"
    )
    assert "group.cc.edecan.app" in entitlements


def test_app_intent_abre_deep_link_sin_auto_enviar() -> None:
    source = (ROOT / "EdecanApp/EdecanAppIntents.swift").read_text(encoding="utf-8")

    assert "AskEdecanIntent" in source
    assert 'components.host = "share"' in source
    assert "opensIntent: OpenURLIntent" in source
    assert "openAppWhenRun" in source
    assert "CreateEdecanTaskIntent" in source
    assert "SearchEdecanConversationsIntent" in source


def test_app_intents_ejecutan_agua_serie_y_aprobar_con_confirmacion() -> None:
    acciones = (ROOT / "EdecanApp/EdecanAccionesIntents.swift").read_text(encoding="utf-8")
    shortcuts = (ROOT / "EdecanApp/EdecanAppIntents.swift").read_text(encoding="utf-8")
    assert "RegistrarAguaEdecanIntent" in acciones
    assert "RegistrarSerieEdecanIntent" in acciones
    assert "AprobarPendienteEdecanIntent" in acciones
    assert "openAppWhenRun = false" in acciones
    assert "requestConfirmation" in acciones
    assert "requiresAuthentication" in acciones
    assert "pendienteParaAprobar" in acciones
    assert "Di que sí" not in shortcuts
    assert "RegistrarAguaEdecanIntent" in shortcuts
    assert "AprobarPendienteEdecanIntent" in shortcuts


def test_widget_muestra_payloads_compartidos_y_abre_deep_link() -> None:
    source = (ROOT / "EdecanWidgets/EdecanWidgetsBundle.swift").read_text(encoding="utf-8")

    assert "SharePayloadStore.pendingCount()" in source
    assert "WidgetSnapshotStore.read()" in source
    assert "activeMissionCount" in source
    assert "pendingReminderCount" in source
    assert "nextReminderAt" in source
    assert 'widgetURL(URL(string: "edecan://share"))' in source


def test_app_actualiza_snapshot_del_widget_sin_credenciales_en_la_extension() -> None:
    store = (ROOT / "EdecanKit/Sources/EdecanKit/WidgetSnapshotStore.swift").read_text(
        encoding="utf-8"
    )
    app = (ROOT / "EdecanApp/RootTabView.swift").read_text(encoding="utf-8")
    assert "async let missions" in store
    assert "async let reminders" in store
    assert "WidgetSnapshotStore.refresh(client: client)" in app
    assert "Authorization" not in store
    assert "getAccessToken" not in store


def test_snapshot_del_widget_tiene_test_nativo() -> None:
    tests = (ROOT / "EdecanKit/Tests/EdecanKitTests/WidgetSnapshotStoreTests.swift").read_text(
        encoding="utf-8"
    )
    assert "WidgetSnapshotStoreTests" in tests
    assert "JSONEncoder" in tests


def test_privacidad_ios_expone_exportacion_y_memoria_sin_borrado_falso_de_cuenta() -> None:
    client = (ROOT / "EdecanKit/Sources/EdecanKit/APIClient.swift").read_text(encoding="utf-8")
    view = (ROOT / "EdecanApp/Screens/PrivacidadView.swift").read_text(encoding="utf-8")
    assert "/v1/privacy/export" in client
    assert "DELETE" in client and "/v1/memory" in client
    assert "exportarDatosPrivacidad" in view
    assert "borrarMemoriaCompleta" in view
    assert "contraseña/TOTP" in view
    tests = (ROOT / "EdecanKit/Tests/EdecanKitTests/APISessionRaceTests.swift").read_text(
        encoding="utf-8"
    )
    assert "privacidadExportaYBorraMemoriaConBearer" in tests
