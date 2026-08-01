"""Fixtures compartidas de `edecan_companion` — sin red, sin tocar el sistema real.

Ningún test de este paquete debe leer/escribir `~/.edecan/` de verdad: todo
`CompanionConfig` de prueba apunta su `sandbox_dir`/`config_path`/
`audit_log_path` dentro de `tmp_path`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from edecan_companion.config import CompanionConfig


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
