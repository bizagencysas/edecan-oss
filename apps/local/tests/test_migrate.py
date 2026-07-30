"""`edecan_local.migrate` — descubrimiento del directorio de Alembic +
`run_migrations` (ARCHITECTURE.md §12f, WP-V3-05).

Offline y determinista: nunca se conecta a Postgres de verdad.
`alembic.command.upgrade` se monkeypatchea (la única llamada real que haría
falta interceptar) — todo lo demás (`find_alembic_dir`, el `Config` que se
arma) se ejercita tal cual.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from edecan_local import migrate


@pytest.fixture(autouse=True)
def _sin_meipass(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ningún test de este módulo debe heredar un `sys._MEIPASS` real (p.
    ej. si algún día se corre la suite empaquetada con PyInstoller) — se
    fuerza a "no frozen" salvo que un test lo fije explícitamente."""
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)


# ---------------------------------------------------------------------------
# _candidate_dirs / find_alembic_dir — orden de descubrimiento
# ---------------------------------------------------------------------------


def test_candidate_dirs_sin_nada_fijado_solo_trae_la_ruta_del_repo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("EDECAN_ALEMBIC_DIR", raising=False)
    candidates = migrate._candidate_dirs()
    assert len(candidates) == 1
    assert candidates[0].name == "alembic"
    assert candidates[0].parent.name == "db"


def test_candidate_dirs_respeta_el_orden_env_meipass_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_dir = tmp_path / "desde-env"
    meipass_dir = tmp_path / "desde-meipass"
    monkeypatch.setenv("EDECAN_ALEMBIC_DIR", str(env_dir))
    monkeypatch.setattr(sys, "_MEIPASS", str(meipass_dir), raising=False)

    candidates = migrate._candidate_dirs()

    assert candidates[0] == env_dir
    assert candidates[1] == meipass_dir / "alembic"
    assert candidates[2].name == "alembic"  # la ruta del repo, tercera


def test_find_alembic_dir_usa_edecan_alembic_dir_si_tiene_env_py(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    custom_dir = tmp_path / "mi-alembic"
    custom_dir.mkdir()
    (custom_dir / "env.py").write_text("# fake", encoding="utf-8")
    monkeypatch.setenv("EDECAN_ALEMBIC_DIR", str(custom_dir))

    assert migrate.find_alembic_dir() == custom_dir


def test_find_alembic_dir_salta_edecan_alembic_dir_vacio_y_sigue_probando(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`EDECAN_ALEMBIC_DIR` apunta a una carpeta SIN `env.py` -- no debe
    ganar solo por estar primero en la lista; sigue probando el resto."""
    carpeta_vacia = tmp_path / "vacia"
    carpeta_vacia.mkdir()
    monkeypatch.setenv("EDECAN_ALEMBIC_DIR", str(carpeta_vacia))

    encontrado = migrate.find_alembic_dir()

    assert encontrado != carpeta_vacia
    assert (encontrado / "env.py").is_file()


def test_find_alembic_dir_usa_meipass_cuando_esta_congelado(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("EDECAN_ALEMBIC_DIR", raising=False)
    bundle_dir = tmp_path / "bundle"
    alembic_dir = bundle_dir / "alembic"
    alembic_dir.mkdir(parents=True)
    (alembic_dir / "env.py").write_text("# fake", encoding="utf-8")
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle_dir), raising=False)

    assert migrate.find_alembic_dir() == alembic_dir


def test_find_alembic_dir_cae_a_la_ruta_del_repo_por_defecto(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("EDECAN_ALEMBIC_DIR", raising=False)
    encontrado = migrate.find_alembic_dir()
    assert encontrado.name == "alembic"
    assert (encontrado / "env.py").is_file()
    assert (encontrado / "versions").is_dir()


def test_find_alembic_dir_lanza_runtime_error_con_detalle_si_nada_sirve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_candidates = [tmp_path / "a", tmp_path / "b"]
    monkeypatch.setattr(migrate, "_candidate_dirs", lambda: fake_candidates)

    with pytest.raises(RuntimeError) as exc_info:
        migrate.find_alembic_dir()

    mensaje = str(exc_info.value)
    assert str(fake_candidates[0]) in mensaje
    assert str(fake_candidates[1]) in mensaje
    assert "EDECAN_ALEMBIC_DIR" in mensaje


# ---------------------------------------------------------------------------
# run_migrations
# ---------------------------------------------------------------------------


def test_run_migrations_arma_config_y_llama_upgrade_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    alembic_dir = tmp_path / "alembic"
    alembic_dir.mkdir()
    (alembic_dir / "env.py").write_text("# fake env.py", encoding="utf-8")
    monkeypatch.setenv("EDECAN_ALEMBIC_DIR", str(alembic_dir))

    import alembic.command

    calls: list[tuple[str | None, str | None, str]] = []

    def fake_upgrade(cfg: object, revision: str) -> None:
        calls.append(
            (
                cfg.get_main_option("script_location"),  # type: ignore[attr-defined]
                cfg.get_main_option("sqlalchemy.url"),  # type: ignore[attr-defined]
                revision,
            )
        )

    monkeypatch.setattr(alembic.command, "upgrade", fake_upgrade)

    database_url = "postgresql+asyncpg://u:p@h:5432/d"
    previous_env = os.environ.get("DATABASE_URL")
    try:
        migrate.run_migrations(database_url)

        assert len(calls) == 1
        script_location, url, revision = calls[0]
        assert script_location == str(alembic_dir)
        assert url == database_url
        assert revision == "head"
        assert os.environ["DATABASE_URL"] == database_url
    finally:
        if previous_env is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_env


def test_run_migrations_sin_alembic_dir_valido_lanza_runtime_error_antes_de_tocar_alembic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(migrate, "_candidate_dirs", lambda: [tmp_path / "no-existe"])

    import alembic.command

    def _no_deberia_llamarse(cfg: object, revision: str) -> None:
        raise AssertionError("upgrade() no debe llamarse si no hay script_location válido")

    monkeypatch.setattr(alembic.command, "upgrade", _no_deberia_llamarse)

    with pytest.raises(RuntimeError):
        migrate.run_migrations("postgresql+asyncpg://u:p@h:5432/d")
