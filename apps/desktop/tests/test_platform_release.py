from __future__ import annotations

import json
import stat
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
TAURI_DIR = REPO_ROOT / "apps" / "desktop" / "src-tauri"


def _config(name: str) -> dict[str, object]:
    return json.loads((TAURI_DIR / name).read_text(encoding="utf-8"))


def test_bundle_targets_are_native_and_platform_specific() -> None:
    base = _config("tauri.conf.json")
    macos = _config("tauri.macos.conf.json")
    windows = _config("tauri.windows.conf.json")
    linux = _config("tauri.linux.conf.json")

    assert "targets" not in base["bundle"]
    assert macos["bundle"]["targets"] == ["app", "dmg"]
    assert windows["bundle"]["targets"] == ["nsis", "msi"]
    assert linux["bundle"]["targets"] == ["appimage", "deb", "rpm"]
    assert linux["bundle"]["linux"]["appimage"]["bundleMediaFramework"] is True


def test_linux_release_builds_and_exercises_the_packaged_application() -> None:
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    linux_job = workflow.split("  desktop-linux:", 1)[1].split("\n  desktop-windows:", 1)[0]

    assert "desktop-linux:" in workflow
    assert "TAURI_CONFIG: '{\"bundle\":{\"externalBin\":[]}}'" in linux_job
    assert "./apps/desktop/scripts/build-app.sh" in workflow
    assert "./apps/desktop/scripts/verify-linux-bundles.sh" in workflow
    assert "apps/desktop/src-tauri/target/release/bundle/appimage/*.AppImage" in workflow
    assert "apps/desktop/src-tauri/target/release/bundle/deb/*.deb" in workflow
    assert "apps/desktop/src-tauri/target/release/bundle/rpm/*.rpm" in workflow
    for dependency in ("dbus-x11", "openbox", "wmctrl"):
        assert dependency in workflow


def test_windows_release_builds_installs_and_exercises_native_packages() -> None:
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    windows_job = workflow.split("  desktop-windows:", 1)[1].split("\n  ios:", 1)[0]
    verifier = (
        REPO_ROOT / "apps" / "desktop" / "scripts" / "verify-windows-bundles.ps1"
    ).read_text(encoding="utf-8")

    assert "desktop-windows:" in workflow
    assert "TAURI_CONFIG: '{\"bundle\":{\"externalBin\":[]}}'" in windows_job
    assert "runs-on: windows-2025" in workflow
    assert ".\\apps\\desktop\\scripts\\build-app.ps1" in workflow
    assert ".\\apps\\desktop\\scripts\\verify-windows-bundles.ps1" in workflow
    assert "apps/desktop/src-tauri/target/release/bundle/nsis/*.exe" in workflow
    assert "apps/desktop/src-tauri/target/release/bundle/msi/*.msi" in workflow
    assert "msiexec.exe" in verifier
    assert '"/a"' in verifier
    assert "$env:RUNNER_TEMP" in verifier
    assert '"/L*V"' in verifier
    assert '"/S"' in verifier
    assert '"/D=$NsisInstall"' in verifier
    assert '/D=`"$NsisInstall`"' not in verifier
    assert "Assert-InstalledPayload" in verifier
    assert '"http://127.0.0.1:$Port/healthz"' in verifier
    assert "CloseMainWindow()" in verifier
    assert 'ArgumentList "--exit-on-close"' in verifier
    assert "Get-SmokeProcesses" in verifier


def test_both_native_smokes_require_the_complete_fydesign_payload() -> None:
    linux = (
        REPO_ROOT / "apps" / "desktop" / "scripts" / "verify-linux-bundles.sh"
    ).read_text(encoding="utf-8")
    windows = (
        REPO_ROOT / "apps" / "desktop" / "scripts" / "verify-windows-bundles.ps1"
    ).read_text(encoding="utf-8")

    for artifact in (
        "fydesign-node",
        "fydesign-mcp.mjs",
        "ffmpeg",
        "ffprobe",
        "yt-dlp",
        "playwright-browsers",
    ):
        assert artifact in linux
        assert artifact in windows


def test_windows_kills_the_process_tree_before_the_pyinstaller_parent() -> None:
    backend = (TAURI_DIR / "src" / "backend.rs").read_text(encoding="utf-8")
    windows_tree_kill = backend.index('Command::new("taskkill")')
    generic_child_kill = backend.index("child.kill()", windows_tree_kill)

    assert windows_tree_kill < generic_child_kill
    assert '.args(["/F", "/T", "/PID"' in backend
    assert ".status()" in backend[windows_tree_kill:generic_child_kill]


def test_release_shell_scripts_are_executable() -> None:
    for name in ("build-app.sh", "build-backend.sh", "verify-linux-bundles.sh"):
        mode = (REPO_ROOT / "apps" / "desktop" / "scripts" / name).stat().st_mode
        assert mode & stat.S_IXUSR, f"{name} must be executable in a source checkout"


def test_macos_installer_keeps_one_stably_signed_canonical_application() -> None:
    installer = (
        REPO_ROOT / "apps" / "desktop" / "scripts" / "install-macos.sh"
    ).read_text(encoding="utf-8")
    builder = (
        REPO_ROOT / "apps" / "desktop" / "scripts" / "build-app.sh"
    ).read_text(encoding="utf-8")

    assert 'TARGET_APP="/Applications/Edecán.app"' in installer
    assert 'TARGET_APP="$HOME/Applications/Edecán.app"' in installer
    assert "EDECAN_INSTALL_PATH" in installer
    assert "EDECAN_MACOS_CODESIGN_IDENTITY" in installer
    assert "codesign_authority" in installer
    assert "codesign --verify --deep --strict" in installer
    assert 'identifier cc.edecan.desktop' in installer
    assert 'Contents/MacOS/edecan-local' in installer
    assert "migrate_macos_autostart" in installer
    assert 'Set :ProgramArguments:0 $executable' in installer
    assert 'launchctl bootout "$gui_domain"' in installer
    assert 'launchctl bootstrap "$gui_domain"' in installer
    assert 'BACKUP_ARCHIVE="$HOME/.Trash/Edecán anterior ' in installer
    assert "BACKUP_APP=" not in installer
    assert "EDECAN_MACOS_CODESIGN_IDENTITY" in builder


def test_ios_chat_dismisses_the_keyboard_without_a_listo_accessory_bar() -> None:
    chat_view = (
        REPO_ROOT / "apps" / "mobile" / "ios" / "EdecanApp" / "Screens" / "ChatView.swift"
    ).read_text(encoding="utf-8")

    assert 'ToolbarItemGroup(placement: .keyboard)' not in chat_view
    assert 'Button("Listo")' not in chat_view
    assert ".scrollDismissesKeyboard(.interactively)" in chat_view
    assert ".onTapGesture" in chat_view
    assert "campoEnfocado = false" in chat_view


def test_ios_remote_control_focuses_on_current_session_without_history_panel() -> None:
    root = Path(__file__).resolve().parents[3]
    view = (root / "apps/mobile/ios/EdecanApp/Screens/RemotoView.swift").read_text()
    model = (
        root / "apps/mobile/ios/EdecanApp/Componentes/RemotoViewModel.swift"
    ).read_text()

    assert "Historial de sesiones" not in view
    assert "FilaSesionRemotaHistorial" not in view
    assert "cargarHistorial" not in model


def test_linux_sidecar_preserves_postgres_runtime_modules() -> None:
    spec = (REPO_ROOT / "apps" / "desktop" / "packaging" / "edecan_local.spec").read_text(
        encoding="utf-8"
    )
    build_script = (
        REPO_ROOT / "apps" / "desktop" / "scripts" / "build-backend.sh"
    ).read_text(encoding="utf-8")

    assert 'sys.platform.startswith("linux")' in spec
    assert '_postgres_module_dest = "pgserver/pginstall/lib/postgresql"' in spec
    for module in ("dict_snowball.so", "vector.so"):
        assert module in spec
        assert f"pgserver/pginstall/lib/postgresql/{module}" in build_script


def test_sidecar_preserves_workspace_tool_entry_points() -> None:
    spec = (REPO_ROOT / "apps" / "desktop" / "packaging" / "edecan_local.spec").read_text(
        encoding="utf-8"
    )

    assert "copy_metadata" in spec
    assert 'distribution_name = pkg.replace("_", "-")' in spec
    assert "datas.extend(copy_metadata(distribution_name))" in spec
    assert 'if pkg.startswith("edecan_")' in spec


def test_linux_smoke_uses_a_real_window_manager_and_waits_for_main_window() -> None:
    verify_script = (
        REPO_ROOT / "apps" / "desktop" / "scripts" / "verify-linux-bundles.sh"
    ).read_text(encoding="utf-8")

    assert 'printf "%s\\n" "$XAUTHORITY"' in verify_script
    assert 'export XAUTHORITY' in verify_script
    assert "dbus-run-session" in verify_script
    assert "openbox --sm-disable" in verify_script
    assert "wmctrl -l" in verify_script
    assert "{ wmctrl -l 2>/dev/null || true; }" in verify_script
    assert 'wmctrl -ic "$WINDOW_ID"' in verify_script
    assert 'exec "$3" --exit-on-close' in verify_script
    assert "xdotool search --name" not in verify_script
    assert 'SPLASH_WINDOW_ID=""' in verify_script
    assert '"$candidate" != "$SPLASH_WINDOW_ID"' in verify_script
    assert '(edecan-local|postgres).*$SMOKE_DIR' in verify_script
    assert 'LAUNCHER_STATUS="$?"' in verify_script


def test_all_desktop_platforms_stay_resident_for_mobile_access() -> None:
    source = (TAURI_DIR / "src" / "lib.rs").read_text(encoding="utf-8")

    assert 'argument == "--exit-on-close"' in source
    assert "let keep_resident = !exit_on_close;" in source
    assert 'cfg!(target_os = "macos") || listen::is_enabled' not in source


def test_linux_is_documented_as_a_first_class_desktop_target() -> None:
    desktop_guide = (REPO_ROOT / "docs" / "desktop.md").read_text(encoding="utf-8")
    desktop_readme = (REPO_ROOT / "apps" / "desktop" / "README.md").read_text(
        encoding="utf-8"
    )

    assert "AppImage" in desktop_guide
    assert "paquete `.deb`" in desktop_guide
    assert "AppImage" in desktop_readme
    assert "no publica hoy un bundle Tauri para Linux" not in desktop_guide
    assert "no publica hoy un bundle Tauri para Linux" not in desktop_readme
    assert "una build o un smoke test ejecutado en macOS" in desktop_readme


def test_windows_and_linux_native_release_gates_are_documented() -> None:
    desktop_guide = (REPO_ROOT / "docs" / "desktop.md").read_text(encoding="utf-8")
    desktop_readme = (REPO_ROOT / "apps" / "desktop" / "README.md").read_text(
        encoding="utf-8"
    )

    for documentation in (desktop_guide, desktop_readme):
        assert "verify-windows-bundles.ps1" in documentation
        assert "verify-linux-bundles.sh" in documentation
        assert "Windows x64" in documentation
        assert "Linux x64" in documentation
