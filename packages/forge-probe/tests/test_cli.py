"""Tests del CLI: códigos de salida, `.env` y encadenado en scripts.

Un CLI de medición se usa dentro de un script, así que su contrato real no es lo
que imprime: son sus códigos de salida. Aquí se fijan. Todos los tests pasan
`--sin-env` o un `.env` de mentira, porque cargar el `.env` real del repositorio
metería el token de verdad en la suite.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx
from edecan_forge_probe import __main__ as cli
from edecan_forge_probe.modelcard import BenchTask, Criterio, ProbeResult
from edecan_forge_probe.runner import BancoNoDisponible, ContextoSonda, Sonda, cargar_banco

URL_WORKERS_AI = r"https://api\.cloudflare\.com/client/v4/accounts/.+/ai/run/.+"


def sonda_falsa(nombre: str, detalle: dict[str, object] | None = None) -> Sonda:
    async def _ejecutar(ctx: ContextoSonda) -> ProbeResult:
        ctx.registrar_uso(entrada=10, salida=5)
        return ProbeResult(probe=nombre, ok=True, valor=1.0, detalle=detalle or {})

    return Sonda(nombre=nombre, ejecutar=_ejecutar)


# --------------------------------------------------------------------------- #
# .env
# --------------------------------------------------------------------------- #


def test_cargar_dotenv_no_pisa_lo_que_ya_esta_exportado(tmp_path: Path) -> None:
    """El entorno del operador manda sobre el archivo. Siempre."""
    ruta = tmp_path / ".env"
    ruta.write_text(
        "# comentario\nexport CLAVE_A='del-archivo'\nCLAVE_B=\"otro\"\nbasura\n", encoding="utf-8"
    )
    entorno = {"CLAVE_A": "del-entorno"}
    leidas = cli.cargar_dotenv(ruta, entorno=entorno)
    assert entorno["CLAVE_A"] == "del-entorno"
    assert entorno["CLAVE_B"] == "otro"
    assert leidas == ["CLAVE_B"]


def test_cargar_dotenv_inexistente_no_revienta(tmp_path: Path) -> None:
    assert cli.cargar_dotenv(tmp_path / "no-hay") == []


def test_buscar_dotenv_sube_directorios(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("A=1\n", encoding="utf-8")
    hondo = tmp_path / "a" / "b"
    hondo.mkdir(parents=True)
    assert cli.buscar_dotenv(hondo) == tmp_path / ".env"


def test_la_revision_por_defecto_identifica_el_codigo() -> None:
    revision = cli.revision_por_defecto()
    assert isinstance(revision, str) and revision


# --------------------------------------------------------------------------- #
# humo
# --------------------------------------------------------------------------- #


def test_humo_sin_credencial_sale_con_5(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--sin-env", "humo"]) == cli.CODIGO_SIN_CREDENCIAL
    salida = capsys.readouterr().out
    assert "CLOUDFLARE_API_TOKEN" in salida
    assert "qué hacer:" in salida


def test_humo_sin_acceso_sale_con_6(
    red: respx.MockRouter, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "cuenta")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "no-es-un-token")
    red.post(url__regex=URL_WORKERS_AI).mock(
        return_value=httpx.Response(
            403, json={"success": False, "errors": [{"code": 5018, "message": "sin acceso"}]}
        )
    )
    assert cli.main(["--sin-env", "humo", "--modelo", "@cf/moonshotai/kimi-k3"]) == (
        cli.CODIGO_SIN_ACCESO
    )
    assert "5018" in capsys.readouterr().out


def test_humo_ok_sale_con_0_y_en_json(
    red: respx.MockRouter, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "cuenta")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "no-es-un-token")
    red.post(url__regex=URL_WORKERS_AI).mock(
        return_value=httpx.Response(
            200,
            json={
                "success": True,
                "result": {
                    "choices": [{"message": {"content": "listo", "reasoning_content": "x"}}],
                    "usage": {"prompt_tokens": 30, "completion_tokens": 40},
                },
            },
        )
    )
    assert cli.main(["--sin-env", "humo", "--json"]) == cli.CODIGO_OK
    salida = capsys.readouterr().out
    assert '"ok": true' in salida
    assert "no-es-un-token" not in salida


# --------------------------------------------------------------------------- #
# sondear
# --------------------------------------------------------------------------- #


def test_sondear_sin_sondas_lo_dice_y_falla(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setattr(cli, "descubrir_sondas", lambda: ())
    codigo = cli.main(["--sin-env", "sondear", "--evidencia", str(tmp_path)])
    assert codigo == cli.CODIGO_ERROR
    assert "No hay sondas instaladas" in capsys.readouterr().err


def test_sondear_parcial_es_no_go_y_escribe_las_dos_salidas(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Medir poco no aprueba: los huecos bloquean igual que un fallo."""
    monkeypatch.setattr(cli, "descubrir_sondas", lambda: (sonda_falsa("contexto.aguja"),))
    codigo = cli.main(
        [
            "--sin-env",
            "sondear",
            "--evidencia",
            str(tmp_path),
            "--revision",
            "rev-test",
            "--precio-entrada",
            "0.95",
            "--precio-salida",
            "4.00",
        ]
    )
    assert codigo == cli.CODIGO_NO_GO
    assert (tmp_path / "modelcard.json").is_file()
    assert (tmp_path / "informe.md").is_file()
    assert (tmp_path / "contabilidad.json").is_file()
    salida = capsys.readouterr().out
    assert "VEREDICTO: NO-GO" in salida
    assert "USD" in salida


def test_sondear_go_cuando_las_sondas_publican_todo(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    completa = sonda_falsa(
        "todo",
        {
            "modelcard": {
                "usable_context_tokens": 120_000,
                "throughput_tps": 40.0,
                "max_tools_effective": 16,
                "bench_success": {"successes": 19, "trials": 20},
                "native_tools": {"code_blob": {"successes": 200, "trials": 200}},
                "ttft": {"p50": 0.5, "p95": 1.0, "muestras": 20},
            }
        },
    )
    monkeypatch.setattr(cli, "descubrir_sondas", lambda: (completa,))
    codigo = cli.main(
        ["--sin-env", "sondear", "--evidencia", str(tmp_path), "--revision", "rev-test"]
    )
    assert codigo == cli.CODIGO_OK


def test_presupuesto_sin_precios_es_un_error_de_uso_claro(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "descubrir_sondas", lambda: (sonda_falsa("a"),))
    codigo = cli.main(
        ["--sin-env", "sondear", "--evidencia", str(tmp_path), "--presupuesto-usd", "1"]
    )
    assert codigo == cli.CODIGO_ERROR
    assert "precio-entrada" in capsys.readouterr().err


def test_solo_ejecuta_el_grupo_pedido(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    corridas: list[str] = []

    def _sonda(nombre: str) -> Sonda:
        async def _ejecutar(ctx: ContextoSonda) -> ProbeResult:
            corridas.append(nombre)
            return ProbeResult(probe=nombre, ok=True, valor=1.0)

        return Sonda(nombre=nombre, ejecutar=_ejecutar)

    monkeypatch.setattr(
        cli, "descubrir_sondas", lambda: (_sonda("contexto.aguja"), _sonda("tools.code_blob"))
    )
    cli.main(
        [
            "--sin-env",
            "sondear",
            "--evidencia",
            str(tmp_path),
            "--revision",
            "rev-test",
            "--solo",
            "tools",
        ]
    )
    assert corridas == ["tools.code_blob"]


# --------------------------------------------------------------------------- #
# informe
# --------------------------------------------------------------------------- #


def test_informe_sin_evidencia_no_inventa_una_tarjeta(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    codigo = cli.main(["--sin-env", "informe", "--evidencia", str(tmp_path)])
    assert codigo == cli.CODIGO_ERROR
    assert "No hay evidencia reutilizable" in capsys.readouterr().err


def test_informe_recompone_desde_la_evidencia_sin_gastar_nada(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cli, "descubrir_sondas", lambda: (sonda_falsa("contexto.aguja"),))
    cli.main(["--sin-env", "sondear", "--evidencia", str(tmp_path), "--revision", "rev-test"])
    (tmp_path / "informe.md").unlink()

    codigo = cli.main(
        [
            "--sin-env",
            "informe",
            "--evidencia",
            str(tmp_path),
            "--revision",
            "rev-test",
            "--salida",
            str(tmp_path / "informe2"),
        ]
    )
    assert codigo == cli.CODIGO_NO_GO
    texto = (tmp_path / "informe2" / "informe.md").read_text(encoding="utf-8")
    assert "recompuesto desde evidencia" in texto


# --------------------------------------------------------------------------- #
# banco
# --------------------------------------------------------------------------- #


def _tarea(criterios: list[Criterio]) -> BenchTask:
    return BenchTask(
        id="edecan-1",
        repo="edecan",
        titulo="arreglar algo",
        enunciado="arréglalo",
        clase="standard",
        lenguajes=["python"],
        criterios=criterios,
    )


def test_banco_listar(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    tarea = _tarea([Criterio(kind="file_exists", descripcion="x", ruta="x")])
    monkeypatch.setattr(cli, "cargar_banco", lambda: (tarea,))
    assert cli.main(["--sin-env", "banco", "--listar"]) == cli.CODIGO_OK
    salida = capsys.readouterr().out
    assert "edecan-1" in salida and "arreglar algo" in salida


def test_banco_verificar_delata_criterios_que_ya_pasan(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "ya.txt").write_text("hecho", encoding="utf-8")
    tarea = _tarea([Criterio(kind="file_exists", descripcion="ya está", ruta="ya.txt")])
    monkeypatch.setattr(cli, "cargar_banco", lambda: (tarea,))
    codigo = cli.main(["--sin-env", "banco", "--verificar", "--raiz-edecan", str(tmp_path)])
    assert codigo == cli.CODIGO_BANCO_INUTIL
    assert "YA PASA" in capsys.readouterr().out


def test_banco_verificar_ok_cuando_todo_falla_hoy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tarea = _tarea([Criterio(kind="file_exists", descripcion="falta", ruta="falta.txt")])
    monkeypatch.setattr(cli, "cargar_banco", lambda: (tarea,))
    codigo = cli.main(["--sin-env", "banco", "--verificar", "--raiz-edecan", str(tmp_path)])
    assert codigo == cli.CODIGO_OK
    assert "el banco mide algo" in capsys.readouterr().out


def test_banco_sin_modulo_falla_claro(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def _sin_banco() -> tuple[BenchTask, ...]:
        raise BancoNoDisponible("no hay banco de tareas instalado")

    monkeypatch.setattr(cli, "cargar_banco", _sin_banco)
    assert cli.main(["--sin-env", "banco", "--listar"]) == cli.CODIGO_ERROR
    assert "banco de tareas" in capsys.readouterr().err


def test_banco_listar_encuentra_el_banco_real_del_paquete(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Sin dobles: si el paquete trae banco, `--listar` lo enseña."""
    try:
        tareas = cargar_banco()
    except BancoNoDisponible:
        pytest.skip("todavía no hay módulo de banco en el paquete")
    assert cli.main(["--sin-env", "banco", "--listar"]) == cli.CODIGO_OK
    salida = capsys.readouterr().out
    assert tareas[0].id in salida


def test_banco_exige_elegir_listar_o_verificar() -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["--sin-env", "banco"])
    assert exc.value.code == 2
