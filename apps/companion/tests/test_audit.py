"""Tests de `edecan_companion.audit` (bitácora JSONL, siempre en tmp_path)."""

from __future__ import annotations

import json

from edecan_companion import audit


def test_log_action_appends_one_jsonl_line_per_call(tmp_path):
    log_path = tmp_path / "companion.log"

    audit.log_action(
        action="read_dir", params={"path": "."}, approved=True, ok=True, log_path=log_path
    )
    audit.log_action(
        action="read_dir", params={"path": "sub"}, approved=True, ok=True, log_path=log_path
    )

    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["action"] == "read_dir"
    assert first["approved"] is True
    assert first["ok"] is True
    assert "timestamp" in first


def test_log_action_creates_parent_directories(tmp_path):
    log_path = tmp_path / "nested" / "dir" / "companion.log"
    audit.log_action(action="x", params={}, approved=False, ok=False, log_path=log_path)
    assert log_path.exists()


def test_log_action_never_raises_when_log_path_is_unwritable(tmp_path):
    # log_path cuyo padre es en realidad un ARCHIVO (no una carpeta): mkdir
    # falla, y log_action debe tragarse el error, no propagarlo.
    blocking_file = tmp_path / "not_a_dir"
    blocking_file.write_text("x")
    log_path = blocking_file / "companion.log"

    audit.log_action(action="x", params={}, approved=True, ok=True, log_path=log_path)  # no lanza


def test_sanitize_params_redacts_content_and_text_but_keeps_other_keys():
    sanitized = audit.sanitize_params(
        {"path": "a.txt", "content": "informacion sensible", "text": "otro secreto"}
    )
    assert sanitized["path"] == "a.txt"
    assert "informacion sensible" not in sanitized["content"]
    assert "otro secreto" not in sanitized["text"]
    assert "caracteres" in sanitized["content"]


def test_sanitize_params_handles_none():
    assert audit.sanitize_params(None) == {}


def test_log_action_output_never_contains_raw_sensitive_content(tmp_path):
    log_path = tmp_path / "companion.log"
    secret = "informacion-muy-secreta-que-no-deberia-quedar-en-el-log"

    audit.log_action(
        action="write_file",
        params={"path": "a.txt", "content": secret},
        approved=True,
        ok=True,
        log_path=log_path,
    )

    assert secret not in log_path.read_text(encoding="utf-8")
