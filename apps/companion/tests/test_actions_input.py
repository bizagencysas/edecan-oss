"""Tests de control remoto de teclado/mouse (WP-V4-10, docs/control-remoto.md
§7): `input_pointer`, `input_key`, el backend `_QuartzInputBackend` (con un
`Quartz` FALSO inyectado en `sys.modules`, nunca el real) y el gate
`remote_input_enabled` en `actions.execute`.

Nada de este archivo mueve el mouse real, escribe texto real, ni importa el
paquete `pyobjc-framework-Quartz` de verdad -- ver `_FakeInputBackend` (doble
del `Protocol` `InputBackend`, usado para los tests de los handlers de alto
nivel) y `_FakeQuartzModule`/`_install_fake_quartz` (doble del propio módulo
`Quartz`, usado para probar `_QuartzInputBackend` en sí sin pyobjc instalado).
"""

from __future__ import annotations

import sys

import pytest
from edecan_companion import actions

# ---------------------------------------------------------------------------
# Doble del Protocol `InputBackend` -- para los tests de `_input_pointer`/
# `_input_key`/`execute()`, que no necesitan saber nada de Quartz.
# ---------------------------------------------------------------------------


class _FakeInputBackend:
    def __init__(self) -> None:
        self.moves: list[tuple[int, int]] = []
        self.clicks: list[tuple[int, int, str]] = []
        self.typed: list[str] = []
        self.pressed: list[str] = []
        self.downs: list[tuple[int, int, str]] = []
        self.ups: list[tuple[int, int, str]] = []
        self.scrolls: list[tuple[int, int]] = []
        self.shortcuts: list[tuple[str, tuple[str, ...]]] = []

    def move_pointer(self, x: int, y: int) -> None:
        self.moves.append((x, y))

    def click_pointer(self, x: int, y: int, button: str) -> None:
        self.clicks.append((x, y, button))

    def pointer_down(self, x: int, y: int, button: str) -> None:
        self.downs.append((x, y, button))

    def pointer_up(self, x: int, y: int, button: str) -> None:
        self.ups.append((x, y, button))

    def scroll_pointer(self, delta_x: int, delta_y: int) -> None:
        self.scrolls.append((delta_x, delta_y))

    def type_text(self, text: str) -> None:
        self.typed.append(text)

    def press_key(self, key: str, modifiers: tuple[str, ...] = ()) -> None:
        self.pressed.append(key)
        if modifiers:
            self.shortcuts.append((key, modifiers))


# ---------------------------------------------------------------------------
# Doble del módulo `Quartz` -- para probar `_QuartzInputBackend` sin pyobjc.
# ---------------------------------------------------------------------------


class _FakeQuartzModule:
    kCGEventMouseMoved = "moved"
    kCGEventLeftMouseDown = "left_down"
    kCGEventLeftMouseUp = "left_up"
    kCGEventRightMouseDown = "right_down"
    kCGEventRightMouseUp = "right_up"
    kCGEventOtherMouseDown = "other_down"
    kCGEventOtherMouseUp = "other_up"
    kCGMouseButtonLeft = "btn_left"
    kCGMouseButtonRight = "btn_right"
    kCGMouseButtonCenter = "btn_center"
    kCGHIDEventTap = "hid_tap"
    kCGScrollEventUnitPixel = "pixel"
    kCGEventFlagMaskCommand = 1
    kCGEventFlagMaskControl = 2
    kCGEventFlagMaskAlternate = 4
    kCGEventFlagMaskShift = 8

    def __init__(self, *, trusted: bool = True) -> None:
        self.trusted = trusted
        self.posted: list[dict] = []

    def AXIsProcessTrusted(self) -> bool:
        return self.trusted

    def CGEventCreateMouseEvent(self, source, event_type, point, button):  # noqa: ANN001
        return {"kind": "mouse", "event_type": event_type, "point": point, "button": button}

    def CGEventCreateKeyboardEvent(self, source, keycode, key_down):  # noqa: ANN001
        return {"kind": "keyboard", "keycode": keycode, "key_down": key_down, "unicode": None}

    def CGEventKeyboardSetUnicodeString(self, event, length, text):  # noqa: ANN001
        event["unicode"] = text

    def CGEventPost(self, tap, event):  # noqa: ANN001
        self.posted.append(event)

    def CGEventCreateScrollWheelEvent(self, source, unit, wheel_count, delta_y, delta_x):  # noqa: ANN001
        return {"kind": "scroll", "delta_x": delta_x, "delta_y": delta_y}

    def CGEventSetFlags(self, event, flags):  # noqa: ANN001
        event["flags"] = flags


def _install_fake_quartz(monkeypatch, *, trusted: bool = True) -> _FakeQuartzModule:
    fake = _FakeQuartzModule(trusted=trusted)
    monkeypatch.setitem(sys.modules, "Quartz", fake)
    return fake


# ---------------------------------------------------------------------------
# _QuartzInputBackend -- construcción (permiso de Accesibilidad, paquete faltante)
# ---------------------------------------------------------------------------


def test_quartz_backend_raises_when_pyobjc_not_installed(monkeypatch):
    # `sys.modules["Quartz"] = None` fuerza que `import Quartz` falle con
    # ImportError sin importar si pyobjc está instalado de verdad en esta
    # máquina (determinista en cualquier entorno, CI incluido).
    monkeypatch.setitem(sys.modules, "Quartz", None)

    with pytest.raises(actions.ActionError, match="pyobjc-framework-Quartz"):
        actions._QuartzInputBackend()


def test_quartz_backend_raises_when_accessibility_permission_not_granted(monkeypatch):
    _install_fake_quartz(monkeypatch, trusted=False)

    with pytest.raises(actions.ActionError, match="Accesibilidad"):
        actions._QuartzInputBackend()


def test_quartz_backend_constructs_when_trusted(monkeypatch):
    _install_fake_quartz(monkeypatch, trusted=True)

    backend = actions._QuartzInputBackend()  # no lanza

    assert isinstance(backend, actions._QuartzInputBackend)


# ---------------------------------------------------------------------------
# _QuartzInputBackend -- traducción a eventos CGEvent (secuencia real)
# ---------------------------------------------------------------------------


def test_quartz_backend_move_pointer_posts_a_single_moved_event(monkeypatch):
    fake = _install_fake_quartz(monkeypatch)
    backend = actions._QuartzInputBackend()

    backend.move_pointer(5, 7)

    assert len(fake.posted) == 1
    assert fake.posted[0]["event_type"] == fake.kCGEventMouseMoved
    assert fake.posted[0]["point"] == (5, 7)


def test_quartz_backend_click_pointer_posts_down_then_up(monkeypatch):
    fake = _install_fake_quartz(monkeypatch)
    backend = actions._QuartzInputBackend()

    backend.click_pointer(10, 20, "left")

    assert len(fake.posted) == 2
    assert fake.posted[0]["event_type"] == fake.kCGEventLeftMouseDown
    assert fake.posted[1]["event_type"] == fake.kCGEventLeftMouseUp
    assert fake.posted[0]["point"] == fake.posted[1]["point"] == (10, 20)


def test_quartz_backend_click_pointer_right_button_uses_right_event_types(monkeypatch):
    fake = _install_fake_quartz(monkeypatch)
    backend = actions._QuartzInputBackend()

    backend.click_pointer(1, 2, "right")

    assert [e["event_type"] for e in fake.posted] == [
        fake.kCGEventRightMouseDown,
        fake.kCGEventRightMouseUp,
    ]


def test_quartz_backend_type_text_posts_key_down_and_up_per_character(monkeypatch):
    fake = _install_fake_quartz(monkeypatch)
    backend = actions._QuartzInputBackend()

    backend.type_text("ab")

    assert len(fake.posted) == 4  # 2 caracteres * (down + up)
    assert [e["key_down"] for e in fake.posted] == [True, False, True, False]
    assert [e["unicode"] for e in fake.posted] == ["a", "a", "b", "b"]


def test_quartz_backend_press_key_uses_the_correct_virtual_keycode(monkeypatch):
    fake = _install_fake_quartz(monkeypatch)
    backend = actions._QuartzInputBackend()

    backend.press_key("enter")

    assert len(fake.posted) == 2
    assert fake.posted[0]["keycode"] == actions._SPECIAL_KEYCODES["enter"] == 36
    assert [e["key_down"] for e in fake.posted] == [True, False]


# ---------------------------------------------------------------------------
# _get_input_backend -- gate de plataforma
# ---------------------------------------------------------------------------


def test_get_input_backend_rejects_unsupported_platforms(monkeypatch):
    monkeypatch.setattr(actions.sys, "platform", "freebsd13")

    with pytest.raises(actions.ActionError, match="no está soportado"):
        actions._get_input_backend()


def test_get_input_backend_constructs_pynput_backend_on_linux(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(actions.sys, "platform", "linux")
    monkeypatch.setattr(actions, "_PynputInputBackend", lambda: sentinel)

    assert actions._get_input_backend() is sentinel


def test_get_input_backend_constructs_quartz_backend_on_darwin(monkeypatch):
    monkeypatch.setattr(actions.sys, "platform", "darwin")
    _install_fake_quartz(monkeypatch)

    backend = actions._get_input_backend()

    assert isinstance(backend, actions._QuartzInputBackend)


# ---------------------------------------------------------------------------
# _input_pointer (con _FakeInputBackend inyectado vía monkeypatch)
# ---------------------------------------------------------------------------


def test_input_pointer_move_only_moves_and_never_clicks(companion_config, monkeypatch):
    fake = _FakeInputBackend()
    monkeypatch.setattr(actions, "_get_input_backend", lambda: fake)

    result = actions._input_pointer({"x": 10, "y": 20, "accion": "move"}, companion_config)

    assert fake.moves == [(10, 20)]
    assert fake.clicks == []
    assert result == {"x": 10, "y": 20, "accion": "move", "button": "left"}


def test_input_pointer_click_moves_then_clicks_once(companion_config, monkeypatch):
    fake = _FakeInputBackend()
    monkeypatch.setattr(actions, "_get_input_backend", lambda: fake)

    actions._input_pointer({"x": 1, "y": 2, "accion": "click"}, companion_config)

    assert fake.moves == [(1, 2)]
    assert fake.clicks == [(1, 2, "left")]


def test_input_pointer_double_click_clicks_twice(companion_config, monkeypatch):
    fake = _FakeInputBackend()
    monkeypatch.setattr(actions, "_get_input_backend", lambda: fake)

    actions._input_pointer({"x": 1, "y": 2, "accion": "double_click"}, companion_config)

    assert fake.clicks == [(1, 2, "left"), (1, 2, "left")]


def test_input_pointer_right_click_forces_right_button_regardless_of_param(
    companion_config, monkeypatch
):
    fake = _FakeInputBackend()
    monkeypatch.setattr(actions, "_get_input_backend", lambda: fake)

    result = actions._input_pointer(
        {"x": 1, "y": 2, "accion": "right_click", "button": "left"}, companion_config
    )

    assert fake.clicks == [(1, 2, "right")]
    assert result["button"] == "right"


def test_input_pointer_honors_custom_button_for_click(companion_config, monkeypatch):
    fake = _FakeInputBackend()
    monkeypatch.setattr(actions, "_get_input_backend", lambda: fake)

    actions._input_pointer(
        {"x": 1, "y": 2, "accion": "click", "button": "middle"}, companion_config
    )

    assert fake.clicks == [(1, 2, "middle")]


def test_input_pointer_scroll_moves_then_scrolls(companion_config, monkeypatch):
    fake = _FakeInputBackend()
    monkeypatch.setattr(actions, "_get_input_backend", lambda: fake)

    result = actions._input_pointer(
        {"x": 20, "y": 30, "accion": "scroll", "delta_y": -240}, companion_config
    )

    assert fake.moves == [(20, 30)]
    assert fake.scrolls == [(0, -240)]
    assert result["delta_y"] == -240


def test_input_pointer_drag_posts_down_moves_and_up(companion_config, monkeypatch):
    fake = _FakeInputBackend()
    monkeypatch.setattr(actions, "_get_input_backend", lambda: fake)

    actions._input_pointer(
        {"start_x": 10, "start_y": 20, "x": 110, "y": 220, "accion": "drag"},
        companion_config,
    )

    assert fake.moves[0] == (10, 20)
    assert fake.moves[-1] == (110, 220)
    assert fake.downs == [(10, 20, "left")]
    assert fake.ups == [(110, 220, "left")]


def test_input_pointer_requires_x(companion_config):
    with pytest.raises(actions.ActionError, match="'x'"):
        actions._input_pointer({"y": 1, "accion": "move"}, companion_config)


def test_input_pointer_requires_y(companion_config):
    with pytest.raises(actions.ActionError, match="'y'"):
        actions._input_pointer({"x": 1, "accion": "move"}, companion_config)


def test_input_pointer_rejects_non_integer_coordinates(companion_config):
    with pytest.raises(actions.ActionError, match="'x'"):
        actions._input_pointer({"x": "10", "y": 1, "accion": "move"}, companion_config)


def test_input_pointer_rejects_bool_as_coordinate(companion_config):
    # bool es subclase de int en Python -- se rechaza explícitamente, no cuela
    # como un entero válido (mismo criterio que `_coerce_non_negative_int`).
    with pytest.raises(actions.ActionError, match="'x'"):
        actions._input_pointer({"x": True, "y": 1, "accion": "move"}, companion_config)


def test_input_pointer_rejects_invalid_accion(companion_config):
    with pytest.raises(actions.ActionError, match="accion"):
        actions._input_pointer({"x": 1, "y": 1, "accion": "boom"}, companion_config)


def test_input_pointer_rejects_invalid_button(companion_config):
    with pytest.raises(actions.ActionError, match="button"):
        actions._input_pointer(
            {"x": 1, "y": 1, "accion": "click", "button": "boom"}, companion_config
        )


# ---------------------------------------------------------------------------
# _input_key
# ---------------------------------------------------------------------------


def test_input_key_texto_types_and_reports_length(companion_config, monkeypatch):
    fake = _FakeInputBackend()
    monkeypatch.setattr(actions, "_get_input_backend", lambda: fake)

    result = actions._input_key({"texto": "hola"}, companion_config)

    assert fake.typed == ["hola"]
    assert fake.pressed == []
    assert result == {"tipo": "texto", "length": 4}


def test_input_key_tecla_presses_and_reports(companion_config, monkeypatch):
    fake = _FakeInputBackend()
    monkeypatch.setattr(actions, "_get_input_backend", lambda: fake)

    result = actions._input_key({"tecla": "enter"}, companion_config)

    assert fake.pressed == ["enter"]
    assert fake.typed == []
    assert result == {"tipo": "tecla", "tecla": "enter"}


def test_input_key_supports_keyboard_shortcuts(companion_config, monkeypatch):
    fake = _FakeInputBackend()
    monkeypatch.setattr(actions, "_get_input_backend", lambda: fake)

    result = actions._input_key(
        {"tecla": "space", "modifiers": ["command", "shift"]}, companion_config
    )

    assert fake.shortcuts == [("space", ("command", "shift"))]
    assert result["modifiers"] == ["command", "shift"]


@pytest.mark.parametrize("tecla", actions._SPECIAL_KEYS)
def test_input_key_accepts_every_documented_special_key(companion_config, monkeypatch, tecla):
    fake = _FakeInputBackend()
    monkeypatch.setattr(actions, "_get_input_backend", lambda: fake)

    actions._input_key({"tecla": tecla}, companion_config)

    assert fake.pressed == [tecla]


def test_input_key_rejects_both_texto_and_tecla(companion_config):
    with pytest.raises(actions.ActionError, match="exactamente uno"):
        actions._input_key({"texto": "hola", "tecla": "enter"}, companion_config)


def test_input_key_rejects_neither_texto_nor_tecla(companion_config):
    with pytest.raises(actions.ActionError, match="exactamente uno"):
        actions._input_key({}, companion_config)


def test_input_key_rejects_invalid_tecla(companion_config):
    with pytest.raises(actions.ActionError, match="inválida"):
        actions._input_key({"tecla": "F13"}, companion_config)


def test_input_key_rejects_empty_texto(companion_config):
    with pytest.raises(actions.ActionError, match="no vacío"):
        actions._input_key({"texto": ""}, companion_config)


# ---------------------------------------------------------------------------
# execute() -- gate de remote_input_enabled + aprobación + feliz + auditoría
# ---------------------------------------------------------------------------


async def test_execute_blocks_input_pointer_without_remote_input_enabled(companion_config):
    assert companion_config.remote_input_enabled is False  # default, ver config.py

    async def _fail_if_asked(action, params, config):
        raise AssertionError("no debería siquiera preguntar: remote_input_enabled=false")

    result = await actions.execute(
        "input_pointer", {"x": 1, "y": 1, "accion": "move"}, companion_config, _fail_if_asked
    )

    assert result["ok"] is False
    assert "remote_input_enabled" in result["error"]


async def test_execute_blocks_input_key_without_remote_input_enabled(companion_config):
    async def _fail_if_asked(action, params, config):
        raise AssertionError("no debería siquiera preguntar: remote_input_enabled=false")

    result = await actions.execute(
        "input_key", {"tecla": "enter"}, companion_config, _fail_if_asked
    )

    assert result["ok"] is False
    assert "remote_input_enabled" in result["error"]


async def test_execute_input_blocked_without_approval(companion_config):
    companion_config.remote_input_enabled = True

    async def _reject_everything(action, params, config):
        return False

    result = await actions.execute(
        "input_pointer", {"x": 1, "y": 1, "accion": "move"}, companion_config, _reject_everything
    )

    assert result["ok"] is False
    assert "rechaz" in result["error"]


async def test_execute_input_pointer_happy_path_with_fake_backend(companion_config, monkeypatch):
    companion_config.remote_input_enabled = True
    fake = _FakeInputBackend()
    monkeypatch.setattr(actions, "_get_input_backend", lambda: fake)

    async def _approve_everything(action, params, config):
        return True

    result = await actions.execute(
        "input_pointer", {"x": 5, "y": 6, "accion": "click"}, companion_config, _approve_everything
    )

    assert result["ok"] is True
    assert result["result"] == {"x": 5, "y": 6, "accion": "click", "button": "left"}
    assert fake.clicks == [(5, 6, "left")]


async def test_execute_input_key_texto_happy_path_with_fake_backend(companion_config, monkeypatch):
    companion_config.remote_input_enabled = True
    fake = _FakeInputBackend()
    monkeypatch.setattr(actions, "_get_input_backend", lambda: fake)

    async def _approve_everything(action, params, config):
        return True

    result = await actions.execute(
        "input_key", {"texto": "hola"}, companion_config, _approve_everything
    )

    assert result["ok"] is True
    assert fake.typed == ["hola"]


async def test_execute_input_key_invalid_tecla_reports_action_error(companion_config, monkeypatch):
    companion_config.remote_input_enabled = True
    monkeypatch.setattr(actions, "_get_input_backend", lambda: _FakeInputBackend())

    async def _approve_everything(action, params, config):
        return True

    result = await actions.execute(
        "input_key", {"tecla": "F13"}, companion_config, _approve_everything
    )

    assert result["ok"] is False
    assert "inválida" in result["error"]


async def test_execute_redacts_texto_in_audit_log_for_input_key(companion_config, monkeypatch):
    companion_config.remote_input_enabled = True
    monkeypatch.setattr(actions, "_get_input_backend", lambda: _FakeInputBackend())

    async def _approve_everything(action, params, config):
        return True

    secret = "contraseña-super-secreta"
    await actions.execute("input_key", {"texto": secret}, companion_config, _approve_everything)

    log_text = companion_config.audit_log_path.read_text(encoding="utf-8")
    assert secret not in log_text


@pytest.mark.parametrize("action_name", ["input_pointer", "input_key"])
def test_input_actions_are_registered_in_actions_dict_and_input_actions_set(action_name):
    assert callable(actions.ACTIONS[action_name])
    assert action_name in actions._INPUT_ACTIONS
