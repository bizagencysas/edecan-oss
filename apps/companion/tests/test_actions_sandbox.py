"""Tests de las acciones de archivos dentro del sandbox (ARCHITECTURE.md §10.12).

Todo corre contra `companion_config` (fixture de `conftest.py`, en tmp_path):
nunca toca el sandbox real del usuario.
"""

from __future__ import annotations

import base64

import pytest
from edecan_companion import actions


def test_path_traversal_is_rejected(companion_config):
    with pytest.raises(actions.ActionError, match="fuera del sandbox"):
        actions._resolve_in_sandbox(companion_config, "../../etc/passwd")


def test_absolute_path_is_treated_as_relative_to_sandbox_not_rejected(companion_config):
    # Una ruta que "parece" absoluta nunca se interpreta como tal: se
    # reinterpreta como relativa al sandbox, así que queda DENTRO de él.
    resolved = actions._resolve_in_sandbox(companion_config, "/etc/passwd")
    resolved.relative_to(companion_config.sandbox_dir)  # no lanza


def test_symlink_escaping_sandbox_is_rejected(companion_config, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("no deberías poder leer esto")
    (companion_config.sandbox_dir / "escape").symlink_to(outside)

    with pytest.raises(actions.ActionError, match="fuera del sandbox"):
        actions._resolve_in_sandbox(companion_config, "escape/secret.txt")


def test_write_then_read_file_roundtrip_inside_sandbox(companion_config):
    write_result = actions._write_file(
        {"path": "notas/hola.txt", "content": "hola mundo"}, companion_config
    )
    assert write_result["path"] == "notas/hola.txt"
    assert write_result["bytes_written"] == len(b"hola mundo")

    read_result = actions._read_file({"path": "notas/hola.txt"}, companion_config)
    assert read_result["content"] == "hola mundo"
    assert read_result["encoding"] == "utf-8"

    on_disk = companion_config.sandbox_dir / "notas" / "hola.txt"
    assert on_disk.read_text(encoding="utf-8") == "hola mundo"


def test_write_file_creates_parent_dirs_within_sandbox(companion_config):
    actions._write_file({"path": "a/b/c/d.txt", "content": "x"}, companion_config)
    assert (companion_config.sandbox_dir / "a" / "b" / "c" / "d.txt").exists()


def test_write_file_rejects_traversal_path(companion_config):
    with pytest.raises(actions.ActionError, match="fuera del sandbox"):
        actions._write_file({"path": "../escape.txt", "content": "x"}, companion_config)


def test_write_file_requires_content_param(companion_config):
    with pytest.raises(actions.ActionError, match="content"):
        actions._write_file({"path": "a.txt"}, companion_config)


def test_write_file_supports_base64_encoding(companion_config):
    raw = bytes(range(4))
    actions._write_file(
        {"path": "bin.dat", "content": base64.b64encode(raw).decode("ascii"), "encoding": "base64"},
        companion_config,
    )
    assert (companion_config.sandbox_dir / "bin.dat").read_bytes() == raw


def test_trash_path_uses_recoverable_system_trash(companion_config, monkeypatch):
    target = companion_config.sandbox_dir / "borrador.txt"
    target.write_text("recuperable", encoding="utf-8")
    calls: list[str] = []
    monkeypatch.setattr("send2trash.send2trash", lambda path: calls.append(path))

    result = actions._trash_path({"path": "borrador.txt"}, companion_config)

    assert calls == [str(target)]
    assert result == {"path": "borrador.txt", "trashed": True}


def test_trash_path_never_accepts_the_sandbox_root(companion_config):
    with pytest.raises(actions.ActionError, match="raíz completa"):
        actions._trash_path({}, companion_config)


def test_read_file_rejects_files_over_max_size(companion_config):
    big = companion_config.sandbox_dir / "big.bin"
    big.write_bytes(b"a" * (actions.MAX_READ_FILE_BYTES + 1))

    with pytest.raises(actions.ActionError, match="grande"):
        actions._read_file({"path": "big.bin"}, companion_config)


def test_read_file_returns_base64_for_binary_content(companion_config):
    binary_path = companion_config.sandbox_dir / "img.bin"
    raw = bytes(range(256))
    binary_path.write_bytes(raw)

    result = actions._read_file({"path": "img.bin"}, companion_config)

    assert result["encoding"] == "base64"
    assert base64.b64decode(result["content"]) == raw


def test_read_file_missing_raises(companion_config):
    with pytest.raises(actions.ActionError, match="no existe"):
        actions._read_file({"path": "no-existe.txt"}, companion_config)


def test_read_dir_lists_entries_of_sandbox_root(companion_config):
    (companion_config.sandbox_dir / "file.txt").write_text("x")
    (companion_config.sandbox_dir / "subdir").mkdir()

    result = actions._read_dir({}, companion_config)

    names = {entry["name"] for entry in result["entries"]}
    assert names == {"file.txt", "subdir"}
    by_name = {entry["name"]: entry for entry in result["entries"]}
    assert by_name["subdir"]["is_dir"] is True
    assert by_name["file.txt"]["is_dir"] is False
    assert by_name["file.txt"]["size_bytes"] == 1


def test_read_dir_missing_path_raises(companion_config):
    with pytest.raises(actions.ActionError, match="no existe"):
        actions._read_dir({"path": "no-existe"}, companion_config)


def test_read_dir_rejects_a_file_path(companion_config):
    (companion_config.sandbox_dir / "file.txt").write_text("x")
    with pytest.raises(actions.ActionError, match="no es una carpeta"):
        actions._read_dir({"path": "file.txt"}, companion_config)
