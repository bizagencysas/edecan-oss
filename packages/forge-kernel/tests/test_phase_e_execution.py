"""Unit tests for Phase E: Execution, Policy, Taint, Secrets and PTY."""

from __future__ import annotations

from edecan_forge_kernel.contracts import (
    EffectClass,
    TaintState,
    TrustLevel,
)
from edecan_forge_kernel.execution import (
    ExecutionPolicyEngine,
    InMemoryEffectLedger,
    SecretRedactor,
    TotalTerminalRunner,
)


def test_classify_commands():
    # Safe command
    cls_safe = ExecutionPolicyEngine.classify_command("echo hello")
    assert cls_safe == EffectClass.SAFE
    assert not ExecutionPolicyEngine.requires_approval(cls_safe)

    # Reversible command
    cls_mutating = ExecutionPolicyEngine.classify_command("git commit -m 'msg'")
    assert cls_mutating == EffectClass.REVERSIBLE
    assert not ExecutionPolicyEngine.requires_approval(cls_mutating)

    # Irreversible command
    cls_danger = ExecutionPolicyEngine.classify_command("rm -rf /")
    assert cls_danger == EffectClass.IRREVERSIBLE
    assert ExecutionPolicyEngine.requires_approval(cls_danger)


def test_taint_elevation():
    cmd = "echo hello"
    # Taint from network/untrusted content
    taint = TaintState(session_id="s1", hwm=TrustLevel.NETWORK)
    cls_elevated = ExecutionPolicyEngine.classify_command(cmd, taint_state=taint)

    assert cls_elevated == EffectClass.IRREVERSIBLE
    assert ExecutionPolicyEngine.requires_approval(cls_elevated)


def test_secret_redaction():
    secret = "sk-proj-1234567890abcdef"
    redactor = SecretRedactor(secrets=[secret])

    text = f"Using API key {secret} to authorize request"
    redacted = redactor.redact(text)

    assert secret not in redacted
    assert "[REDACTED_SECRET]" in redacted


def test_effect_ledger_idempotency():
    ledger = InMemoryEffectLedger()
    key = "eff-100"

    # Reserve
    rec1 = ledger.reserve(target="deploy", key=key, spec_digest="hash123")
    assert rec1.status == "reserved"

    # Commit
    rec2 = ledger.commit(key=key, outcome_ref="cas-456")
    assert rec2.status == "committed"

    # Second reserve returns committed status
    rec3 = ledger.reserve(target="deploy", key=key, spec_digest="hash123")
    assert rec3.status == "committed"


def test_pty_runner():
    runner = TotalTerminalRunner()
    res = runner.run_pty_command("echo 'PTY_TEST_OK'")
    assert res.exit_code == 0
    assert "PTY_TEST_OK" in res.output
