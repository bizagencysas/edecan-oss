"""Contrato del orquestador de agentes en paralelo (``ide_equipo``).

Lo que de verdad protege este módulo, según lo pidió el dueño, y que estas
pruebas fijan como comportamiento:
- reparto correcto: sub-tareas con rutas/zonas disjuntas se aceptan y
  corren todas;
- reparto con solapamiento (archivo repetido o zona que se come a otra) se
  RECHAZA entero antes de ejecutar nada;
- un fallo de una sub-tarea no tumba a las demás -- se reporta aislado;
- un tope de concurrencia real: nunca hay más de N sub-tareas corriendo
  a la vez;
- cancelación cooperativa (sub-tareas que no arrancaron se marcan
  "cancelada" y no llaman al runner) y forzada (corta una sub-tarea que ya
  estaba en curso).
"""

from __future__ import annotations

import asyncio

import pytest
from edecan_companion.ide_equipo import (
    ControlEquipo,
    EquipoDeAgentes,
    EquipoError,
    Subtarea,
    construir_plan,
    detectar_solapamientos,
    especificacion_herramienta,
    subtareas_desde_json,
)


def _sub(id_: str, *rutas: str, instrucciones: str = "haz el trabajo") -> Subtarea:
    return Subtarea(id=id_, titulo=f"Tarea {id_}", instrucciones=instrucciones, rutas=rutas)


# --------------------------------------------------------------------------
# Reparto: correcto y solapado
# --------------------------------------------------------------------------


def test_reparto_disjunto_no_tiene_conflictos() -> None:
    subtareas = [
        _sub("a", "apps/api/routers/ide.py"),
        _sub("b", "apps/companion/edecan_companion/ide_equipo.py"),
        _sub("c", "packages/core/"),
    ]
    assert detectar_solapamientos(subtareas) == []
    plan = construir_plan(subtareas)
    assert len(plan.subtareas) == 3


def test_reparto_con_archivo_repetido_se_rechaza() -> None:
    subtareas = [
        _sub("a", "apps/api/main.py"),
        _sub("b", "apps/api/main.py"),
    ]
    conflictos = detectar_solapamientos(subtareas)
    assert len(conflictos) == 1
    assert conflictos[0].subtarea_a == "a"
    assert conflictos[0].subtarea_b == "b"
    with pytest.raises(EquipoError, match="solapamiento"):
        construir_plan(subtareas)


def test_reparto_con_archivo_dentro_de_zona_ajena_se_rechaza() -> None:
    subtareas = [
        _sub("frontend", "apps/web/"),
        _sub("un_archivo", "apps/web/src/App.tsx"),
    ]
    with pytest.raises(EquipoError, match="solapamiento"):
        construir_plan(subtareas)


def test_reparto_con_zonas_anidadas_se_rechaza() -> None:
    subtareas = [
        _sub("amplio", "packages/core/"),
        _sub("angosto", "packages/core/edecan_core/"),
    ]
    with pytest.raises(EquipoError, match="solapamiento"):
        construir_plan(subtareas)


def test_zonas_hermanas_no_chocan() -> None:
    subtareas = [
        _sub("uno", "packages/core/a/"),
        _sub("dos", "packages/core/b/"),
    ]
    assert detectar_solapamientos(subtareas) == []
    construir_plan(subtareas)  # no lanza


def test_ids_repetidos_se_rechazan() -> None:
    subtareas = [_sub("x", "a.py"), _sub("x", "b.py")]
    with pytest.raises(EquipoError, match="repetido"):
        construir_plan(subtareas)


def test_plan_vacio_se_rechaza() -> None:
    with pytest.raises(EquipoError):
        construir_plan([])


def test_subtarea_sin_rutas_se_rechaza() -> None:
    with pytest.raises(EquipoError, match="ninguna ruta"):
        Subtarea(id="a", titulo="t", instrucciones="i", rutas=())


def test_ruta_con_escape_de_directorio_se_rechaza() -> None:
    with pytest.raises(EquipoError, match="fuera del workspace"):
        Subtarea(id="a", titulo="t", instrucciones="i", rutas=("../fuera.py",))


def test_ruta_vacia_se_rechaza() -> None:
    with pytest.raises(EquipoError):
        Subtarea(id="a", titulo="t", instrucciones="i", rutas=("",))


# --------------------------------------------------------------------------
# Ejecución: reparto correcto, fallo aislado, concurrencia, cancelación
# --------------------------------------------------------------------------


async def test_ejecucion_correcta_junta_resultados_de_todas() -> None:
    plan = construir_plan([_sub("a", "a.py"), _sub("b", "b.py"), _sub("c", "c.py")])

    async def runner(sub: Subtarea, control: ControlEquipo) -> str:
        return f"listo:{sub.id}"

    equipo = EquipoDeAgentes(runner=runner, max_concurrencia=3)
    resultado = await equipo.ejecutar(plan)

    assert resultado.exito_total
    assert set(resultado.completadas) == {"a", "b", "c"}
    assert resultado.estados["a"].salida == "listo:a"
    assert resultado.fallidas == []
    assert resultado.canceladas == []


async def test_fallo_de_una_sub_tarea_no_tumba_a_las_demas() -> None:
    plan = construir_plan([_sub("buena", "a.py"), _sub("mala", "b.py"), _sub("otra_buena", "c.py")])

    async def runner(sub: Subtarea, control: ControlEquipo) -> str:
        if sub.id == "mala":
            raise RuntimeError("el sub-agente reventó")
        return f"ok:{sub.id}"

    equipo = EquipoDeAgentes(runner=runner, max_concurrencia=2)
    resultado = await equipo.ejecutar(plan)

    assert not resultado.exito_total
    assert resultado.fallidas == ["mala"]
    assert resultado.estados["mala"].error == "el sub-agente reventó"
    assert set(resultado.completadas) == {"buena", "otra_buena"}
    assert "1 fallida" in resultado.resumen()


async def test_tope_de_concurrencia_nunca_se_excede() -> None:
    plan = construir_plan([_sub(str(i), f"archivo_{i}.py") for i in range(10)])
    en_curso = 0
    maximo_observado = 0
    lock = asyncio.Lock()

    async def runner(sub: Subtarea, control: ControlEquipo) -> str:
        nonlocal en_curso, maximo_observado
        async with lock:
            en_curso += 1
            maximo_observado = max(maximo_observado, en_curso)
        await asyncio.sleep(0.02)
        async with lock:
            en_curso -= 1
        return "listo"

    equipo = EquipoDeAgentes(runner=runner, max_concurrencia=3)
    resultado = await equipo.ejecutar(plan)

    assert maximo_observado <= 3
    assert resultado.exito_total
    assert len(resultado.completadas) == 10


def test_max_concurrencia_fuera_de_rango_se_rechaza() -> None:
    async def runner(sub: Subtarea, control: ControlEquipo) -> str:
        return "x"

    with pytest.raises(EquipoError):
        EquipoDeAgentes(runner=runner, max_concurrencia=0)
    with pytest.raises(EquipoError):
        EquipoDeAgentes(runner=runner, max_concurrencia=999)


async def test_cancelacion_cooperativa_evita_arrancar_pendientes() -> None:
    plan = construir_plan([_sub(str(i), f"archivo_{i}.py") for i in range(6)])
    arrancadas: list[str] = []
    cancelar = asyncio.Event()

    async def runner(sub: Subtarea, control: ControlEquipo) -> str:
        arrancadas.append(sub.id)
        if len(arrancadas) == 2:
            cancelar.set()
        await asyncio.sleep(0.01)
        return "listo"

    # tope de 2: las primeras 2 arrancan, la segunda dispara la cancelación
    # antes de que el resto (esperando cupo) alcance a arrancar.
    equipo = EquipoDeAgentes(runner=runner, max_concurrencia=2)
    resultado = await equipo.ejecutar(plan, cancelar=cancelar)

    assert resultado.cancelado
    assert len(arrancadas) <= 3  # las 2 en curso + a lo sumo 1 que ya tenía cupo
    assert len(resultado.canceladas) >= 3
    assert set(resultado.canceladas).isdisjoint(arrancadas)


async def test_cancelar_todo_forzado_corta_una_sub_tarea_en_curso() -> None:
    plan = construir_plan([_sub("larga", "a.py"), _sub("corta", "b.py")])
    empezo_larga = asyncio.Event()

    async def runner(sub: Subtarea, control: ControlEquipo) -> str:
        if sub.id == "larga":
            empezo_larga.set()
            await asyncio.sleep(5)  # nunca debería llegar a terminar
            return "no debería llegar aquí"
        return "listo:corta"

    equipo = EquipoDeAgentes(runner=runner, max_concurrencia=2)
    tarea_equipo = asyncio.create_task(equipo.ejecutar(plan))
    await asyncio.wait_for(empezo_larga.wait(), timeout=1)
    equipo.cancelar_todo(forzado=True)

    resultado = await asyncio.wait_for(tarea_equipo, timeout=1)

    assert resultado.estados["larga"].estado == "cancelada"
    assert resultado.cancelado


async def test_instancia_no_permite_dos_ejecuciones_simultaneas() -> None:
    plan = construir_plan([_sub("a", "a.py")])
    empezo = asyncio.Event()
    seguir = asyncio.Event()

    async def runner(sub: Subtarea, control: ControlEquipo) -> str:
        empezo.set()
        await seguir.wait()
        return "listo"

    equipo = EquipoDeAgentes(runner=runner, max_concurrencia=1)
    tarea = asyncio.create_task(equipo.ejecutar(plan))
    await asyncio.wait_for(empezo.wait(), timeout=1)

    with pytest.raises(EquipoError, match="ya tiene un plan"):
        await equipo.ejecutar(plan)

    seguir.set()
    await tarea


# --------------------------------------------------------------------------
# Contrato de herramienta / conversión desde JSON
# --------------------------------------------------------------------------


def test_especificacion_herramienta_tiene_forma_de_tool_spec() -> None:
    spec = especificacion_herramienta()
    assert spec["name"] == "repartir_equipo"
    assert spec["input_schema"]["required"] == ["subtareas"]
    assert "subtareas" in spec["input_schema"]["properties"]


def test_subtareas_desde_json_produce_subtareas_validas() -> None:
    bruto = [
        {"id": "a", "titulo": "A", "instrucciones": "haz A", "rutas": ["a.py"]},
        {"id": "b", "titulo": "B", "instrucciones": "haz B", "rutas": ["b/"]},
    ]
    subtareas = subtareas_desde_json(bruto)
    assert [s.id for s in subtareas] == ["a", "b"]
    plan = construir_plan(subtareas)
    assert len(plan.subtareas) == 2


def test_subtareas_desde_json_rechaza_payload_no_lista() -> None:
    with pytest.raises(EquipoError):
        subtareas_desde_json({"id": "a"})


def test_subtareas_desde_json_rechaza_item_sin_rutas() -> None:
    with pytest.raises(EquipoError):
        subtareas_desde_json([{"id": "a", "titulo": "A", "instrucciones": "x", "rutas": []}])
