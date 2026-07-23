from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from edecan_toolkit.seguridad import (
    AuditarSeguridadProyectoTool,
    EjecutarPentestGPTAutorizadoTool,
    _normalize_target,
)


def _settings(repo: Path, data_dir: Path, **overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "EDECAN_LOCAL_MODE": True,
        "EDECAN_LOCAL_REPO_PATH": str(repo),
        "DATA_DIR": str(data_dir),
        "PENTESTGPT_BINARY": None,
        "PENTESTGPT_TIMEOUT_SECONDS": 60,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_pentest_activo_es_dangerous_y_auditoria_estatica_no() -> None:
    assert AuditarSeguridadProyectoTool().dangerous is False
    assert EjecutarPentestGPTAutorizadoTool().dangerous is True


def test_normaliza_objetivo_y_rechaza_credenciales() -> None:
    assert _normalize_target("Example.COM/") == "https://example.com"
    assert _normalize_target("http://127.0.0.1:8080/app/") == "http://127.0.0.1:8080/app"
    assert _normalize_target("https://usuario:clave@example.com") is None
    assert _normalize_target("https://example.com/#fragment") is None


@pytest.mark.asyncio
async def test_auditoria_detecta_sin_revelar_el_secreto(make_ctx, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    secret = "sk-prohibido-12345678901234567890"
    (repo / "config.py").write_text(f'API_KEY = "{secret}"\n', encoding="utf-8")
    (repo / ".env").write_text(f"API_KEY={secret}\n", encoding="utf-8")
    ctx = make_ctx(settings=_settings(repo, tmp_path / "data"))

    result = await AuditarSeguridadProyectoTool().run(ctx, {"ruta": "."})

    assert result.data is not None
    assert result.data["summary"]["findings"] >= 2
    assert secret not in result.content
    assert secret not in str(result.data)
    assert {finding["rule"] for finding in result.data["findings"]} >= {
        "hardcoded-secret",
        "sensitive-file",
    }


@pytest.mark.asyncio
async def test_auditoria_rechaza_escape_de_ruta(make_ctx, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    ctx = make_ctx(settings=_settings(repo, tmp_path / "data"))

    result = await AuditarSeguridadProyectoTool().run(ctx, {"ruta": ".."})

    assert "no es un directorio dentro" in result.content


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("confirmation", "scope"),
    [(False, "https://example.com"), (True, "https://otro.example.com")],
)
async def test_pentest_requiere_autorizacion_y_alcance_exacto(
    make_ctx,
    tmp_path: Path,
    confirmation: bool,
    scope: str,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    ctx = make_ctx(settings=_settings(repo, tmp_path / "data"))

    result = await EjecutarPentestGPTAutorizadoTool().run(
        ctx,
        {
            "objetivo": "https://example.com",
            "alcance_autorizado": scope,
            "confirmo_que_tengo_autorizacion": confirmation,
        },
    )

    assert "no se ejecutó" in result.content.lower()


@pytest.mark.asyncio
async def test_pentest_no_instala_dependencias_automaticamente(make_ctx, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    ctx = make_ctx(
        settings=_settings(repo, tmp_path / "data", PENTESTGPT_BINARY="/no/existe/pentestgpt")
    )

    result = await EjecutarPentestGPTAutorizadoTool().run(
        ctx,
        {
            "objetivo": "https://example.com",
            "alcance_autorizado": "https://example.com",
            "confirmo_que_tengo_autorizacion": True,
        },
    )

    assert "no está instalado" in result.content


@pytest.mark.asyncio
async def test_pentest_guarda_reporte_saneado(
    make_ctx, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    binary = tmp_path / "pentestgpt"
    binary.write_text("binario simulado", encoding="utf-8")
    binary.chmod(0o700)
    data_dir = tmp_path / "data"
    ctx = make_ctx(settings=_settings(repo, data_dir, PENTESTGPT_BINARY=str(binary)))

    async def fake_run(*args, **kwargs):
        return 0, "reporte defensivo sin secretos", False

    monkeypatch.setattr("edecan_toolkit.seguridad._run_pentestgpt", fake_run)

    result = await EjecutarPentestGPTAutorizadoTool().run(
        ctx,
        {
            "objetivo": "https://example.com",
            "alcance_autorizado": "https://example.com/",
            "confirmo_que_tengo_autorizacion": True,
            "instruccion": "Revisa solo la aplicación web propia.",
        },
    )

    assert result.data is not None
    report_path = Path(result.data["report_path"])
    assert report_path.is_file()
    report = report_path.read_text(encoding="utf-8")
    assert "reporte defensivo sin secretos" in report
    assert "--target" not in report
