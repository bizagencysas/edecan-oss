"""Tests del CLI (`edecan_evals.runner.main`) y del reporte (stdout + JSON
artifact). Todos ejercitan rutas que NO tocan `edecan_core` (validación de
argumentos, carga de suites, impresión, escritura de artifact), así que no
necesitan el doble local de `test_runner_offline.py`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from edecan_evals.runner import (
    ResultadoCaso,
    ResultadoSuite,
    escribir_artifact,
    imprimir_resumen,
    main,
)


@pytest.fixture(autouse=True)
def _sin_credenciales_live(monkeypatch: pytest.MonkeyPatch) -> None:
    """Evita que un `.env`/entorno real del desarrollador con `ANTHROPIC_API_KEY`
    haga que estos tests intenten (o parezca que podrían intentar) una llamada
    real — nunca debe activarse `--live` desde `packages/evals/tests/`."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_COMPAT_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_COMPAT_API_KEY", raising=False)


def _resultado(aprobados: int, total: int) -> ResultadoSuite:
    casos = [
        ResultadoCaso(
            caso_id=f"c{i}", aprobado=i < aprobados, razones=[] if i < aprobados else ["falló x"]
        )
        for i in range(total)
    ]
    return ResultadoSuite(suite="demo", total=total, aprobados=aprobados, casos=casos)


def test_imprimir_resumen_muestra_ok_y_fail(capsys: pytest.CaptureFixture[str]) -> None:
    resultado = _resultado(aprobados=1, total=2)
    imprimir_resumen(resultado)
    salida = capsys.readouterr().out
    assert "Suite: demo" in salida
    assert "[OK  ] c0" in salida
    assert "[FAIL] c1" in salida
    assert "falló x" in salida
    assert "Aprobados: 1/2" in salida


def test_escribir_artifact_json_valido(tmp_path: Path) -> None:
    resultado = _resultado(aprobados=2, total=2)
    ruta = escribir_artifact(resultado, directorio=tmp_path)

    assert ruta.exists()
    assert ruta.parent == tmp_path
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    assert datos["suite"] == "demo"
    assert datos["aprobados"] == 2
    assert datos["total"] == 2
    assert len(datos["casos"]) == 2


def test_escribir_artifact_crea_directorio_si_no_existe(tmp_path: Path) -> None:
    directorio = tmp_path / "no-existe-todavia"
    ruta = escribir_artifact(_resultado(1, 1), directorio=directorio)
    assert ruta.exists()


def test_main_suite_inexistente_retorna_2(capsys: pytest.CaptureFixture[str]) -> None:
    codigo = main(["--suite", "no-existe-esta-suite-jamas"])
    assert codigo == 2
    assert "no se pudo cargar" in capsys.readouterr().err.lower()


def test_main_live_sin_credenciales_retorna_2_y_no_llama_nada(
    capsys: pytest.CaptureFixture[str],
) -> None:
    codigo = main(["--suite", "sin_linkedin", "--live"])
    assert codigo == 2
    assert "--live requiere" in capsys.readouterr().err


def test_main_requiere_suite() -> None:
    with pytest.raises(SystemExit):
        main([])
