"""Fixtures compartidas de `edecan_companion` — sin red, sin tocar el sistema real.

Ningún test de este paquete debe leer/escribir `~/.edecan/` de verdad: todo
`CompanionConfig` de prueba apunta su `sandbox_dir`/`config_path`/
`audit_log_path` dentro de `tmp_path`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from edecan_companion.config import CompanionConfig


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
