"""Contrato del planificador de reparto (``ide_reparto``).

Lo que este módulo garantiza y que estas pruebas fijan:
- pasos con rutas disjuntas caen en la MISMA oleada (corren en paralelo);
- pasos sin rutas declaradas (o dependientes por construcción) van cada uno
  en su propia oleada, en el orden original;
- pasos con rutas que se solapan se separan en oleadas distintas
  (secuencial forzado), nunca se ejecutan a la vez;
- un paso que falla se reporta aislado y el resto del reparto sigue,
  incluidas las oleadas posteriores;
- cancelar a mitad de camino deja de arrancar oleadas siguientes y no
  corrompe nada (ningún paso cancelado llegó a invocar al runner).
"""

from __future__ import annotations

import asyncio

import pytest
from edecan_companion.ide_equipo import ControlEquipo
from edecan_companion.ide_reparto import (
    PasoReparto,
    PlanificadorReparto,
    RepartoError,
    construir_oleadas,
    especificacion_herramienta,
    pasos_desde_json,
    rutas_desde_texto,
)


def _paso(id_: str, *rutas: str, instrucciones: str = "haz el trabajo") -> PasoReparto:
    return PasoReparto(
        id=id_,
        titulo=f"Paso {id_}",
        instrucciones=instrucciones,
        rutas=rutas or None,
    )


# --------------------------------------------------------------------------
# Planificación pura: construir_oleadas
# --------------------------------------------------------------------------


def test_pasos_independientes_caen_en_una_sola_oleada_paralela() -> None:
    pasos = [
        _paso("a", "apps/api/routers/ide.py"),
        _paso("b", "apps/companion/edecan_companion/ide_reparto.py"),
        _paso("c", "packages/core/"),
    ]
    plan = construir_oleadas(pasos)
    assert len(plan.oleadas) == 1
    assert plan.oleadas[0].es_paralela
    assert {p.id for p in plan.oleadas[0].pasos} == {"a", "b", "c"}


def test_pasos_sin_rutas_van_solos_y_en_orden() -> None:
    pasos = [_paso("a"), _paso("b"), _paso("c")]
    plan = construir_oleadas(pasos)
    assert len(plan.oleadas) == 3
    assert [o.pasos[0].id for o in plan.oleadas] == ["a", "b", "c"]
    assert all(not o.es_paralela for o in plan.oleadas)


def test_archivos_solapados_fuerzan_oleadas_separadas() -> None:
    pasos = [
        _paso("a", "apps/api/main.py"),
        _paso("b", "apps/api/main.py"),
    ]
    plan = construir_oleadas(pasos)
    assert len(plan.oleadas) == 2
    assert [o.pasos[0].id for o in plan.oleadas] == ["a", "b"]


def test_zona_ajena_fuerza_oleadas_separadas() -> None:
    pasos = [
        _paso("frontend", "apps/web/"),
        _paso("archivo", "apps/web/src/App.tsx"),
        _paso("independiente", "packages/core/"),
    ]
    plan = construir_oleadas(pasos)
    # "frontend" y "archivo" chocan -> oleadas separadas; "independiente" no
    # choca con "archivo" (la oleada abierta tras el choque), así que se
    # suma a esa misma oleada.
    assert len(plan.oleadas) == 2
    assert plan.oleadas[0].pasos[0].id == "frontend"
    assert {p.id for p in plan.oleadas[1].pasos} == {"archivo", "independiente"}


def test_paso_desconocido_intercalado_aisla_antes_y_despues() -> None:
    pasos = [
        _paso("a", "a.py"),
        _paso("desconocido"),
        _paso("b", "b.py"),
    ]
    plan = construir_oleadas(pasos)
    assert len(plan.oleadas) == 3
    assert [o.pasos[0].id for o in plan.oleadas] == ["a", "desconocido", "b"]


def test_no_reordena_pasos_respecto_al_plan_original() -> None:
    pasos = [
        _paso("uno", "a.py"),
        _paso("dos", "a.py"),  # choca con "uno" -> nueva oleada
        _paso("tres", "b.py"),  # no choca con "dos" -> se suma
    ]
    plan = construir_oleadas(pasos)
    ids_en_orden = [p.id for o in plan.oleadas for p in o.pasos]
    assert ids_en_orden == ["uno", "dos", "tres"]


def test_reparto_vacio_se_rechaza() -> None:
    with pytest.raises(RepartoError):
        construir_oleadas([])


def test_ids_repetidos_se_rechazan() -> None:
    with pytest.raises(RepartoError, match="repetido"):
        construir_oleadas([_paso("x", "a.py"), _paso("x", "b.py")])


def test_paso_sin_titulo_se_rechaza() -> None:
    with pytest.raises(RepartoError):
        PasoReparto(id="a", titulo="  ", instrucciones="haz algo", rutas=("a.py",))


def test_rutas_vacia_explicita_se_trata_como_desconocida() -> None:
    paso = PasoReparto(id="a", titulo="t", instrucciones="i", rutas=())
    assert paso.rutas is None


# --------------------------------------------------------------------------
# Heurística de respaldo: rutas_desde_texto
# --------------------------------------------------------------------------


def test_rutas_desde_texto_reconoce_archivo_con_extension() -> None:
    rutas = rutas_desde_texto("Agrega el campo X en apps/api/models.py y listo")
    assert rutas == ("apps/api/models.py",)


def test_rutas_desde_texto_reconoce_zona() -> None:
    rutas = rutas_desde_texto("Refactoriza todo lo que hay bajo packages/core/")
    assert rutas == ("packages/core/",)


def test_rutas_desde_texto_sin_nada_reconocible_devuelve_none() -> None:
    assert rutas_desde_texto("Piensa en cómo mejorar el rendimiento general") is None
    assert rutas_desde_texto("") is None


def test_rutas_desde_texto_descarta_zona_redundante_con_archivo() -> None:
    rutas = rutas_desde_texto("Edita apps/api/routers/ide.py dentro de apps/api/routers/")
    assert rutas == ("apps/api/routers/ide.py",)


# --------------------------------------------------------------------------
# Ejecución: oleadas en paralelo, fallo aislado, cancelación
# --------------------------------------------------------------------------


async def test_ejecucion_paralela_es_realmente_concurrente() -> None:
    pasos = [_paso("a", "a.py"), _paso("b", "b.py"), _paso("c", "c.py")]
    en_curso = 0
    maximo_observado = 0
    lock = asyncio.Lock()

    async def runner(sub, control: ControlEquipo) -> str:
        nonlocal en_curso, maximo_observado
        async with lock:
            en_curso += 1
            maximo_observado = max(maximo_observado, en_curso)
        await asyncio.sleep(0.03)
        async with lock:
            en_curso -= 1
        return f"listo:{sub.id}"

    planificador = PlanificadorReparto(runner=runner, max_concurrencia=3)
    resultado = await planificador.ejecutar(pasos)

    assert resultado.exito_total
    assert maximo_observado == 3  # las 3 corrieron a la vez, una sola oleada
    assert set(resultado.completados) == {"a", "b", "c"}
    assert resultado.oleadas_totales == 1


async def test_pasos_dependientes_se_ejecutan_en_orden_y_no_a_la_vez() -> None:
    pasos = [_paso("a"), _paso("b"), _paso("c")]  # sin rutas -> cada uno solo
    orden_de_arranque: list[str] = []
    en_curso = 0
    maximo_observado = 0

    async def runner(sub, control: ControlEquipo) -> str:
        nonlocal en_curso, maximo_observado
        orden_de_arranque.append(sub.id)
        en_curso += 1
        maximo_observado = max(maximo_observado, en_curso)
        await asyncio.sleep(0.01)
        en_curso -= 1
        return "listo"

    planificador = PlanificadorReparto(runner=runner, max_concurrencia=3)
    resultado = await planificador.ejecutar(pasos)

    assert resultado.exito_total
    assert orden_de_arranque == ["a", "b", "c"]
    assert maximo_observado == 1  # nunca dos pasos "dependientes" a la vez
    assert resultado.oleadas_totales == 3


async def test_falla_un_paso_y_el_resto_del_reparto_sigue() -> None:
    pasos = [
        _paso("buena", "a.py"),
        _paso("mala", "b.py"),
        _paso("despues", "b.py"),  # mismo archivo que "mala" -> oleada siguiente
    ]
    plan = construir_oleadas(pasos)
    assert len(plan.oleadas) == 2  # confirma que "despues" quedó en otra oleada

    async def runner(sub, control: ControlEquipo) -> str:
        if sub.id == "mala":
            raise RuntimeError("el sub-agente reventó")
        return f"ok:{sub.id}"

    planificador = PlanificadorReparto(runner=runner, max_concurrencia=2)
    resultado = await planificador.ejecutar(pasos)

    assert not resultado.exito_total
    assert resultado.fallidos == ["mala"]
    assert resultado.estados["mala"].error == "el sub-agente reventó"
    # la oleada posterior a la que falló igual corrió.
    assert "despues" in resultado.completados
    assert "buena" in resultado.completados
    assert "1 fallido" in resultado.resumen()


async def test_cancelacion_a_mitad_no_arranca_oleadas_siguientes() -> None:
    pasos = [
        _paso("primera", "a.py"),
        _paso("segunda", "b.py"),
        _paso("tercera", "c.py"),
    ]
    arrancados: list[str] = []
    referencia: dict[str, PlanificadorReparto] = {}

    async def runner(sub, control: ControlEquipo) -> str:
        arrancados.append(sub.id)
        if sub.id == "primera":
            referencia["planificador"].cancelar_todo()
        await asyncio.sleep(0.01)
        return "listo"

    planificador = PlanificadorReparto(runner=runner, max_concurrencia=1)
    referencia["planificador"] = planificador

    resultado = await planificador.ejecutar(pasos)

    assert resultado.cancelado
    assert arrancados == ["primera"]
    assert "segunda" in resultado.cancelados
    assert "tercera" in resultado.cancelados
    # el workspace queda consistente: los pasos cancelados nunca llamaron al runner.
    assert set(resultado.cancelados).isdisjoint(arrancados)


async def test_cancelar_todo_forzado_corta_una_oleada_en_curso() -> None:
    pasos = [_paso("larga", "a.py"), _paso("otra", "b.py")]
    empezo_larga = asyncio.Event()

    async def runner(sub, control: ControlEquipo) -> str:
        if sub.id == "larga":
            empezo_larga.set()
            await asyncio.sleep(5)
            return "no debería llegar aquí"
        return "listo"

    planificador = PlanificadorReparto(runner=runner, max_concurrencia=2)
    tarea = asyncio.create_task(planificador.ejecutar(pasos))
    await asyncio.wait_for(empezo_larga.wait(), timeout=1)
    planificador.cancelar_todo(forzado=True)

    resultado = await asyncio.wait_for(tarea, timeout=1)

    assert resultado.cancelado
    assert resultado.estados["larga"].estado == "cancelada"


async def test_instancia_no_permite_dos_repartos_simultaneos() -> None:
    pasos = [_paso("a", "a.py")]
    empezo = asyncio.Event()
    seguir = asyncio.Event()

    async def runner(sub, control: ControlEquipo) -> str:
        empezo.set()
        await seguir.wait()
        return "listo"

    planificador = PlanificadorReparto(runner=runner, max_concurrencia=1)
    tarea = asyncio.create_task(planificador.ejecutar(pasos))
    await asyncio.wait_for(empezo.wait(), timeout=1)

    with pytest.raises(RepartoError, match="ya tiene un reparto"):
        await planificador.ejecutar(pasos)

    seguir.set()
    await tarea


def test_max_concurrencia_fuera_de_rango_se_rechaza() -> None:
    async def runner(sub, control: ControlEquipo) -> str:
        return "x"

    with pytest.raises(RepartoError):
        PlanificadorReparto(runner=runner, max_concurrencia=0)
    with pytest.raises(RepartoError):
        PlanificadorReparto(runner=runner, max_concurrencia=999)


# --------------------------------------------------------------------------
# Conversión desde JSON / contrato de herramienta
# --------------------------------------------------------------------------


def test_pasos_desde_json_produce_pasos_validos_con_y_sin_rutas() -> None:
    bruto = [
        {"id": "a", "titulo": "A", "instrucciones": "haz A", "rutas": ["a.py"]},
        {"id": "b", "titulo": "B", "instrucciones": "haz B"},
    ]
    pasos = pasos_desde_json(bruto)
    assert pasos[0].rutas == ("a.py",)
    assert pasos[1].rutas is None
    plan = construir_oleadas(pasos)
    assert len(plan.oleadas) == 2  # "b" sin rutas siempre va solo


def test_pasos_desde_json_rechaza_payload_no_lista() -> None:
    with pytest.raises(RepartoError):
        pasos_desde_json({"id": "a"})


def test_especificacion_herramienta_tiene_forma_de_tool_spec() -> None:
    spec = especificacion_herramienta()
    assert spec["name"] == "planificar_reparto"
    assert spec["input_schema"]["required"] == ["pasos"]
    assert "pasos" in spec["input_schema"]["properties"]


def test_paso_con_rutas_invalidas_se_reporta_como_reparto_error() -> None:
    with pytest.raises(RepartoError):
        construir_oleadas([_paso("a", "../fuera.py")])
