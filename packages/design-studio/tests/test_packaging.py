from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_desktop_sidecar_collects_design_studio_and_entry_point_metadata() -> None:
    spec = (REPO_ROOT / "apps/desktop/packaging/edecan_local.spec").read_text(encoding="utf-8")
    assert '"edecan_design_studio"' in spec
    assert 'distribution_name = pkg.replace("_", "-")' in spec
    assert "datas.extend(copy_metadata(distribution_name))" in spec


def test_desktop_build_packages_a_self_contained_fydesign_engine() -> None:
    """El adaptador Python solo no basta en un equipo limpio.

    FyDesign es Node/TypeScript. El instalador debe construirlo de forma
    reproducible y declarar el artefacto resultante como sidecar/recurso de
    Tauri; depender del ``node`` o del checkout del desarrollador haria que el
    chat funcionase en dev y fallase despues de instalar el DMG/AppImage/NSIS.
    """

    desktop = REPO_ROOT / "apps/desktop"
    tauri_config = (desktop / "src-tauri/tauri.conf.json").read_text(encoding="utf-8")
    def studio_build_contract(pattern: str) -> str:
        candidates = [
            path.read_text(encoding="utf-8")
            for path in sorted((desktop / "scripts").glob(pattern))
            if "fydesign-engine" in path.read_text(encoding="utf-8").lower()
        ]
        assert candidates, f"ningun script {pattern} empaqueta fydesign-engine"
        return "\n".join(candidates)

    unix_build = studio_build_contract("*.sh")
    windows_build = studio_build_contract("*.ps1")

    for build in (unix_build, windows_build):
        lowered = build.lower()
        assert "fydesign-engine" in lowered
        assert "packages/fydesign-engine" in lowered.replace("\\", "/")
        assert any(
            pinned_install in lowered
            for pinned_install in ("npm ci", "pnpm install --frozen-lockfile")
        )
        assert any(
            self_contained in lowered
            for self_contained in ("--experimental-sea-config", "--compile", "node-runtime")
        )

    bundle_contract = f"{tauri_config}\n{unix_build}\n{windows_build}".lower()
    assert "fydesign-engine" in bundle_contract
    assert "externalbin" in bundle_contract or "resources" in bundle_contract

    for dependency in ("ffmpeg", "ffprobe", "yt-dlp"):
        assert dependency in unix_build.lower()
        assert dependency in windows_build.lower()
    for build in (unix_build, windows_build):
        lowered = build.lower()
        assert "--only-shell" in lowered
        assert "prune --omit=dev" in lowered
        assert "node_modules" in lowered and "@types" in lowered
        assert "delete value.devdependencies" in lowered
        assert "capabilities.md" in lowered
        assert "porting_manifest.json" in lowered
        assert "node_modules/.package-lock.json" in lowered.replace("\\", "/")
        assert "extraneous" in lowered
        assert "ls --omit=dev --depth=0" in lowered
        assert "@sparticuz" in lowered
        assert "not legally redistributable" in lowered
        assert "gpl-3.0.txt" in lowered
        assert "ffmpeg-source.txt" in lowered
        assert "ffmpeg-ffprobe-static" not in lowered
    assert 'YTDLP_VERSION="2026.06.09"'.lower() in unix_build.lower()
    assert '$YtDlpVersion = "2026.06.09"'.lower() in windows_build.lower()
    assert "sha256" in unix_build.lower()
    assert "sha256" in windows_build.lower()

    backend = (desktop / "src-tauri/src/backend.rs").read_text(encoding="utf-8")
    for runtime_key in ("FFMPEG_PATH", "FFPROBE_PATH", "YTDLP_PATH"):
        assert runtime_key in backend


def test_desktop_documents_separate_multimedia_licenses() -> None:
    notice = (
        REPO_ROOT / "packages/fydesign-engine/NOTICE"
    ).read_text(encoding="utf-8")
    assert "GPL-3.0-or-later" in notice
    assert "ffmpeg and ffprobe 8.1.2" in notice
    assert "nonfree or unredistributable" in notice
    assert "yt-dlp 2026.06.09" in notice
    assert "THIRD_PARTY_LICENSES" in notice
    assert "not relicensed" in notice

    package_lock = (
        REPO_ROOT / "packages/fydesign-engine/package-lock.json"
    ).read_text(encoding="utf-8")
    assert "ffmpeg-ffprobe-static" not in package_lock
