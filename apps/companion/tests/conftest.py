"""Fixtures compartidas de `edecan_companion` — sin red, sin tocar el sistema real.

Ningún test de este paquete debe leer/escribir `~/.edecan/` de verdad: todo
`CompanionConfig` de prueba apunta su `sandbox_dir`/`config_path`/
`audit_log_path` dentro de `tmp_path`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from edecan_companion.config import CompanionConfig
from edecan_companion.ide_opencode_binario import (
    BinarioOpencodeNoEncontrado,
    resolver_binario_opencode,
)


def hay_binario_opencode() -> bool:
    """True cuando el resolver de producción encuentra un ejecutable.

    Linux/Windows CI no instalan el sidecar; las pruebas que arrancan
    ``opencode serve`` de verdad se saltan, no se borran.
    """
    try:
        resolver_binario_opencode()
    except BinarioOpencodeNoEncontrado:
        return False
    return True


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "opencode_binario: arranca opencode serve real; se salta si no hay binario",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """CI Linux/Windows no empaqueta el sidecar: las pruebas que arrancan
    ``opencode serve`` se saltan, no se borran ni se simulan."""

    if hay_binario_opencode():
        return
    skip = pytest.mark.skip(
        reason=(
            "No hay binario de opencode (bundle / EDECAN_OPENCODE_BIN / PATH); "
            "CI no empaqueta el sidecar. Las pruebas se saltan, no se simulan."
        )
    )
    for item in items:
        if item.get_closest_marker("opencode_binario"):
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _motor_ide_por_defecto_en_pruebas(monkeypatch: pytest.MonkeyPatch) -> None:
    """El motor por defecto de un ``SessionManager``/``IDERuntime`` de
    producción es "opencode" (ver ``ide_sessions.SessionManager._motor_vigente``,
    docs/opencode-motor.md) -- pero arrancar un proceso ``opencode serve``
    real más credenciales reales de Cloudflare en CADA turno de CADA prueba
    de este paquete volvería la suite lenta, dependiente de red y gastando
    tokens reales en pruebas que nunca pidieron ejercitar opencode (la
    mayoría monkeypatchea ``WorkersIDEAgent.run`` esperando el motor viejo).

    Se fija "viejo" acá, en un único lugar, en vez de tocar cada sitio del
    paquete que construye ``SessionManager``/``IDERuntime`` (varios archivos,
    ninguno "mío" en la ronda que agregó esto). Una prueba que sí quiera
    ejercer el camino opencode lo pide explícito con
    ``monkeypatch.setenv("EDECAN_IDE_MOTOR", "opencode")`` -- las pruebas
    reales de ``test_ide_opencode_motor.py`` y compañía no pasan por acá
    porque construyen ``MotorOpencode``/``ServidorOpencode`` directo, nunca
    ``SessionManager``.
    """
    monkeypatch.setenv("EDECAN_IDE_MOTOR", "viejo")


@pytest.fixture
def companion_config(tmp_path: Path) -> CompanionConfig:
    """`CompanionConfig` aislado en `tmp_path`, con las listas blancas vacías por defecto."""
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    return CompanionConfig(
        sandbox_dir=sandbox.resolve(),
        # Aislado en tmp_path: sin esto, las acciones `transfer_*` caerían al
        # default `~/Edecán/Compartidos` y tocarían el home real.
        transfer_dir=(tmp_path / "compartidos").resolve(),
        allowed_apps=[],
        allowed_commands=[],
        auto_approve=[],
        config_path=tmp_path / "companion.yaml",
        audit_log_path=tmp_path / "companion.log",
    )
