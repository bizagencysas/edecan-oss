"""El banco de `edecan` cumple su propio contrato.

No prueba el repositorio: prueba que el BANCO está bien formado. Un banco con
un criterio que apunta a un guion inexistente, o con una pista que ya no
existe, mide ruido — y peor, lo mide en silencio.

La comprobación de que cada criterio FALLA sobre el repo intacto no está aquí:
esa es responsabilidad del runner (`Criterio.debe_fallar_antes`) y se hizo a
mano al construir el banco, ejecutando cada comando.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PAQUETE = Path(__file__).resolve().parents[1]
if str(_PAQUETE) not in sys.path:  # pragma: no cover - red de seguridad de imports
    sys.path.insert(0, str(_PAQUETE))

from bench.edecan import TAREAS, por_id, tareas  # noqa: E402
from edecan_forge_probe.modelcard import BenchTask  # noqa: E402

_RAIZ = _PAQUETE.parents[1]

# Archivos que la tarea tiene que CREAR: se acepta que hoy no existan, pero su
# directorio sí, porque una pista a un directorio inexistente es una errata.
_PISTAS_A_CREAR = frozenset(
    {
        "packages/llm/tests/test_output_safety.py",
        "apps/web/format.test.mjs",
    }
)

_REPARTO = {"trivial": 5, "standard": 8, "guarded": 2}


def test_el_banco_tiene_quince_tareas_con_el_reparto_pedido() -> None:
    assert len(TAREAS) == 15
    for clase, cuantas in _REPARTO.items():
        assert len(tareas(clase)) == cuantas, clase
    assert sum(_REPARTO.values()) == len(TAREAS)


def test_los_ids_son_unicos_y_del_repo_edecan() -> None:
    ids = [t.id for t in TAREAS]
    assert len(set(ids)) == len(ids)
    assert all(t.repo == "edecan" for t in TAREAS)
    assert all(t.id.startswith("edecan-") for t in TAREAS)


@pytest.mark.parametrize("tarea", TAREAS, ids=lambda t: t.id)
def test_cada_tarea_tiene_un_criterio_ejecutable_que_hoy_debe_fallar(tarea: BenchTask) -> None:
    comandos = [c for c in tarea.criterios if c.kind == "command"]
    assert comandos, "sin criterio ejecutable no hay tarea"
    assert all(c.comando for c in comandos)
    assert any(c.debe_fallar_antes for c in tarea.criterios), (
        "una tarea cuyo criterio ya pasa sobre el repo intacto no mide nada"
    )


@pytest.mark.parametrize("tarea", TAREAS, ids=lambda t: t.id)
def test_cada_criterio_apunta_a_algo_que_existe(tarea: BenchTask) -> None:
    for criterio in tarea.criterios:
        if criterio.kind == "command":
            assert criterio.comando is not None
            for argumento in criterio.comando:
                if argumento.startswith("packages/forge-probe/bench/checks/"):
                    assert (_RAIZ / argumento).is_file(), argumento
            assert criterio.timeout_s > 0
        else:
            assert criterio.ruta, criterio.descripcion


@pytest.mark.parametrize("tarea", TAREAS, ids=lambda t: t.id)
def test_las_pistas_apuntan_al_repositorio_real(tarea: BenchTask) -> None:
    assert tarea.archivos_pista, "sin pistas no se puede medir el recall de la búsqueda"
    for pista in tarea.archivos_pista:
        destino = _RAIZ / pista
        if pista in _PISTAS_A_CREAR:
            assert destino.parent.is_dir(), pista
            assert not destino.exists(), f"{pista} ya existe: la tarea está hecha"
        else:
            assert destino.exists(), pista


@pytest.mark.parametrize("tarea", TAREAS, ids=lambda t: t.id)
def test_el_enunciado_no_regala_la_solucion(tarea: BenchTask) -> None:
    assert len(tarea.enunciado) > 120, "un enunciado de una línea no es un encargo real"
    assert tarea.lenguajes
    # El enunciado se le lee al agente: no debe contener la ruta exacta de un
    # archivo que tiene que ENCONTRAR (esa es la pista, que no se le entrega).
    # Las dos tareas de "falta un test" son la excepción declarada: el encargo
    # es justamente escribir un archivo concreto sobre un módulo concreto, y
    # una persona lo diría igual.
    if tarea.id in {"edecan-test-output-safety", "edecan-test-web-format"}:
        pytest.skip("el encargo nombra el archivo a crear y el módulo que cubre")
    rutas = {p for p in tarea.archivos_pista if "/" in p}
    assert not [ruta for ruta in rutas if ruta in tarea.enunciado]


def test_el_presupuesto_crece_con_la_clase() -> None:
    techo = {clase: max(t.presupuesto_usd for t in tareas(clase)) for clase in _REPARTO}
    piso = {clase: min(t.presupuesto_usd for t in tareas(clase)) for clase in _REPARTO}
    assert techo["trivial"] <= piso["standard"]
    assert techo["standard"] <= piso["guarded"]
    turnos = {clase: max(t.max_turnos for t in tareas(clase)) for clase in _REPARTO}
    assert turnos["trivial"] <= turnos["standard"] <= turnos["guarded"]


def test_por_id_encuentra_y_se_queja() -> None:
    assert por_id("edecan-api-usage-desglose").clase == "standard"
    with pytest.raises(KeyError):
        por_id("edecan-tarea-que-no-existe")
