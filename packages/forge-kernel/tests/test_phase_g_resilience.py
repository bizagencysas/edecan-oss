"""Unit tests for Phase G: Resilience, Stuck Agent Detection and TUI."""

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


def test_stuck_agent_detector():
    with tempfile.TemporaryDirectory() as tmp_dir:
        vfs = Vfs(cas=Cas(root=Path(tmp_dir)))
        detector = StuckAgentDetector(max_no_progress_turns=3)

        assert not detector.record_turn(vfs, "list_dir", {})
        assert not detector.record_turn(vfs, "list_dir", {})
        assert detector.record_turn(vfs, "list_dir", {})


def test_recovery_prompt_generation():
    hint = AutoHealingEngine.generate_recovery_prompt("run_command", retry_count=2)
    assert "run_command" in hint
    assert "SYSTEM RECOVERY HINT" in hint


def test_tui_dashboard():
    dashboard = TuiStateInspector.format_status_dashboard(
        session_id="test-sess",
        vfs_version=1,
        effect_class="REVERSIBLE",
        turns_count=3,
    )
    assert "test-sess" in dashboard
    assert "REVERSIBLE" in dashboard
