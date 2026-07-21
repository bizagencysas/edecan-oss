"""Tests de `approval.default_approver` (nunca toca la terminal real: `input` se monkeypatchea)."""

from __future__ import annotations

import logging
import time

import pytest
from edecan_companion import approval


async def test_auto_approve_skips_the_prompt_entirely(companion_config, monkeypatch):
    companion_config.auto_approve.append("read_dir")

    def _fail_if_called(prompt=""):
        raise AssertionError("no debería preguntar: la acción está en auto_approve")

    monkeypatch.setattr("builtins.input", _fail_if_called)

    approved = await approval.default_approver("read_dir", {}, companion_config)
    assert approved is True


async def test_prompt_rejects_on_explicit_no(companion_config, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")

    approved = await approval.default_approver("run_command", {"command": "ls"}, companion_config)
    assert approved is False


async def test_prompt_approves_on_explicit_yes(companion_config, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")

    approved = await approval.default_approver("run_command", {"command": "ls"}, companion_config)
    assert approved is True


async def test_prompt_rejects_on_empty_answer(companion_config, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt="": "")

    approved = await approval.default_approver("read_dir", {}, companion_config)
    assert approved is False


async def test_prompt_is_case_and_accent_insensitive_for_yes(companion_config, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt="": "  SÍ  ")

    approved = await approval.default_approver("read_dir", {}, companion_config)
    assert approved is True


async def test_action_not_in_auto_approve_still_prompts(companion_config, monkeypatch):
    companion_config.auto_approve.append("read_dir")  # otra acción, no "run_command"
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")

    approved = await approval.default_approver("run_command", {"command": "ls"}, companion_config)
    assert approved is False


async def test_trash_path_ignores_auto_approve_and_approval_memory(companion_config, monkeypatch):
    companion_config.auto_approve.append("trash_path")
    companion_config.remember_approvals_minutes = 10
    prompts: list[str] = []

    def _approve(prompt=""):
        prompts.append(prompt)
        return "y"

    monkeypatch.setattr("builtins.input", _approve)
    assert await approval.default_approver("trash_path", {"path": "a.txt"}, companion_config)
    assert await approval.default_approver("trash_path", {"path": "a.txt"}, companion_config)
    assert len(prompts) == 2
    assert all("PAPELERA" in prompt for prompt in prompts)


async def test_prompt_times_out_and_rejects(companion_config, monkeypatch):
    def _never_returns_in_time(prompt=""):
        time.sleep(0.3)
        return "y"

    monkeypatch.setattr("builtins.input", _never_returns_in_time)

    approved = await approval.default_approver("read_dir", {}, companion_config, timeout=0.05)
    assert approved is False


# -- remember_approvals_minutes ----------------------------------------------
#
# `remember_approvals_minutes` está apagado (0) por defecto: los tests de
# arriba (que nunca lo tocan) ya prueban que sin él SIEMPRE se pregunta, cada
# vez, incluso para la misma acción repetida -- eso es lo que valida
# `test_action_not_in_auto_approve_still_prompts` en espíritu. Lo que sigue
# prueba el comportamiento específico de recordar/expirar/nunca-recordar-un-no.


async def test_remember_approvals_reuses_without_prompting_again(companion_config, monkeypatch):
    companion_config.remember_approvals_minutes = 5
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")

    first = await approval.default_approver("apply_edit", {}, companion_config)
    assert first is True

    def _fail_if_called(prompt=""):
        raise AssertionError("no debería preguntar: la aprobación sigue recordada")

    monkeypatch.setattr("builtins.input", _fail_if_called)

    second = await approval.default_approver("apply_edit", {}, companion_config)
    assert second is True


async def test_remember_approvals_off_by_default_always_prompts_again(
    companion_config, monkeypatch
):
    assert companion_config.remember_approvals_minutes == 0  # default del fixture/dataclass
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    await approval.default_approver("apply_edit", {}, companion_config)

    calls = []

    def _spy_input(prompt=""):
        calls.append(prompt)
        return "y"

    monkeypatch.setattr("builtins.input", _spy_input)
    approved = await approval.default_approver("apply_edit", {}, companion_config)

    assert approved is True
    assert len(calls) == 1  # sin remember_approvals_minutes, vuelve a preguntar


async def test_remember_approvals_expires_after_the_configured_minutes(
    companion_config, monkeypatch
):
    companion_config.remember_approvals_minutes = 1
    fake_now = {"t": 1_000.0}
    monkeypatch.setattr(approval.time, "monotonic", lambda: fake_now["t"])

    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    first = await approval.default_approver("apply_edit", {}, companion_config)
    assert first is True

    fake_now["t"] += 61  # pasó más de 1 minuto: la aprobación recordada expiró

    calls = []

    def _spy_input(prompt=""):
        calls.append(prompt)
        return "y"

    monkeypatch.setattr("builtins.input", _spy_input)
    second = await approval.default_approver("apply_edit", {}, companion_config)

    assert second is True
    assert len(calls) == 1  # tuvo que volver a preguntar: ya había expirado


async def test_remember_approvals_still_valid_just_before_expiring(companion_config, monkeypatch):
    companion_config.remember_approvals_minutes = 1
    fake_now = {"t": 1_000.0}
    monkeypatch.setattr(approval.time, "monotonic", lambda: fake_now["t"])

    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    await approval.default_approver("apply_edit", {}, companion_config)

    fake_now["t"] += 59  # todavía dentro de la ventana de 1 minuto

    def _fail_if_called(prompt=""):
        raise AssertionError("no debería preguntar: todavía no expiró")

    monkeypatch.setattr("builtins.input", _fail_if_called)
    approved = await approval.default_approver("apply_edit", {}, companion_config)

    assert approved is True


async def test_denied_approval_is_never_remembered(companion_config, monkeypatch):
    companion_config.remember_approvals_minutes = 5
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")

    first = await approval.default_approver("apply_edit", {}, companion_config)
    assert first is False

    calls = []

    def _spy_input(prompt=""):
        calls.append(prompt)
        return "y"

    monkeypatch.setattr("builtins.input", _spy_input)
    second = await approval.default_approver("apply_edit", {}, companion_config)

    assert second is True
    assert len(calls) == 1  # el rechazo anterior no se recordó: volvió a preguntar


async def test_remember_approvals_is_scoped_per_action(companion_config, monkeypatch):
    companion_config.remember_approvals_minutes = 5
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    await approval.default_approver("apply_edit", {}, companion_config)

    calls = []

    def _spy_input(prompt=""):
        calls.append(prompt)
        return "y"

    monkeypatch.setattr("builtins.input", _spy_input)
    # Una acción DISTINTA no hereda la aprobación recordada de "apply_edit".
    approved = await approval.default_approver("run_command", {}, companion_config)

    assert approved is True
    assert len(calls) == 1


async def test_remember_approval_reuse_is_logged(companion_config, monkeypatch, caplog):
    companion_config.remember_approvals_minutes = 5
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    await approval.default_approver("apply_edit", {}, companion_config)

    def _fail_if_called(prompt=""):
        raise AssertionError("no debería preguntar")

    monkeypatch.setattr("builtins.input", _fail_if_called)

    with caplog.at_level(logging.INFO, logger="edecan_companion.approval"):
        approved = await approval.default_approver("apply_edit", {}, companion_config)

    assert approved is True
    assert any("recordada" in record.message for record in caplog.records)


# -- input_pointer/input_key: regla "más dura" (WP-V4-10, control remoto) ----
#
# Nunca pasan por `auto_approve`, y su "recordado" usa
# `remote_input_remember_minutes` (no `remember_approvals_minutes`) Y queda
# acotado a `params["session_id"]` -- nunca se hereda entre sesiones, ni
# siquiera dentro de la ventana de minutos.


@pytest.mark.parametrize("action_name", ["input_pointer", "input_key"])
async def test_input_actions_ignore_auto_approve_and_still_prompt(
    companion_config, monkeypatch, action_name
):
    companion_config.auto_approve.append(action_name)
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")

    approved = await approval.default_approver(
        action_name, {"session_id": "s1"}, companion_config
    )

    assert approved is True  # sí aprobó, pero PORQUE preguntó y dijeron que sí


async def test_input_action_without_session_id_never_remembers(companion_config, monkeypatch):
    companion_config.remote_input_remember_minutes = 10
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")

    await approval.default_approver("input_pointer", {}, companion_config)  # sin session_id

    calls = []

    def _spy_input(prompt=""):
        calls.append(prompt)
        return "y"

    monkeypatch.setattr("builtins.input", _spy_input)
    approved = await approval.default_approver("input_pointer", {}, companion_config)

    assert approved is True
    assert len(calls) == 1  # volvió a preguntar: sin session_id nunca se recuerda


async def test_input_action_remembers_within_the_same_session(companion_config, monkeypatch):
    companion_config.remote_input_remember_minutes = 10
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")

    first = await approval.default_approver(
        "input_pointer", {"session_id": "sesion-A"}, companion_config
    )
    assert first is True

    def _fail_if_called(prompt=""):
        raise AssertionError("no debería preguntar: misma sesión, dentro de la ventana")

    monkeypatch.setattr("builtins.input", _fail_if_called)

    second = await approval.default_approver(
        "input_pointer", {"session_id": "sesion-A"}, companion_config
    )
    assert second is True


async def test_input_action_never_inherits_approval_from_a_different_session(
    companion_config, monkeypatch
):
    companion_config.remote_input_remember_minutes = 10
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")

    await approval.default_approver("input_pointer", {"session_id": "sesion-A"}, companion_config)

    calls = []

    def _spy_input(prompt=""):
        calls.append(prompt)
        return "y"

    monkeypatch.setattr("builtins.input", _spy_input)
    approved = await approval.default_approver(
        "input_pointer", {"session_id": "sesion-B"}, companion_config
    )

    assert approved is True
    assert len(calls) == 1  # sesión distinta: NUNCA hereda la aprobación de "sesion-A"


async def test_input_action_remember_expires_after_remote_input_remember_minutes(
    companion_config, monkeypatch
):
    companion_config.remote_input_remember_minutes = 1
    companion_config.remember_approvals_minutes = 999  # el general NO debe aplicar aquí
    fake_now = {"t": 1_000.0}
    monkeypatch.setattr(approval.time, "monotonic", lambda: fake_now["t"])
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")

    await approval.default_approver("input_pointer", {"session_id": "s1"}, companion_config)

    fake_now["t"] += 61  # pasó más de remote_input_remember_minutes (1)

    calls = []

    def _spy_input(prompt=""):
        calls.append(prompt)
        return "y"

    monkeypatch.setattr("builtins.input", _spy_input)
    approved = await approval.default_approver(
        "input_pointer", {"session_id": "s1"}, companion_config
    )

    assert approved is True
    assert len(calls) == 1  # expiró según el tope DURO de input, no el general (999 min)


async def test_input_action_denial_is_never_remembered(companion_config, monkeypatch):
    companion_config.remote_input_remember_minutes = 10
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")

    first = await approval.default_approver(
        "input_key", {"session_id": "s1"}, companion_config
    )
    assert first is False

    calls = []

    def _spy_input(prompt=""):
        calls.append(prompt)
        return "y"

    monkeypatch.setattr("builtins.input", _spy_input)
    second = await approval.default_approver("input_key", {"session_id": "s1"}, companion_config)

    assert second is True
    assert len(calls) == 1  # el rechazo anterior no se recordó: volvió a preguntar


async def test_input_action_remember_is_scoped_per_action_within_the_same_session(
    companion_config, monkeypatch
):
    companion_config.remote_input_remember_minutes = 10
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")

    await approval.default_approver("input_pointer", {"session_id": "s1"}, companion_config)

    calls = []

    def _spy_input(prompt=""):
        calls.append(prompt)
        return "y"

    monkeypatch.setattr("builtins.input", _spy_input)
    # "input_key" es una acción DISTINTA de "input_pointer": no hereda su
    # aprobación recordada aunque sea la misma sesión.
    approved = await approval.default_approver("input_key", {"session_id": "s1"}, companion_config)

    assert approved is True
    assert len(calls) == 1


async def test_input_action_remember_off_when_remote_input_remember_minutes_is_zero(
    companion_config, monkeypatch
):
    companion_config.remote_input_remember_minutes = 0
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")

    await approval.default_approver("input_pointer", {"session_id": "s1"}, companion_config)

    calls = []

    def _spy_input(prompt=""):
        calls.append(prompt)
        return "y"

    monkeypatch.setattr("builtins.input", _spy_input)
    approved = await approval.default_approver(
        "input_pointer", {"session_id": "s1"}, companion_config
    )

    assert approved is True
    assert len(calls) == 1  # 0 = nunca se recuerda, siempre pregunta


async def test_input_action_prompt_mentions_control_remoto(companion_config, monkeypatch):
    seen_prompts = []

    def _spy_input(prompt=""):
        seen_prompts.append(prompt)
        return "y"

    monkeypatch.setattr("builtins.input", _spy_input)
    await approval.default_approver("input_pointer", {"session_id": "s1"}, companion_config)

    assert len(seen_prompts) == 1
    assert "CONTROL REMOTO" in seen_prompts[0]
