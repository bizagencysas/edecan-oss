"""Demostración y Validación de la Fase G — Resiliencia, Autocuración y TUI Dashboard."""

from __future__ import annotations

import tempfile
from pathlib import Path

from edecan_forge_kernel.cas import Cas
from edecan_forge_kernel.resilience import (
    AutoHealingEngine,
    StuckAgentDetector,
    TuiStateInspector,
)
from edecan_forge_kernel.vfs import Vfs


def run_phase_g_demo():
    print("============================================================")
    print("       DEMO DE LA FASE G: RESILIENCIA, ATASCAMIENTO Y TUI   ")
    print("============================================================\n")

    # 1. Detección de Agentes Atascados
    print("1. Probando Detector de Agentes Atascados (3 turnos sin cambios en VFS)...")
    with tempfile.TemporaryDirectory() as tmp_dir:
        vfs = Vfs(cas=Cas(root=Path(tmp_dir)))
        detector = StuckAgentDetector(max_no_progress_turns=3)

        # Turno 1
        is_stuck_1 = detector.record_turn(vfs, "read_file_window", {"path": "main.py"})
        print(f"   Turno 1: ¿Atascado? = {is_stuck_1}")
        assert not is_stuck_1

        # Turno 2 (Misma herramienta, sin modificar VFS)
        is_stuck_2 = detector.record_turn(vfs, "read_file_window", {"path": "main.py"})
        print(f"   Turno 2: ¿Atascado? = {is_stuck_2}")
        assert not is_stuck_2

        # Turno 3 (Misma herramienta, sin modificar VFS -> ATASCADO DETECTADO)
        is_stuck_3 = detector.record_turn(vfs, "read_file_window", {"path": "main.py"})
        print(f"   Turno 3: ¿Atascado? = {is_stuck_3}")
        assert is_stuck_3
        print("   [OK] Detección de bucle atascado verificada correctamente.\n")

    # 2. Generación de Prompt de Autocuración (Recovery Hint)
    print("2. Probando Generación de Hints de Autocuración...")
    hint = AutoHealingEngine.generate_recovery_prompt("read_file_window", retry_count=3)
    print(f"   Hint generado:\n   {hint}")
    assert "read_file_window" in hint
    assert "SYSTEM RECOVERY HINT" in hint
    print("   [OK] Inyección de Hint de recuperación verificada.\n")

    # 3. TUI Dashboard State Inspector
    print("3. Probando Panel TUI de Inspección en Tiempo Real...")
    dashboard = TuiStateInspector.format_status_dashboard(
        session_id="sess-889900", vfs_version=12, effect_class="SAFE", turns_count=5
    )
    print(dashboard)
    assert "sess-889900" in dashboard
    assert "VFS Version   : 12" in dashboard
    print("   [OK] Renderizado de Dashboard TUI verificado.\n")

    print("============================================================")
    print("          VEREDICTO FASE G: RESILIENCIA Y TUI EN VERDE      ")
    print("============================================================\n")


if __name__ == "__main__":
    run_phase_g_demo()
