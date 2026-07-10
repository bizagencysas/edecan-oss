"""Tests de `edecan_companion.config` (siempre con `path=` en tmp_path; nunca ~/.edecan real)."""

from __future__ import annotations

from pathlib import Path

from edecan_companion.config import load_config


def test_load_config_creates_file_with_safe_defaults_when_missing(tmp_path: Path):
    config_path = tmp_path / "companion.yaml"
    assert not config_path.exists()

    config = load_config(config_path)

    assert config_path.exists()
    assert config.allowed_apps == []
    assert config.allowed_commands == []
    assert config.auto_approve == []
    # A diferencia de las listas blancas de arriba, remember/ide_enabled NO
    # empiezan "todo apagado": el IDE se comporta como cualquier otra acción
    # (sigue pidiendo aprobación) apenas se instala, así que no hace falta
    # que el usuario lo prenda a mano; remember_approvals_minutes sí empieza
    # apagado (0) porque cambia CÓMO se pregunta, no si se pregunta.
    assert config.remember_approvals_minutes == 0
    assert config.ide_enabled is True
    # Control remoto de teclado/mouse (WP-V4-10): APAGADO por defecto a
    # propósito -- opt-in explícito del dueño de la máquina, ver el
    # comentario de `remote_input_enabled` en `_CONFIG_TEMPLATE`.
    assert config.remote_input_enabled is False
    assert config.remote_input_remember_minutes == 10
    assert config.approval_memory == {}
    assert config.config_path == config_path
    assert config.audit_log_path == tmp_path / "companion.log"
    # El sandbox_dir default se crea y queda resuelto (realpath) y existente.
    assert config.sandbox_dir.is_absolute()
    assert config.sandbox_dir.exists()
    assert config.sandbox_dir.is_dir()


def test_default_config_file_has_spanish_comments_and_is_valid_yaml(tmp_path: Path):
    config_path = tmp_path / "companion.yaml"
    load_config(config_path)

    text = config_path.read_text(encoding="utf-8")
    assert "#" in text
    assert "aprobación" in text

    import yaml

    data = yaml.safe_load(text)
    assert data["allowed_apps"] == []
    assert data["allowed_commands"] == []
    assert data["auto_approve"] == []
    assert data["remember_approvals_minutes"] == 0
    assert data["ide_enabled"] is True
    assert data["remote_input_enabled"] is False
    assert data["remote_input_remember_minutes"] == 10


def test_load_config_reads_existing_values_and_resolves_symlinked_sandbox(tmp_path: Path):
    real_sandbox = tmp_path / "real_sandbox"
    real_sandbox.mkdir()
    link = tmp_path / "link_sandbox"
    link.symlink_to(real_sandbox)

    config_path = tmp_path / "companion.yaml"
    config_path.write_text(
        f'sandbox_dir: "{link}"\n'
        'allowed_apps: ["Safari"]\n'
        'allowed_commands: ["ls"]\n'
        'auto_approve: ["read_dir"]\n'
        "remember_approvals_minutes: 15\n"
        "ide_enabled: false\n"
        "remote_input_enabled: true\n"
        "remote_input_remember_minutes: 3\n",
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.sandbox_dir == real_sandbox.resolve()
    assert config.allowed_apps == ["Safari"]
    assert config.allowed_commands == ["ls"]
    assert config.auto_approve == ["read_dir"]
    assert config.remember_approvals_minutes == 15
    assert config.ide_enabled is False
    assert config.remote_input_enabled is True
    assert config.remote_input_remember_minutes == 3


def test_load_config_falls_back_to_defaults_for_malformed_remember_and_ide_enabled(
    tmp_path: Path,
):
    config_path = tmp_path / "companion.yaml"
    config_path.write_text(
        'remember_approvals_minutes: "quince"\nide_enabled: "si-por-favor"\n', encoding="utf-8"
    )

    config = load_config(config_path)

    assert config.remember_approvals_minutes == 0
    assert config.ide_enabled is True


def test_load_config_rejects_negative_remember_approvals_minutes(tmp_path: Path):
    config_path = tmp_path / "companion.yaml"
    config_path.write_text("remember_approvals_minutes: -5\n", encoding="utf-8")

    config = load_config(config_path)

    assert config.remember_approvals_minutes == 0


def test_load_config_falls_back_to_defaults_for_malformed_remote_input_fields(tmp_path: Path):
    config_path = tmp_path / "companion.yaml"
    config_path.write_text(
        'remote_input_enabled: "claro"\nremote_input_remember_minutes: "diez"\n',
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.remote_input_enabled is False
    assert config.remote_input_remember_minutes == 10


def test_load_config_rejects_negative_remote_input_remember_minutes(tmp_path: Path):
    config_path = tmp_path / "companion.yaml"
    config_path.write_text("remote_input_remember_minutes: -1\n", encoding="utf-8")

    config = load_config(config_path)

    assert config.remote_input_remember_minutes == 10


def test_load_config_falls_back_to_empty_lists_on_malformed_types(tmp_path: Path):
    config_path = tmp_path / "companion.yaml"
    config_path.write_text('allowed_apps: "no-es-una-lista"\n', encoding="utf-8")

    config = load_config(config_path)

    assert config.allowed_apps == []


def test_load_config_does_not_crash_on_invalid_yaml(tmp_path: Path):
    config_path = tmp_path / "companion.yaml"
    config_path.write_text("esto: [no cierra\n", encoding="utf-8")

    config = load_config(config_path)

    assert config.allowed_apps == []
    assert config.allowed_commands == []
    assert config.auto_approve == []


def test_load_config_does_not_recreate_existing_file(tmp_path: Path):
    config_path = tmp_path / "companion.yaml"
    config_path.write_text('allowed_commands: ["git"]\n', encoding="utf-8")
    original_mtime = config_path.stat().st_mtime_ns

    load_config(config_path)

    assert config_path.stat().st_mtime_ns == original_mtime
