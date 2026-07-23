"""Tests de las acciones nuevas del IDE embebido (ARCHITECTURE.md §10.12,
ROADMAP_V2.md §7.8, WP-V2-08): `list_tree`, `search_files`, `apply_edit`,
`screenshot`, y el gate `ide_enabled` en `actions.execute`.

Todo corre contra `companion_config` (fixture de `conftest.py`, en
`tmp_path`): nunca toca el sandbox real del usuario ni la pantalla real.
"""

from __future__ import annotations

import base64
import io
from pathlib import Path

import pytest
from edecan_companion import actions

# ---------------------------------------------------------------------------
# _clamp_int (helper)
# ---------------------------------------------------------------------------


def test_clamp_int_returns_default_when_missing():
    assert actions._clamp_int(None, default=5, minimum=1, maximum=10) == 5


def test_clamp_int_caps_values_above_the_maximum():
    assert actions._clamp_int(999, default=5, minimum=1, maximum=10) == 10


def test_clamp_int_floors_values_below_the_minimum():
    assert actions._clamp_int(-3, default=5, minimum=1, maximum=10) == 1


def test_clamp_int_falls_back_to_default_on_invalid_type():
    assert actions._clamp_int("no-es-un-entero", default=5, minimum=1, maximum=10) == 5


def test_clamp_int_passes_through_a_value_inside_the_range():
    assert actions._clamp_int(7, default=5, minimum=1, maximum=10) == 7


# ---------------------------------------------------------------------------
# list_tree
# ---------------------------------------------------------------------------


def test_list_tree_lists_dirs_before_files_sorted_by_name(companion_config):
    root = companion_config.sandbox_dir
    (root / "b.txt").write_text("b")
    (root / "a.txt").write_text("a")
    (root / "zdir").mkdir()
    (root / "adir").mkdir()

    result = actions._list_tree({}, companion_config)

    names = [e["name"] for e in result["entries"]]
    assert names == ["adir", "zdir", "a.txt", "b.txt"]
    assert result["truncated"] is False


def test_list_tree_marks_is_dir_and_reports_file_size(companion_config):
    root = companion_config.sandbox_dir
    (root / "sub").mkdir()
    (root / "f.txt").write_text("hola")

    result = actions._list_tree({}, companion_config)
    by_name = {e["name"]: e for e in result["entries"]}

    assert by_name["sub"]["is_dir"] is True
    assert by_name["f.txt"]["is_dir"] is False
    assert by_name["f.txt"]["size_bytes"] == 4


def test_list_tree_recurses_into_subdirectories(companion_config):
    root = companion_config.sandbox_dir
    (root / "sub").mkdir()
    (root / "sub" / "nested.txt").write_text("x")

    result = actions._list_tree({}, companion_config)

    sub = next(e for e in result["entries"] if e["name"] == "sub")
    assert sub["children"] is not None
    assert [c["name"] for c in sub["children"]] == ["nested.txt"]


def test_list_tree_ignores_git_node_modules_pycache_and_venv(companion_config):
    root = companion_config.sandbox_dir
    for ignored in (".git", "node_modules", "__pycache__", ".venv"):
        (root / ignored).mkdir()
        (root / ignored / "x.txt").write_text("x")
    (root / "src").mkdir()

    result = actions._list_tree({}, companion_config)

    assert {e["name"] for e in result["entries"]} == {"src"}


def test_list_tree_respects_max_depth(companion_config, monkeypatch):
    monkeypatch.setattr(actions, "MAX_TREE_DEPTH", 2)
    root = companion_config.sandbox_dir
    (root / "l1").mkdir()
    (root / "l1" / "l2").mkdir()
    (root / "l1" / "l2" / "l3").mkdir()

    result = actions._list_tree({}, companion_config)

    l1 = next(e for e in result["entries"] if e["name"] == "l1")
    l2 = next(e for e in l1["children"] if e["name"] == "l2")
    # profundidad 2 alcanzada: "l2" se ve, pero no se expande su contenido.
    assert l2["children"] is None


def test_list_tree_respects_max_entries_and_marks_truncated(companion_config, monkeypatch):
    monkeypatch.setattr(actions, "MAX_TREE_ENTRIES", 3)
    root = companion_config.sandbox_dir
    for i in range(5):
        (root / f"f{i}.txt").write_text("x")

    result = actions._list_tree({}, companion_config)

    assert len(result["entries"]) == 3
    assert result["truncated"] is True


def test_list_tree_params_above_the_hard_caps_are_clamped_not_rejected(companion_config):
    (companion_config.sandbox_dir / "x.txt").write_text("x")

    result = actions._list_tree({"max_depth": 999, "max_entries": 999999}, companion_config)

    assert result["truncated"] is False
    assert len(result["entries"]) == 1


def test_list_tree_missing_path_raises(companion_config):
    with pytest.raises(actions.ActionError, match="no existe"):
        actions._list_tree({"path": "no-existe"}, companion_config)


def test_list_tree_rejects_a_file_path(companion_config):
    (companion_config.sandbox_dir / "f.txt").write_text("x")
    with pytest.raises(actions.ActionError, match="no es una carpeta"):
        actions._list_tree({"path": "f.txt"}, companion_config)


def test_list_tree_rejects_path_traversal(companion_config):
    with pytest.raises(actions.ActionError, match="fuera del sandbox"):
        actions._list_tree({"path": "../../etc"}, companion_config)


def test_list_tree_does_not_descend_into_symlink_that_escapes_sandbox(companion_config, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("fuera del sandbox")
    (companion_config.sandbox_dir / "escape").symlink_to(outside)

    result = actions._list_tree({}, companion_config)

    escape_entry = next(e for e in result["entries"] if e["name"] == "escape")
    # se lista el symlink (igual que read_dir ya hacía), pero NUNCA se listan
    # sus contenidos reales, que viven fuera del sandbox.
    assert escape_entry["children"] is None


# ---------------------------------------------------------------------------
# search_files
# ---------------------------------------------------------------------------


def test_search_files_finds_case_insensitive_substring_matches(companion_config):
    root = companion_config.sandbox_dir
    (root / "a.py").write_text("def Hola():\n    return 'HOLA mundo'\n")
    (root / "b.py").write_text("otra cosa\n")

    result = actions._search_files({"query": "hola"}, companion_config)

    assert result["truncated"] is False
    paths_lines = [(m["path"], m["line"]) for m in result["matches"]]
    assert ("a.py", 1) in paths_lines
    assert ("a.py", 2) in paths_lines
    assert all(m["path"] != "b.py" for m in result["matches"])


def test_search_files_truncates_long_lines(companion_config):
    long_line = ("x" * 500) + "objetivo" + ("y" * 500)
    (companion_config.sandbox_dir / "big_line.txt").write_text(long_line)

    result = actions._search_files({"query": "objetivo"}, companion_config)

    assert len(result["matches"]) == 1
    assert len(result["matches"][0]["texto"]) == actions.MAX_SEARCH_LINE_CHARS


def test_search_files_skips_binary_files(companion_config):
    (companion_config.sandbox_dir / "bin.dat").write_bytes(bytes(range(256)))
    (companion_config.sandbox_dir / "text.txt").write_text("buscar aqui")

    result = actions._search_files({"query": "buscar"}, companion_config)

    assert all(m["path"] != "bin.dat" for m in result["matches"])
    assert any(m["path"] == "text.txt" for m in result["matches"])


def test_search_files_skips_files_over_the_size_cap(companion_config, monkeypatch):
    monkeypatch.setattr(actions, "MAX_SEARCH_FILE_BYTES", 5)
    (companion_config.sandbox_dir / "grande.txt").write_text("buscar esta palabra")

    result = actions._search_files({"query": "buscar"}, companion_config)

    assert result["matches"] == []


def test_search_files_caps_matches_and_marks_truncated(companion_config, monkeypatch):
    monkeypatch.setattr(actions, "MAX_SEARCH_MATCHES", 2)
    (companion_config.sandbox_dir / "muchas.txt").write_text("buscar\nbuscar\nbuscar\n")

    result = actions._search_files({"query": "buscar"}, companion_config)

    assert len(result["matches"]) == 2
    assert result["truncated"] is True


def test_search_files_caps_files_scanned_and_marks_truncated(companion_config, monkeypatch):
    monkeypatch.setattr(actions, "MAX_SEARCH_FILES", 1)
    root = companion_config.sandbox_dir
    (root / "a.txt").write_text("buscar")
    (root / "b.txt").write_text("buscar")

    result = actions._search_files({"query": "buscar"}, companion_config)

    assert result["truncated"] is True
    assert len({m["path"] for m in result["matches"]}) <= 1


def test_search_files_ignores_git_and_node_modules(companion_config):
    root = companion_config.sandbox_dir
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("buscar")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "x.js").write_text("buscar")

    result = actions._search_files({"query": "buscar"}, companion_config)

    assert result["matches"] == []


def test_search_files_requires_query_param(companion_config):
    with pytest.raises(actions.ActionError, match="query"):
        actions._search_files({}, companion_config)


def test_search_files_missing_path_raises(companion_config):
    with pytest.raises(actions.ActionError, match="no existe"):
        actions._search_files({"query": "x", "path": "no-existe"}, companion_config)


def test_search_files_does_not_read_through_a_symlink_escaping_sandbox(companion_config, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("palabra_secreta")
    (companion_config.sandbox_dir / "link.txt").symlink_to(outside / "secret.txt")

    result = actions._search_files({"query": "palabra_secreta"}, companion_config)

    assert result["matches"] == []


# ---------------------------------------------------------------------------
# apply_edit
# ---------------------------------------------------------------------------


def test_apply_edit_replaces_the_unique_occurrence(companion_config):
    target = companion_config.sandbox_dir / "a.py"
    target.write_text("def hola():\n    return 1\n")

    result = actions._apply_edit(
        {"path": "a.py", "old_string": "return 1", "new_string": "return 2"}, companion_config
    )

    assert result["path"] == "a.py"
    assert result["replacements"] == 1
    assert target.read_text(encoding="utf-8") == "def hola():\n    return 2\n"


def test_apply_edit_rejects_old_string_not_found(companion_config):
    (companion_config.sandbox_dir / "a.py").write_text("hola")

    with pytest.raises(actions.ActionError, match="no se encontró"):
        actions._apply_edit(
            {"path": "a.py", "old_string": "adios", "new_string": "x"}, companion_config
        )


def test_apply_edit_rejects_non_unique_old_string_without_replace_all(companion_config):
    target = companion_config.sandbox_dir / "a.py"
    target.write_text("x = 1\nx = 1\n")

    with pytest.raises(actions.ActionError, match="no es único"):
        actions._apply_edit(
            {"path": "a.py", "old_string": "x = 1", "new_string": "x = 2"}, companion_config
        )

    # la edición rechazada no debe haber tocado el archivo.
    assert target.read_text(encoding="utf-8") == "x = 1\nx = 1\n"


def test_apply_edit_replace_all_replaces_every_occurrence(companion_config):
    target = companion_config.sandbox_dir / "a.py"
    target.write_text("x = 1\nx = 1\n")

    result = actions._apply_edit(
        {"path": "a.py", "old_string": "x = 1", "new_string": "x = 2", "replace_all": True},
        companion_config,
    )

    assert result["replacements"] == 2
    assert target.read_text(encoding="utf-8") == "x = 2\nx = 2\n"


def test_apply_edit_writes_atomically_via_tmp_and_rename(companion_config, monkeypatch):
    target = companion_config.sandbox_dir / "a.py"
    target.write_text("hola mundo")

    seen_tmp_paths = []
    original_replace = actions.os.replace

    def _spy_replace(src, dst):
        seen_tmp_paths.append(src)
        assert Path(src).exists()
        assert Path(src).parent == companion_config.sandbox_dir
        return original_replace(src, dst)

    monkeypatch.setattr(actions.os, "replace", _spy_replace)

    actions._apply_edit(
        {"path": "a.py", "old_string": "hola", "new_string": "adios"}, companion_config
    )

    assert len(seen_tmp_paths) == 1
    assert not Path(seen_tmp_paths[0]).exists()  # se renombró: no quedó huérfano
    assert target.read_text(encoding="utf-8") == "adios mundo"


def test_apply_edit_cleans_up_tmp_file_if_the_write_fails(companion_config, monkeypatch):
    target = companion_config.sandbox_dir / "a.py"
    target.write_text("hola")

    def _boom(src, dst):
        raise OSError("disco lleno (simulado)")

    monkeypatch.setattr(actions.os, "replace", _boom)

    with pytest.raises(OSError):
        actions._apply_edit(
            {"path": "a.py", "old_string": "hola", "new_string": "adios"}, companion_config
        )

    leftover = list(companion_config.sandbox_dir.glob(".a.py.*.tmp"))
    assert leftover == []
    assert target.read_text(encoding="utf-8") == "hola"  # el original quedó intacto


def test_apply_edit_requires_old_string_param(companion_config):
    (companion_config.sandbox_dir / "a.py").write_text("x")
    with pytest.raises(actions.ActionError, match="old_string"):
        actions._apply_edit({"path": "a.py", "new_string": "y"}, companion_config)


def test_apply_edit_requires_new_string_param(companion_config):
    (companion_config.sandbox_dir / "a.py").write_text("x")
    with pytest.raises(actions.ActionError, match="new_string"):
        actions._apply_edit({"path": "a.py", "old_string": "x"}, companion_config)


def test_apply_edit_missing_file_raises(companion_config):
    with pytest.raises(actions.ActionError, match="no existe"):
        actions._apply_edit(
            {"path": "no-existe.py", "old_string": "a", "new_string": "b"}, companion_config
        )


def test_apply_edit_rejects_path_traversal(companion_config):
    with pytest.raises(actions.ActionError, match="fuera del sandbox"):
        actions._apply_edit(
            {"path": "../escape.py", "old_string": "a", "new_string": "b"}, companion_config
        )


def test_apply_edit_rejects_files_over_the_size_cap(companion_config):
    target = companion_config.sandbox_dir / "big.bin"
    target.write_text("a" * (actions.MAX_READ_FILE_BYTES + 1))

    with pytest.raises(actions.ActionError, match="grande"):
        actions._apply_edit(
            {"path": "big.bin", "old_string": "a", "new_string": "b"}, companion_config
        )


def test_apply_edit_rejects_binary_file(companion_config):
    (companion_config.sandbox_dir / "bin.dat").write_bytes(bytes(range(256)))

    with pytest.raises(actions.ActionError, match="UTF-8"):
        actions._apply_edit(
            {"path": "bin.dat", "old_string": "a", "new_string": "b"}, companion_config
        )


# ---------------------------------------------------------------------------
# screenshot
# ---------------------------------------------------------------------------


def test_screenshot_rejects_unsupported_platforms(companion_config, monkeypatch):
    monkeypatch.setattr(actions.sys, "platform", "freebsd13")

    with pytest.raises(actions.ActionError, match="no soportada"):
        actions._screenshot({}, companion_config)


def test_screenshot_uses_complete_native_macos_capture(companion_config, monkeypatch):
    monkeypatch.setattr(actions.sys, "platform", "darwin")
    fake_png_bytes = b"\x89PNG\r\n\x1a\nfake-image-bytes"
    monkeypatch.setattr(
        actions,
        "_screenshot_via_screencapture",
        lambda params: (fake_png_bytes, 1512, 982, -1512, 0),
    )

    result = actions._screenshot({}, companion_config)

    assert base64.b64decode(result["image_b64"]) == fake_png_bytes
    assert result["width"] == 1512
    assert result["height"] == 982
    assert result["origin_x"] == -1512


def test_screenshot_passes_the_display_to_native_macos_capture(companion_config, monkeypatch):
    monkeypatch.setattr(actions.sys, "platform", "darwin")
    seen_params = []

    def fake_capture(params):
        seen_params.append(params)
        return b"x", 100, 100, 0, 0

    monkeypatch.setattr(actions, "_screenshot_via_screencapture", fake_capture)

    actions._screenshot({"display": 2}, companion_config)

    assert seen_params == [{"display": 2}]


def test_screenshot_surfaces_native_permission_failure(companion_config, monkeypatch):
    monkeypatch.setattr(actions.sys, "platform", "darwin")

    def denied(params):
        raise actions.ActionError("Autoriza Grabación de pantalla")

    monkeypatch.setattr(actions, "_screenshot_via_screencapture", denied)

    with pytest.raises(actions.ActionError, match="Grabaci"):
        actions._screenshot({}, companion_config)


def test_screenshot_rejects_invalid_display_param(companion_config, monkeypatch):
    monkeypatch.setattr(actions.sys, "platform", "darwin")

    with pytest.raises(actions.ActionError, match="display"):
        actions._screenshot_via_screencapture({"display": "no-es-numero"})


def test_native_macos_capture_includes_windows_dock_and_cursor(monkeypatch):
    from PIL import Image

    seen_command: list[str] = []

    def fake_run(command, **kwargs):
        seen_command.extend(command)
        output_path = Path(command[-1])
        Image.new("RGB", (1200, 800), color=(30, 40, 50)).save(output_path, format="PNG")
        return actions.subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(actions, "_macos_display_target", lambda params: (2, 99, -1200, 0))
    monkeypatch.setattr(actions, "_macos_screen_capture_allowed", lambda: True)
    monkeypatch.setattr(actions.subprocess, "run", fake_run)

    image_bytes, width, height, origin_x, origin_y = actions._screenshot_via_screencapture(
        {"display": 2}
    )

    assert image_bytes.startswith(b"\x89PNG")
    assert (width, height, origin_x, origin_y) == (1200, 800, -1200, 0)
    assert seen_command[:2] == ["/usr/sbin/screencapture", "-x"]
    assert seen_command[seen_command.index("-D") + 1] == "2"
    assert "-C" in seen_command


def test_native_macos_capture_can_hide_cursor(monkeypatch):
    from PIL import Image

    seen_command: list[str] = []

    def fake_run(command, **kwargs):
        seen_command.extend(command)
        Image.new("RGB", (10, 10)).save(Path(command[-1]), format="PNG")
        return actions.subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(actions, "_macos_display_target", lambda params: (1, 1, 0, 0))
    monkeypatch.setattr(actions, "_macos_screen_capture_allowed", lambda: True)
    monkeypatch.setattr(actions.subprocess, "run", fake_run)

    actions._screenshot_via_screencapture({"include_cursor": False})

    assert "-C" not in seen_command


def test_native_macos_capture_does_not_repeat_tcc_prompt_when_permission_is_off(monkeypatch):
    called = False

    def unexpected_run(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("screencapture no debe ejecutarse sin permiso")

    monkeypatch.setattr(actions, "_macos_display_target", lambda params: (1, 1, 0, 0))
    monkeypatch.setattr(actions, "_macos_screen_capture_allowed", lambda: False)
    monkeypatch.setattr(actions.subprocess, "run", unexpected_run)

    with pytest.raises(actions.ActionError, match="lista superior"):
        actions._screenshot_via_screencapture({})

    assert called is False


def test_native_macos_capture_prefers_authorized_desktop_bridge(monkeypatch):
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (900, 600), color=(1, 2, 3)).save(buffer, format="PNG")
    monkeypatch.setattr(actions, "_macos_display_target", lambda params: (1, 1, 0, 0))
    monkeypatch.setattr(
        actions,
        "_desktop_bridge_call",
        lambda action, params: {"image_b64": base64.b64encode(buffer.getvalue()).decode("ascii")},
    )
    monkeypatch.setattr(
        actions,
        "_macos_screen_capture_allowed",
        lambda: (_ for _ in ()).throw(AssertionError("no debe consultar TCC en el sidecar")),
    )

    image_bytes, width, height, origin_x, origin_y = actions._screenshot_via_screencapture({})

    assert image_bytes.startswith(b"\x89PNG")
    assert (width, height, origin_x, origin_y) == (900, 600, 0, 0)


# ---------------------------------------------------------------------------
# execute() -- gate de ide_enabled
# ---------------------------------------------------------------------------


async def test_ide_actions_are_blocked_when_ide_enabled_is_false(companion_config):
    companion_config.ide_enabled = False
    (companion_config.sandbox_dir / "f.txt").write_text("x")

    async def _fail_if_asked(action, params, config):
        raise AssertionError("no debería siquiera preguntar: ide_enabled=false")

    result = await actions.execute("list_tree", {}, companion_config, _fail_if_asked)

    assert result["ok"] is False
    assert "ide_enabled" in result["error"] or "deshabilitado" in result["error"]


async def test_non_ide_actions_are_unaffected_by_ide_enabled_false(companion_config):
    companion_config.ide_enabled = False
    (companion_config.sandbox_dir / "f.txt").write_text("x")

    async def _approve_everything(action, params, config):
        return True

    result = await actions.execute("read_dir", {}, companion_config, _approve_everything)
    assert result["ok"] is True


async def test_ide_actions_work_normally_with_the_default_ide_enabled_true(companion_config):
    assert companion_config.ide_enabled is True  # default del dataclass

    async def _approve_everything(action, params, config):
        return True

    result = await actions.execute("list_tree", {}, companion_config, _approve_everything)
    assert result["ok"] is True


@pytest.mark.parametrize("action_name", ["list_tree", "search_files", "apply_edit", "screenshot"])
def test_new_ide_actions_are_registered_in_actions_dict(action_name):
    assert callable(actions.ACTIONS[action_name])
    assert action_name in actions._IDE_ACTIONS
