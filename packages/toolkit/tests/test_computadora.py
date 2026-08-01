"""Tests de `edecan_toolkit.computadora`: `usar_computadora`."""

from __future__ import annotations

import pytest
from edecan_schemas.plans import (
    FLAG_COMPANION_IDE,
    FLAG_COMPANION_REMOTE_INPUT,
    FLAG_COMPANION_REMOTE_VIEW,
)
from edecan_toolkit.computadora import UsarComputadoraTool


async def test_usar_computadora_sin_companion_emparejado(make_ctx):
    resultado = await UsarComputadoraTool().run(make_ctx(extras={}), {"accion": "captura_pantalla"})
    assert "companion" in resultado.content.lower()
    assert "emparejado" in resultado.content.lower()


async def test_usar_computadora_con_companion_lo_invoca(make_ctx):
    llamadas = []

    async def companion_falso(accion: str, parametros: dict) -> dict:
        llamadas.append((accion, parametros))
        return {"ok": True, "captura": "base64..."}

    ctx = make_ctx(extras={"companion": companion_falso})
    resultado = await UsarComputadoraTool().run(
        ctx, {"accion": "captura_pantalla", "parametros": {"pantalla": 1}}
    )

    assert llamadas == [("captura_pantalla", {"pantalla": 1})]
    assert resultado.data["resultado"] == {"ok": True, "captura": "base64..."}


async def test_usar_computadora_sin_accion(make_ctx):
    async def companion_falso(accion, parametros):
        return {}

    ctx = make_ctx(extras={"companion": companion_falso})
    resultado = await UsarComputadoraTool().run(ctx, {"accion": "  "})
    assert "acción" in resultado.content.lower()


def test_usar_computadora_flag_y_dangerous():
    tool = UsarComputadoraTool()
    assert tool.dangerous is True
    assert tool.requires_flags == frozenset({"companion"})


# ---------------------------------------------------------------------------
# Regresión (riesgo-legal-tos): `requires_flags` solo exige el flag base
# `companion` -- las acciones más sensibles del dispatch table compartido del
# companion (IDE embebido, control remoto de teclado/mouse) exigen ADEMÁS el
# mismo flag más fino que ya exigen los routers HTTP dedicados
# (`routers/ide.py`, `routers/remote.py`) para esa MISMA acción. Sin este
# chequeo dentro de `run()`, un tenant con `companion=True` pero, p. ej.,
# `companion.remote_input=False` (`hosted_basic`, ver `edecan_schemas.plans`)
# podía alcanzar `input_pointer`/`input_key` igual con solo pedírselo al
# modelo por chat -- el companion nunca se llegaba a invocar en absoluto
# antes de este fix.
# ---------------------------------------------------------------------------


def _companion_no_debe_llamarse(llamadas: list[tuple[str, dict]]):
    async def _companion_falso(accion: str, parametros: dict) -> dict:
        llamadas.append((accion, parametros))
        return {"ok": True}

    return _companion_falso


@pytest.mark.parametrize(
    "accion,flags_insuficientes,mensaje_esperado",
    [
        ("list_tree", {}, "ide"),
        ("search_files", {FLAG_COMPANION_IDE: False}, "ide"),
        ("apply_edit", {FLAG_COMPANION_IDE: False}, "ide"),
        # `read_file`/`write_file`/`run_command` son acciones "v1" que
        # `edecan_companion.actions._IDE_ACTIONS` NO trata como "de IDE"
        # localmente, pero SÍ están servidas bajo `/v1/ide/*`
        # (`routers/ide.py::get_file`/`put_file`/`post_run`, las tres detrás
        # de `_require_companion_ide`) -- deben bloquearse igual que
        # `list_tree`/`search_files`/`apply_edit` (hallazgo plan-flag-bypass:
        # `_ACCIONES_IDE` antes solo tenía 3 de las 6 acciones que
        # `ide._require_companion_ide` protege).
        ("read_file", {}, "ide"),
        ("write_file", {FLAG_COMPANION_IDE: False}, "ide"),
        ("trash_path", {FLAG_COMPANION_IDE: False}, "ide"),
        ("run_command", {FLAG_COMPANION_IDE: False}, "ide"),
        ("screenshot", {}, "vista remota"),
        ("screenshot", {FLAG_COMPANION_REMOTE_VIEW: False}, "vista remota"),
        ("input_pointer", {}, "control remoto"),
        ("input_key", {FLAG_COMPANION_REMOTE_INPUT: False}, "control remoto"),
        # `companion.remote_view=True` sin `companion.remote_input` no basta
        # -- `input_pointer`/`input_key` exigen los DOS flags, igual que
        # `_require_remote_control` (que depende de `_require_remote_view`).
        (
            "input_key",
            {FLAG_COMPANION_REMOTE_VIEW: True, FLAG_COMPANION_REMOTE_INPUT: False},
            "control remoto",
        ),
    ],
)
async def test_usar_computadora_bloquea_accion_sin_flag_fino(
    make_ctx, accion, flags_insuficientes, mensaje_esperado
):
    llamadas: list[tuple[str, dict]] = []
    ctx = make_ctx(
        extras={"companion": _companion_no_debe_llamarse(llamadas), "flags": flags_insuficientes}
    )

    resultado = await UsarComputadoraTool().run(ctx, {"accion": accion})

    assert llamadas == [], "el companion NUNCA debe invocarse si falta el flag fino"
    assert mensaje_esperado in resultado.content.lower()
    assert "plan" in resultado.content.lower()


async def test_usar_computadora_bloquea_accion_sin_dict_de_flags(make_ctx):
    """`ctx.extras` sin la clave `"flags"` (o con un valor no-`dict`) debe
    tratarse como "ningún flag fino activo" -- fail-closed, nunca fail-open."""
    llamadas: list[tuple[str, dict]] = []
    ctx = make_ctx(extras={"companion": _companion_no_debe_llamarse(llamadas)})  # sin "flags"

    resultado = await UsarComputadoraTool().run(ctx, {"accion": "input_pointer"})

    assert llamadas == []
    assert "control remoto" in resultado.content.lower()


@pytest.mark.parametrize(
    "accion,flags_suficientes",
    [
        ("list_tree", {FLAG_COMPANION_IDE: True}),
        ("search_files", {FLAG_COMPANION_IDE: True}),
        ("apply_edit", {FLAG_COMPANION_IDE: True}),
        ("read_file", {FLAG_COMPANION_IDE: True}),
        ("write_file", {FLAG_COMPANION_IDE: True}),
        ("trash_path", {FLAG_COMPANION_IDE: True}),
        ("run_command", {FLAG_COMPANION_IDE: True}),
        ("screenshot", {FLAG_COMPANION_REMOTE_VIEW: True}),
        (
            "input_pointer",
            {FLAG_COMPANION_REMOTE_VIEW: True, FLAG_COMPANION_REMOTE_INPUT: True},
        ),
        (
            "input_key",
            {FLAG_COMPANION_REMOTE_VIEW: True, FLAG_COMPANION_REMOTE_INPUT: True},
        ),
    ],
)
async def test_usar_computadora_permite_accion_con_flag_fino_activo(
    make_ctx, accion, flags_suficientes
):
    llamadas = []

    async def companion_falso(accion_recibida: str, parametros: dict) -> dict:
        llamadas.append((accion_recibida, parametros))
        return {"ok": True}

    ctx = make_ctx(extras={"companion": companion_falso, "flags": flags_suficientes})

    resultado = await UsarComputadoraTool().run(ctx, {"accion": accion})

    assert llamadas == [(accion, {})]
    assert resultado.data["resultado"] == {"ok": True}


@pytest.mark.parametrize("accion", ["open_app", "read_dir", "clipboard_get", "clipboard_set"])
async def test_usar_computadora_acciones_base_no_exigen_flags_finos(make_ctx, accion):
    """De las 7 acciones originales (v1), estas 4 siguen sin exigir ningún
    flag más allá del `companion` base -- `extras` ni siquiera trae `"flags"`.
    Las otras 3 (`read_file`/`write_file`/`run_command`) SÍ están servidas
    bajo `/v1/ide/*` (`routers/ide.py`) y por eso ahora exigen
    `companion.ide` igual que `list_tree`/`search_files`/`apply_edit` -- ver
    `test_usar_computadora_bloquea_accion_sin_flag_fino`/
    `test_usar_computadora_permite_accion_con_flag_fino_activo` más arriba
    (hallazgo plan-flag-bypass, `_ACCIONES_IDE` en `computadora.py`)."""
    llamadas = []

    async def companion_falso(accion_recibida: str, parametros: dict) -> dict:
        llamadas.append((accion_recibida, parametros))
        return {"ok": True}

    ctx = make_ctx(extras={"companion": companion_falso})  # sin "flags"

    resultado = await UsarComputadoraTool().run(ctx, {"accion": accion})

    assert llamadas == [(accion, {})]
    assert resultado.data["resultado"] == {"ok": True}


async def test_usar_computadora_sin_flag_remote_input_no_alcanza_input_remoto_por_chat(make_ctx):
    """Defensa en profundidad de `_bloqueo_por_plan`: aunque HOY los 4 planes
    reales otorgan todos los flags por igual (pago único, sin gating de
    features -- ver `packages/schemas/edecan_schemas/plans.py`), el chequeo
    en sí sigue siendo código real que debe seguir bloqueando fail-closed si
    algún día vuelve a existir un tenant/flags con `companion=True` pero
    `companion.remote_input=False` (p. ej. un `plan_key` huérfano). Se arma
    el dict de flags a mano en vez de leerlo de `PLANES` real, ya que ningún
    plan actual reproduce ese escenario."""
    flags_sin_input_remoto = {
        "companion": True,
        FLAG_COMPANION_IDE: True,
        FLAG_COMPANION_REMOTE_VIEW: True,
        FLAG_COMPANION_REMOTE_INPUT: False,
    }

    llamadas: list[tuple[str, dict]] = []
    ctx = make_ctx(
        extras={
            "companion": _companion_no_debe_llamarse(llamadas),
            "flags": flags_sin_input_remoto,
        }
    )

    resultado = await UsarComputadoraTool().run(
        ctx, {"accion": "input_pointer", "parametros": {"x": 100, "y": 200, "accion": "click"}}
    )

    assert llamadas == [], "sin companion.remote_input no debe tocar el companion"
    assert "control remoto" in resultado.content.lower()
