"""Doctor local, no destructivo, para la fase 3 de Edecán.

Comprueba contratos que pueden validarse sin arrancar servicios ni modificar
datos. No llama a proveedores externos y nunca presenta este chequeo como una
prueba de producción.
"""

from __future__ import annotations

import argparse
import importlib
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_IMPORTS = (
    "edecan_core.session",
    "edecan_core.session_store",
    "edecan_voice.realtime",
    "edecan_agents.orchestrator",
    "edecan_automations.engine",
    "edecan_api.main",
)


def _check_imports() -> list[str]:
    failures: list[str] = []
    for module in _IMPORTS:
        try:
            importlib.import_module(module)
        except Exception as exc:  # noqa: BLE001 - el doctor debe reportar el módulo
            failures.append(f"import {module}: {type(exc).__name__}: {exc}")
    return failures


def _check_migration_head() -> list[str]:
    try:
        result = subprocess.run(
            [
                "uv",
                "run",
                "--all-packages",
                "alembic",
                "-c",
                "packages/db/alembic.ini",
                "heads",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return [f"alembic heads: {type(exc).__name__}: {exc}"]
    if result.returncode != 0:
        return [f"alembic heads: {result.stderr.strip() or result.stdout.strip()}"]
    if "0046_provider_health_events" not in result.stdout:
        return [f"alembic heads no contiene 0046_provider_health_events: {result.stdout.strip()}"]
    return []


def _check_files() -> list[str]:
    required = (
        "HANDOFF.md",
        "app.md",
        "config/modelos.yml",
        "packages/db/alembic/versions/0040_unified_sessions.py",
        "packages/db/alembic/versions/0041_mission_archival.py",
        "packages/db/alembic/versions/0042_mission_pause_resume.py",
        "packages/db/alembic/versions/0043_persistent_agents.py",
        "packages/db/alembic/versions/0044_persistent_agent_handoffs.py",
        "packages/db/alembic/versions/0045_persistent_agent_lease_index.py",
        "packages/db/alembic/versions/0046_provider_health_events.py",
        "packages/core/edecan_core/session.py",
        "packages/voice/edecan_voice/realtime.py",
        "apps/mobile/ios/EdecanKit/Sources/EdecanKit/RealtimeVoiceClient.swift",
        "apps/mobile/ios/EdecanKit/Sources/EdecanKit/SharePayloadStore.swift",
        "apps/mobile/ios/EdecanShareExtension/ShareViewController.swift",
        "apps/mobile/ios/EdecanShareExtension/EdecanShareExtension.entitlements",
        "apps/mobile/ios/EdecanApp/EdecanAppIntents.swift",
        "apps/mobile/ios/EdecanWidgets/EdecanWidgetsBundle.swift",
    )
    return [f"falta {relative}" for relative in required if not (ROOT / relative).is_file()]


def _check_production_environment() -> list[str]:
    required = ("DATABASE_URL", "REDIS_URL", "JWT_SECRET", "PUBLIC_BASE_URL")
    return [f"falta variable {name}" for name in required if not os.environ.get(name)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--production",
        action="store_true",
        help="también exige las variables mínimas; no conecta ni despliega nada",
    )
    args = parser.parse_args()

    failures = [*_check_files(), *_check_imports(), *_check_migration_head()]
    if args.production:
        failures.extend(_check_production_environment())

    if failures:
        print("PHASE3 DOCTOR: FAIL")
        for failure in failures:
            print(f"- {failure}")
        return 1

    scope = "local + variables de despliegue presentes" if args.production else "local"
    print(f"PHASE3 DOCTOR: OK ({scope}; sin conexiones ni escrituras)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
