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
# Doble del `Quartz` de GEOMETRÍA -- para `_macos_pointer_display_bounds`, que
# no sintetiza ningún evento: solo enumera pantallas y lee `CGDisplayBounds`.
# ---------------------------------------------------------------------------


class _FakeCGRect:
    """`CGRect` de pyobjc: `.origin.x/.y` y `.size.width/.height`, en PUNTOS."""

    def __init__(self, x: float, y: float, width: float, height: float) -> None:
        self.origin = type("Origin", (), {"x": x, "y": y})()
        self.size = type("Size", (), {"width": width, "height": height})()


class _FakeQuartzDisplaysModule:
    kCGErrorSuccess = 0

    def __init__(self, rects: list[tuple[float, float, float, float]]) -> None:
        # `display_id` arbitrario y distinto por pantalla (como los reales).
        self._rects = {100 + index: rect for index, rect in enumerate(rects)}
        self.bounds_consultados: list[int] = []

    def CGGetActiveDisplayList(self, max_displays, displays, count):  # noqa: ANN001
        ids = list(self._rects)
        return self.kCGErrorSuccess, ids, len(ids)

    def CGDisplayBounds(self, display_id):  # noqa: ANN001
        self.bounds_consultados.append(display_id)
        return _FakeCGRect(*self._rects[display_id])


def _install_fake_quartz_displays(
    monkeypatch, rects: list[tuple[float, float, float, float]]
) -> _FakeQuartzDisplaysModule:
    fake = _FakeQuartzDisplaysModule(rects)
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
    monkeypatch.delenv("EDECAN_DESKTOP_BRIDGE_SOCKET", raising=False)
    monkeypatch.delenv("EDECAN_DESKTOP_BRIDGE_TOKEN", raising=False)
    _install_fake_quartz(monkeypatch)

    backend = actions._get_input_backend()

    assert isinstance(backend, actions._QuartzInputBackend)


def test_get_input_backend_prefers_authorized_desktop_bridge_on_darwin(monkeypatch):
    monkeypatch.setattr(actions.sys, "platform", "darwin")
    monkeypatch.setenv("EDECAN_DESKTOP_BRIDGE_SOCKET", "/tmp/edecan-test.sock")
    monkeypatch.setenv("EDECAN_DESKTOP_BRIDGE_TOKEN", "test-token")

    backend = actions._get_input_backend()

    assert isinstance(backend, actions._DesktopBridgeInputBackend)


def test_desktop_bridge_input_backend_forwards_typed_actions(monkeypatch):
    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        actions,
        "_desktop_bridge_call",
        lambda action, params: calls.append((action, params)) or {"executed": True},
    )
    backend = actions._DesktopBridgeInputBackend()

    backend.click_pointer(10, 20, "left")
    backend.scroll_pointer(3, -90)
    backend.type_text("Hola")
    backend.press_key("enter", ("command",))

    assert calls == [
        ("click_pointer", {"x": 10, "y": 20, "button": "left"}),
        ("scroll_pointer", {"delta_x": 3, "delta_y": -90}),
        ("type_text", {"text": "Hola"}),
        ("press_key", {"key": "enter", "modifiers": ["command"]}),
    ]


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
# Coordenada NORMALIZADA (`nx`/`ny`) -> coordenada real del display.
#
# El bug que esto cubre: el frame que ve el teléfono viaja REDUCIDO a
# `max_width` (1600 por defecto), la captura Retina son PÍXELES (3456 de
# ancho) y `CGEvent` consume PUNTOS lógicos (2056 de ancho). Mandar la
# coordenada del frame como si fueran puntos dejaba cada clic al 77.8% del
# camino hacia la esquina superior izquierda. Con `nx`/`ny` la cuenta la hace
# el companion, que es el único que conoce la geometría real.
# ---------------------------------------------------------------------------


def _bounds_fijos(origin_x: int, origin_y: int, width: int, height: int):
    """Reemplazo de `_pointer_display_bounds` con una geometría conocida."""
    return lambda params: (origin_x, origin_y, width, height)


def test_fraction_to_display_coord_mapea_el_centro_y_los_extremos():
    # 2056 x 1329 = los PUNTOS lógicos reales de la Mac del reporte.
    assert actions._fraction_to_display_coord(0.0, 0, 2056) == 0
    assert actions._fraction_to_display_coord(0.5, 0, 2056) == 1028
    # 1.0 se recorta a `size - 1`: `origin + size` ya es el monitor de al lado.
    assert actions._fraction_to_display_coord(1.0, 0, 2056) == 2055
    # Redondeo mitad-hacia-arriba (no el "al par" de `round()`): 0.5*1329=664.5.
    assert actions._fraction_to_display_coord(0.5, 0, 1329) == 665


def test_fraction_to_display_coord_respeta_el_origen_de_un_segundo_monitor():
    # Monitor secundario a la derecha del principal: origen (2056, 0).
    assert actions._fraction_to_display_coord(0.0, 2056, 1920) == 2056
    assert actions._fraction_to_display_coord(1.0, 2056, 1920) == 2056 + 1919
    # Monitor arriba del principal: origen vertical NEGATIVO.
    assert actions._fraction_to_display_coord(0.0, -1080, 1080) == -1080


def test_input_pointer_normalizado_gana_sobre_x_y(companion_config, monkeypatch):
    fake = _FakeInputBackend()
    monkeypatch.setattr(actions, "_get_input_backend", lambda: fake)
    monkeypatch.setattr(actions, "_pointer_display_bounds", _bounds_fijos(0, 0, 2056, 1329))

    # `x`/`y` vienen en el espacio del frame comprimido (1600 de ancho) y son
    # justamente los que llevaban el clic al lugar equivocado: se ignoran.
    result = actions._input_pointer(
        {"x": 800, "y": 517, "nx": 0.5, "ny": 0.5, "accion": "click"}, companion_config
    )

    assert fake.moves == [(1028, 665)]
    assert fake.clicks == [(1028, 665, "left")]
    assert result["x"] == 1028
    assert result["y"] == 665


def test_input_pointer_normalizado_alcanza_la_esquina_inferior_derecha(
    companion_config, monkeypatch
):
    # El síntoma más visible del bug: el Dock y la franja derecha eran
    # literalmente inalcanzables. Con la fracción, `1.0` llega al último punto.
    fake = _FakeInputBackend()
    monkeypatch.setattr(actions, "_get_input_backend", lambda: fake)
    monkeypatch.setattr(actions, "_pointer_display_bounds", _bounds_fijos(0, 0, 2056, 1329))

    actions._input_pointer(
        {"x": 1599, "y": 1033, "nx": 1.0, "ny": 1.0, "accion": "click"}, companion_config
    )

    assert fake.clicks == [(2055, 1328, "left")]


def test_input_pointer_normalizado_en_un_monitor_con_origen_distinto_de_cero(
    companion_config, monkeypatch
):
    fake = _FakeInputBackend()
    monkeypatch.setattr(actions, "_get_input_backend", lambda: fake)
    monkeypatch.setattr(actions, "_pointer_display_bounds", _bounds_fijos(2056, -120, 1920, 1080))

    actions._input_pointer(
        {"x": 0, "y": 0, "nx": 0.5, "ny": 0.0, "accion": "click"}, companion_config
    )

    assert fake.clicks == [(2056 + 960, -120, "left")]


def test_input_pointer_drag_normalizado_mapea_inicio_y_fin(companion_config, monkeypatch):
    fake = _FakeInputBackend()
    monkeypatch.setattr(actions, "_get_input_backend", lambda: fake)
    monkeypatch.setattr(actions, "_pointer_display_bounds", _bounds_fijos(0, 0, 2000, 1000))

    result = actions._input_pointer(
        {
            "x": 1,
            "y": 2,
            "start_x": 3,
            "start_y": 4,
            "nx": 0.75,
            "ny": 0.5,
            "start_nx": 0.25,
            "start_ny": 0.1,
            "accion": "drag",
        },
        companion_config,
    )

    assert fake.downs == [(500, 100, "left")]
    assert fake.ups == [(1500, 500, "left")]
    assert fake.moves[0] == (500, 100)
    assert fake.moves[-1] == (1500, 500)
    assert result["start_x"] == 500
    assert result["start_y"] == 100


def test_input_pointer_sin_normalizadas_nunca_consulta_la_geometria(companion_config, monkeypatch):
    """Camino legacy INTACTO: sin `nx`/`ny` no se toca el display para nada."""
    fake = _FakeInputBackend()
    monkeypatch.setattr(actions, "_get_input_backend", lambda: fake)

    def _explota(params):  # pragma: no cover - debe no llamarse nunca
        raise AssertionError("el camino legacy no debe consultar la geometría del display")

    monkeypatch.setattr(actions, "_pointer_display_bounds", _explota)

    result = actions._input_pointer({"x": 10, "y": 20, "accion": "click"}, companion_config)

    assert fake.clicks == [(10, 20, "left")]
    assert result == {"x": 10, "y": 20, "accion": "click", "button": "left"}


def test_input_pointer_rechaza_media_coordenada_normalizada(companion_config):
    with pytest.raises(actions.ActionError, match="'nx' y 'ny'"):
        actions._input_pointer({"x": 1, "y": 2, "nx": 0.5, "accion": "click"}, companion_config)


def test_input_pointer_rechaza_media_coordenada_normalizada_de_inicio(companion_config):
    with pytest.raises(actions.ActionError, match="'start_nx' y 'start_ny'"):
        actions._input_pointer(
            {"x": 1, "y": 2, "start_x": 1, "start_y": 2, "start_ny": 0.5, "accion": "drag"},
            companion_config,
        )


@pytest.mark.parametrize("valor", [-0.01, 1.01, float("nan"), float("inf"), "0.5", True, None])
def test_input_pointer_rechaza_fracciones_invalidas(companion_config, valor):
    # `None` en `nx` con `ny` presente cae en la regla "se mandan juntos".
    with pytest.raises(actions.ActionError, match="'nx'"):
        actions._input_pointer(
            {"x": 1, "y": 2, "nx": valor, "ny": 0.5, "accion": "click"}, companion_config
        )


def test_pointer_display_bounds_rechaza_plataformas_sin_backend(monkeypatch):
    monkeypatch.setattr(actions.sys, "platform", "freebsd13")

    with pytest.raises(actions.ActionError, match="no está soportado"):
        actions._pointer_display_bounds({})


def test_macos_pointer_display_bounds_devuelve_puntos_logicos_no_pixeles(monkeypatch):
    # `CGDisplayBounds` habla en PUNTOS: en la Mac del reporte, 2056x1329
    # mientras `screencapture` entrega 3456x2234 píxeles Retina.
    fake = _install_fake_quartz_displays(
        monkeypatch,
        [(0.0, 0.0, 2056.0, 1329.0), (2056.0, -120.0, 1920.0, 1080.0)],
    )

    assert actions._macos_pointer_display_bounds({}) == (0, 0, 2056, 1329)
    assert actions._macos_pointer_display_bounds({"display": 2}) == (2056, -120, 1920, 1080)
    assert fake.bounds_consultados  # se usó CGDisplayBounds, no una dimensión inventada


def test_macos_pointer_display_bounds_rechaza_un_display_fuera_de_rango(monkeypatch):
    _install_fake_quartz_displays(monkeypatch, [(0.0, 0.0, 2056.0, 1329.0)])

    with pytest.raises(actions.ActionError, match="fuera de rango"):
        actions._macos_pointer_display_bounds({"display": 7})


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
