"""Prueba de humo (con respx) y verificación del banco de tareas.

La prueba de humo existe para separar cuatro fallos que se confunden entre sí y
cuestan una tarde cada uno: no hay token, no hay cuenta, el token no vale, el
modelo existe pero esta cuenta no tiene acceso. Cada uno tiene aquí su test.

Ninguna de estas llamadas sale a la red: el router de respx del `conftest` las
intercepta y revienta si alguna se escapa.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx
import pytest
import respx
from edecan_forge_probe.modelcard import BenchTask, Criterio
from edecan_forge_probe.runner import (
    BancoNoDisponible,
    cargar_banco,
    evaluar_criterio,
    prueba_de_humo,
    verificar_banco,
)

URL_WORKERS_AI = r"https://api\.cloudflare\.com/client/v4/accounts/.+/ai/run/.+"

requiere_integracion = pytest.mark.skipif(
    os.environ.get("FORGE_PROBE_INTEGRACION") != "1",
    reason=(
        "Test de integración: gasta dinero real. Actívalo con FORGE_PROBE_INTEGRACION=1. "
        "Tener el token en el entorno NO basta como condición."
    ),
)

ENTORNO = {"CLOUDFLARE_ACCOUNT_ID": "cuenta-de-prueba", "CLOUDFLARE_API_TOKEN": "no-es-un-token"}

RESPUESTA_OK = {
    "success": True,
    "result": {
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": "listo", "reasoning_content": "..."},
            }
        ],
        "usage": {
            "prompt_tokens": 40,
            "completion_tokens": 65,
            "prompt_tokens_details": {"cached_tokens": 0},
            "completion_tokens_details": {"reasoning_tokens": 57},
            "neurons": 3.5,
        },
    },
}


@pytest.fixture
def humo_directo(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fuerza la ruta cruda de la prueba de humo, sin delegar en `providers`.

    Las dos rutas —la delegada y la directa— tienen que dar el mismo veredicto,
    así que las dos se prueban.
    """
    import edecan_forge_probe.runner as runner

    async def _sin_delegar(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(runner, "_humo_con_proveedor", _sin_delegar)


# --------------------------------------------------------------------------- #
# Fallos que no gastan ni una llamada
# --------------------------------------------------------------------------- #


async def test_humo_sin_token_no_llama_a_nadie() -> None:
    res = await prueba_de_humo(entorno={"CLOUDFLARE_ACCOUNT_ID": "x"})
    assert res.ok is False
    assert res.codigo == "falta_token"
    assert "CLOUDFLARE_API_TOKEN" in res.mensaje
    assert res.remedio


async def test_humo_sin_cuenta() -> None:
    res = await prueba_de_humo(entorno={"CLOUDFLARE_API_TOKEN": "algo"})
    assert res.codigo == "falta_cuenta"


async def test_el_token_nunca_aparece_en_la_salida() -> None:
    """Un token en un mensaje de error sigue siendo un token filtrado."""
    secreto = "cf-token-secretisimo-1234567890"
    res = await prueba_de_humo(
        entorno={"CLOUDFLARE_ACCOUNT_ID": "", "CLOUDFLARE_API_TOKEN": secreto}
    )
    assert secreto not in res.model_dump_json()


# --------------------------------------------------------------------------- #
# Diagnóstico contra la API (mockeada)
# --------------------------------------------------------------------------- #


async def test_humo_ok(red: respx.MockRouter, humo_directo: None) -> None:
    red.post(url__regex=URL_WORKERS_AI).mock(return_value=httpx.Response(200, json=RESPUESTA_OK))
    res = await prueba_de_humo(entorno=ENTORNO)
    assert res.ok is True and res.codigo == "ok"
    assert res.contenido == "listo"
    assert res.razonamiento_presente is True
    assert res.uso is not None and res.uso.razonamiento == 57


async def test_humo_ok_delegando_en_el_proveedor(red: respx.MockRouter) -> None:
    """La ruta que usa `providers.WorkersAIProvider` da el mismo veredicto."""
    red.post(url__regex=URL_WORKERS_AI).mock(return_value=httpx.Response(200, json=RESPUESTA_OK))
    res = await prueba_de_humo(entorno=ENTORNO)
    assert res.ok is True and res.codigo == "ok"
    assert res.contenido == "listo"


async def test_humo_distingue_sin_acceso_de_credencial_mala(red: respx.MockRouter) -> None:
    """403 con `code: 5018` es «el modelo no es tuyo», no «tu token no vale»."""
    red.post(url__regex=URL_WORKERS_AI).mock(
        return_value=httpx.Response(
            403, json={"success": False, "errors": [{"code": 5018, "message": "no such model"}]}
        )
    )
    res = await prueba_de_humo(entorno=ENTORNO, modelo="@cf/moonshotai/kimi-k3")
    assert res.codigo == "sin_acceso"
    assert "no tiene acceso" in res.mensaje.lower() or "NO tiene acceso" in res.mensaje
    assert "FORGE_PROBE_MODEL" in (res.remedio or "")


async def test_humo_credencial_invalida(red: respx.MockRouter, humo_directo: None) -> None:
    red.post(url__regex=URL_WORKERS_AI).mock(
        return_value=httpx.Response(
            401, json={"success": False, "errors": [{"code": 10000, "message": "auth"}]}
        )
    )
    res = await prueba_de_humo(entorno=ENTORNO)
    assert res.codigo == "credencial_invalida"


async def test_humo_modelo_inexistente(red: respx.MockRouter, humo_directo: None) -> None:
    red.post(url__regex=URL_WORKERS_AI).mock(
        return_value=httpx.Response(404, json={"success": False, "errors": []})
    )
    res = await prueba_de_humo(entorno=ENTORNO, modelo="@cf/inventado/no-existe")
    assert res.codigo == "modelo_inexistente"


async def test_humo_avisa_cuando_el_razonamiento_se_come_la_respuesta(
    red: respx.MockRouter, humo_directo: None
) -> None:
    """Medido: con `max_tokens` corto llega `content` vacío y se cobra igual."""
    cuerpo = {
        "success": True,
        "result": {
            "choices": [{"message": {"content": "", "reasoning_content": "pensando mucho"}}],
            "usage": {
                "prompt_tokens": 40,
                "completion_tokens": 32,
                "completion_tokens_details": {"reasoning_tokens": 32},
            },
        },
    }
    red.post(url__regex=URL_WORKERS_AI).mock(return_value=httpx.Response(200, json=cuerpo))
    res = await prueba_de_humo(entorno=ENTORNO)
    assert res.ok is True
    assert "max_tokens" in res.mensaje


async def test_humo_red_caida(red: respx.MockRouter, humo_directo: None) -> None:
    red.post(url__regex=URL_WORKERS_AI).mock(side_effect=httpx.ConnectError("sin dns"))
    res = await prueba_de_humo(entorno=ENTORNO)
    assert res.codigo == "red"
    assert res.ok is False


# --------------------------------------------------------------------------- #
# Banco de tareas
# --------------------------------------------------------------------------- #


def _tarea(criterios: list[Criterio]) -> BenchTask:
    return BenchTask(
        id="t1",
        repo="edecan",
        titulo="tarea de prueba",
        enunciado="haz algo",
        clase="trivial",
        criterios=criterios,
    )


def test_criterio_file_exists(tmp_path: Path) -> None:
    criterio = Criterio(kind="file_exists", descripcion="existe x", ruta="x.txt")
    assert evaluar_criterio(criterio, tmp_path, task_id="t", indice=0).pasa is False
    (tmp_path / "x.txt").write_text("hola", encoding="utf-8")
    assert evaluar_criterio(criterio, tmp_path, task_id="t", indice=0).pasa is True


def test_criterio_file_contains_usa_regex(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("def suma(a, b):\n    return a + b\n", encoding="utf-8")
    criterio = Criterio(
        kind="file_contains", descripcion="hay resta", ruta="a.py", patron=r"def resta\("
    )
    assert evaluar_criterio(criterio, tmp_path, task_id="t", indice=0).pasa is False
    criterio_ok = criterio.model_copy(update={"patron": r"def suma\("})
    assert evaluar_criterio(criterio_ok, tmp_path, task_id="t", indice=0).pasa is True


def test_criterio_command_usa_argv_sin_shell(tmp_path: Path) -> None:
    # `sys.executable`, no `"python"`: un shim de pyenv sin versión activa
    # falla con "command not found" y el criterio dejaba de medir lo que
    # promete (el exit code del comando, no la resolución del intérprete).
    ok = Criterio(
        kind="command",
        descripcion="sale 0",
        comando=[sys.executable, "-c", "raise SystemExit(0)"],
    )
    mal = Criterio(
        kind="command",
        descripcion="sale 1",
        comando=[sys.executable, "-c", "raise SystemExit(1)"],
    )
    assert evaluar_criterio(ok, tmp_path, task_id="t", indice=0).pasa is True
    assert evaluar_criterio(mal, tmp_path, task_id="t", indice=0).pasa is False


def test_criterio_sin_los_campos_que_necesita_es_un_error_no_un_aprobado(tmp_path: Path) -> None:
    criterio = Criterio(kind="command", descripcion="sin comando")
    res = evaluar_criterio(criterio, tmp_path, task_id="t", indice=0)
    assert res.pasa is False
    assert res.error is not None


def test_criterio_sobre_una_raiz_inexistente_no_pasa(tmp_path: Path) -> None:
    criterio = Criterio(kind="file_exists", descripcion="x", ruta="x")
    res = evaluar_criterio(criterio, tmp_path / "no-existe", task_id="t", indice=0)
    assert res.pasa is False and res.error is not None


def test_verificar_banco_delata_una_tarea_que_ya_esta_hecha(tmp_path: Path) -> None:
    """El vicio clásico de un banco: criterios que se cumplen antes de empezar."""
    (tmp_path / "ya.txt").write_text("hecho", encoding="utf-8")
    tarea = _tarea(
        [
            Criterio(kind="file_exists", descripcion="ya está", ruta="ya.txt"),
            Criterio(kind="file_exists", descripcion="falta", ruta="falta.txt"),
        ]
    )
    resultados = verificar_banco([tarea], {"edecan": tmp_path})
    assert [r.pasa for r in resultados] == [True, False]
    assert resultados[0].mide_algo is False
    assert resultados[1].mide_algo is True


def test_verificar_banco_ignora_criterios_que_no_deben_fallar_antes(tmp_path: Path) -> None:
    tarea = _tarea(
        [Criterio(kind="file_exists", descripcion="da igual", ruta="x", debe_fallar_antes=False)]
    )
    assert verificar_banco([tarea], {"edecan": tmp_path}) == []


def test_verificar_banco_sin_raiz_configurada_lo_dice(tmp_path: Path) -> None:
    tarea = _tarea([Criterio(kind="file_exists", descripcion="x", ruta="x")])
    tarea = tarea.model_copy(update={"repo": "unconfigured"})
    resultados = verificar_banco([tarea], {"unconfigured": None})  # type: ignore[dict-item]
    assert resultados[0].error is not None


def test_cargar_banco_falla_claro_si_no_hay_modulo() -> None:
    with pytest.raises(BancoNoDisponible, match="BenchTask"):
        cargar_banco("edecan_forge_probe.banco_que_no_existe")


# --------------------------------------------------------------------------- #
# Integración: ESTO SÍ GASTA DINERO
# --------------------------------------------------------------------------- #


@pytest.mark.integration
@requiere_integracion
async def test_humo_contra_workers_ai_de_verdad() -> None:
    """Una sola llamada real. Sólo corre con FORGE_PROBE_INTEGRACION=1.

    Tener el token en el entorno no basta: un `pytest` distraído no debe poder
    convertirse en una factura.
    """
    res = await prueba_de_humo()
    assert res.codigo in {"ok", "sin_acceso", "modelo_inexistente", "credencial_invalida"}
    if res.ok:
        assert res.uso is not None and res.uso.salida > 0
