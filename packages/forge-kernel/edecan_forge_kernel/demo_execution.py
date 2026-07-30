"""Demostración y Validación de la Fase E — Ejecución, Sandbox, Taint, Secretos y PTY."""

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


def run_phase_e_demo():
    print("============================================================")
    print("       DEMO DE LA FASE E: EJECUCIÓN, POLICY Y SEGURIDAD     ")
    print("============================================================\n")

    # 1. Terminal Total PTY
    print("1. Probando Terminal Total PTY (entorno real, PTY)...")
    runner = TotalTerminalRunner()
    res = runner.run_pty_command("echo 'Forge Terminal PTY Active' && pwd")
    print(f"   Exit Code: {res.exit_code}")
    print(f"   Salida PTY:\n{res.output.strip()}")
    assert res.exit_code == 0
    assert "Forge Terminal PTY Active" in res.output
    print("   [OK] Terminal Total PTY funcionando sobre el entorno del usuario.\n")

    # 2. Clasificación de Comandos e Inyección de Prompts (Taint)
    print("2. Probando Clasificación de Comandos y elevación por Taint...")

    cmd_safe = "echo 'Hello World'"
    class_safe = ExecutionPolicyEngine.classify_command(cmd_safe, taint_state=None)
    print(f"   Comando normal '{cmd_safe}': EffectClass = {class_safe.name}")
    assert class_safe == EffectClass.SAFE

    # Comando derivado de contenido no confiable (NETWORK)
    taint_untrusted = TaintState(session_id="s1", hwm=TrustLevel.NETWORK)
    class_elevated = ExecutionPolicyEngine.classify_command(cmd_safe, taint_state=taint_untrusted)
    print(f"   Comando '{cmd_safe}' con hwm=NETWORK: EffectClass = {class_elevated.name}")
    assert class_elevated >= EffectClass.IRREVERSIBLE
    assert ExecutionPolicyEngine.requires_approval(class_elevated)
    print("   [OK] Elevación de riesgo por Taint verificada. Prompt injection bloqueado.\n")

    cmd_danger = "rm -rf /tmp/test_dir"
    class_danger = ExecutionPolicyEngine.classify_command(cmd_danger, taint_state=None)
    print(f"   Comando peligroso '{cmd_danger}': EffectClass = {class_danger.name}")
    assert class_danger == EffectClass.IRREVERSIBLE
    assert ExecutionPolicyEngine.requires_approval(class_danger)
    print("   [OK] Detección de comandos irreversibles aprobada.\n")

    # 3. Redacción de Secretos en Razonamiento
    print("3. Probando Redactor de Secretos en trazas de razonamiento...")
    secret = "sk_live_998877665544332211_supersecret"
    raw_thinking = f"Análisis de clave API = {secret} extraída de .env"

    redactor = SecretRedactor(secrets=[secret])
    redacted_thinking = redactor.redact(raw_thinking)
    print(f"   Original : {raw_thinking}")
    print(f"   Redactado: {redacted_thinking}")
    assert secret not in redacted_thinking
    assert "[REDACTED_SECRET]" in redacted_thinking
    print("   [OK] Redacción de secretos en razonamiento/logs verificada.\n")

    # 4. EffectLedger idempotencia
    print("4. Probando EffectLedger e idempotencia contra ejecuciones dobles...")
    ledger = InMemoryEffectLedger()
    record = ledger.reserve(
        target="git_push",
        key="eff-001",
        spec_digest="b2b:0011223344556677889900aabbccddeeff0011223344556677889900aabbccdd",
    )
    print(f"   Efecto reservado id = {record.key}, estado = {record.status}")

    # Commit y re-reserva
    cas_ref = "b2b:1111222233334444555566667777888899990000aaaabbbbccccddddeeeeffff"
    committed = ledger.commit(key="eff-001", outcome_ref=cas_ref)
    print(f"   Efecto completado estado = {committed.status}")
    assert committed.status == "committed"

    second_attempt = ledger.reserve(
        target="git_push",
        key="eff-001",
        spec_digest="b2b:0011223344556677889900aabbccddeeff0011223344556677889900aabbccdd",
    )
    print(f"   Intento de re-ejecución tras commit: estado = {second_attempt.status}")
    assert second_attempt.status == "committed"
    print("   [OK] Idempotencia de EffectLedger verificada.\n")

    print("============================================================")
    print("          VEREDICTO FASE E: EJECUCIÓN Y POLICY EN VERDE     ")
    print("============================================================\n")


if __name__ == "__main__":
    run_phase_e_demo()
