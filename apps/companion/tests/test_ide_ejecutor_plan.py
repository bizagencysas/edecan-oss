"""Pruebas de ``ide_ejecutor_plan``: interpretar Markdown en pasos ejecutables
y el ciclo de vida persistente (crear, avanzar respetando dependencias,
fallar/reintentar sin repetir pasos ya hechos, sobrevivir a "cerrar la app").
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from edecan_companion.ide_ejecutor_plan import (
    EjecutorPlanStore,
    IDEEjecutorPlanError,
    PlanEjecutable,
    interpretar_markdown,
)

# --------------------------------------------------------------------- #
# interpretar_markdown -- un plan con casillas.
# --------------------------------------------------------------------- #


def test_interpreta_lista_de_casillas_y_respeta_las_ya_marcadas():
    md = """# Plan de despliegue

- [x] Configurar el repositorio.
- [ ] Instalar dependencias.
- [ ] Escribir pruebas unitarias.
"""
    interpretacion = interpretar_markdown(md)
    assert interpretacion.es_ejecutable
    assert interpretacion.formato_detectado == "checkbox"
    assert interpretacion.titulo == "Plan de despliegue"
    assert [p.descripcion for p in interpretacion.pasos] == [
        "Configurar el repositorio.",
        "Instalar dependencias.",
        "Escribir pruebas unitarias.",
    ]
    # La casilla ya marcada nace "hecho" -- retomar un plan que el dueño ya
    # avanzó a mano no debe repetir ese paso.
    assert [p.estado for p in interpretacion.pasos] == ["hecho", "pendiente", "pendiente"]


# --------------------------------------------------------------------- #
# interpretar_markdown -- lista numerada, con dependencias explícitas.
# --------------------------------------------------------------------- #


def test_interpreta_lista_numerada_con_dependencias_fuera_de_orden():
    # El paso 1 depende del 2 -- un plan "mal ordenado" es lo normal.
    md = """1. Escribir pruebas de integración. Depende del paso 2.
2. Implementar la función de cálculo de riesgo.
3. Documentar el endpoint. Depende de los pasos 1 y 2.
"""
    interpretacion = interpretar_markdown(md)
    assert interpretacion.es_ejecutable
    assert interpretacion.formato_detectado == "numerada"
    pasos = interpretacion.pasos
    assert pasos[0].depende_de == (2,)
    assert pasos[1].depende_de == ()
    assert pasos[2].depende_de == (1, 2)
    # La cláusula de dependencia no debe quedar incrustada en la descripción.
    assert "depende" not in pasos[0].descripcion.lower()


# --------------------------------------------------------------------- #
# interpretar_markdown -- prosa organizada con encabezados.
# --------------------------------------------------------------------- #


def test_interpreta_prosa_con_encabezados_y_dependencia_lineal():
    md = """# Plan de despliegue

## Paso 1: Preparar el entorno
Instala las dependencias necesarias y corre las pruebas en local.

## Paso 2: Desplegar a staging
Sube la rama y espera el pipeline. Depende del paso 1.

## Paso 3: Desplegar a producción
Promueve el build de staging solo si el paso 2 salió bien. Depende del paso 2.
"""
    interpretacion = interpretar_markdown(md)
    assert interpretacion.es_ejecutable
    assert interpretacion.formato_detectado == "encabezados"
    assert interpretacion.titulo == "Plan de despliegue"
    assert [p.descripcion for p in interpretacion.pasos] == [
        "Preparar el entorno",
        "Desplegar a staging",
        "Desplegar a producción",
    ]
    assert interpretacion.pasos[0].depende_de == ()
    assert interpretacion.pasos[1].depende_de == (1,)
    assert interpretacion.pasos[2].depende_de == (2,)
    # El cuerpo del párrafo se conserva como detalle, no se pierde.
    assert "pipeline" in interpretacion.pasos[1].detalle


# --------------------------------------------------------------------- #
# interpretar_markdown -- viñetas simples (sin casillas ni números).
# --------------------------------------------------------------------- #


def test_interpreta_vinetas_simples():
    md = """- Crear la tabla de usuarios.
- Exponer el endpoint de alta. Requiere el paso 1.
"""
    interpretacion = interpretar_markdown(md)
    assert interpretacion.es_ejecutable
    assert interpretacion.formato_detectado == "vinetas"
    assert interpretacion.pasos[1].depende_de == (1,)


# --------------------------------------------------------------------- #
# interpretar_markdown -- prosa suelta, sin listas ni encabezados.
# --------------------------------------------------------------------- #


def test_interpreta_parrafos_sueltos_como_ultimo_recurso():
    md = (
        "Primero clona el repositorio y crea una rama nueva.\n\n"
        "Después corre las migraciones de base de datos. Depende del paso 1.\n\n"
        "Finalmente despliega a producción. Depende del paso 2."
    )
    interpretacion = interpretar_markdown(md)
    assert interpretacion.es_ejecutable
    assert interpretacion.formato_detectado == "parrafos"
    assert len(interpretacion.pasos) == 3
    assert interpretacion.pasos[1].depende_de == (1,)
    assert interpretacion.pasos[2].depende_de == (2,)


# --------------------------------------------------------------------- #
# interpretar_markdown -- casos rotos: vacío, ciclo, referencia inexistente.
# --------------------------------------------------------------------- #


def test_interpretar_markdown_vacio_no_lanza_y_reporta_error():
    interpretacion = interpretar_markdown("   ")
    assert not interpretacion.es_ejecutable
    assert interpretacion.pasos == []
    assert interpretacion.errores


def test_interpretar_markdown_rechaza_exceso_de_pasos():
    from edecan_companion.ide_ejecutor_plan import MAX_PASOS

    md = "\n".join(f"{i}. Paso {i}." for i in range(1, MAX_PASOS + 2))
    interpretacion = interpretar_markdown(md)
    assert not interpretacion.es_ejecutable
    assert any("máximo soportado" in error for error in interpretacion.errores)


def test_interpretar_markdown_detecta_ciclo_de_dependencias():
    md = """1. Hacer A. Depende del paso 2.
2. Hacer B. Depende del paso 1.
"""
    interpretacion = interpretar_markdown(md)
    assert not interpretacion.es_ejecutable
    assert any("ciclo" in error.lower() for error in interpretacion.errores)


def test_interpretar_markdown_referencia_a_paso_inexistente():
    md = """1. Hacer A. Depende del paso 5.
2. Hacer B.
"""
    interpretacion = interpretar_markdown(md)
    assert not interpretacion.es_ejecutable
    assert any("no existe en el plan" in error for error in interpretacion.errores)


def test_interpretar_markdown_no_permite_autodependencia():
    md = "1. Hacer A. Depende del paso 1.\n2. Hacer B.\n"
    interpretacion = interpretar_markdown(md)
    assert not interpretacion.es_ejecutable
    assert any("depender de sí mismo" in error for error in interpretacion.errores)


def test_interpretar_markdown_no_lanza_con_tipos_invalidos():
    assert interpretar_markdown(None).errores  # type: ignore[arg-type]
    assert interpretar_markdown(123).errores  # type: ignore[arg-type]


# --------------------------------------------------------------------- #
# EjecutorPlanStore -- crear / obtener / listar.
# --------------------------------------------------------------------- #


def test_crear_persiste_un_plan_ejecutable(tmp_path: Path):
    store = EjecutorPlanStore(tmp_path)
    md = "1. Paso uno.\n2. Paso dos.\n"
    plan = store.crear(md, session_id="sesion-1")
    assert plan["session_id"] == "sesion-1"
    assert plan["estado"] == "pendiente"
    assert len(plan["pasos"]) == 2

    recargado = store.obtener(plan["id"])
    assert recargado == plan


def test_crear_rechaza_un_plan_con_ciclo(tmp_path: Path):
    store = EjecutorPlanStore(tmp_path)
    md = "1. A. Depende del paso 2.\n2. B. Depende del paso 1.\n"
    with pytest.raises(IDEEjecutorPlanError):
        store.crear(md)
    # Nada debe quedar persistido de un plan rechazado.
    assert store.listar() == []


def test_obtener_plan_inexistente_lanza(tmp_path: Path):
    store = EjecutorPlanStore(tmp_path)
    with pytest.raises(IDEEjecutorPlanError):
        store.obtener("no-existe")


def test_listar_filtra_por_session_id(tmp_path: Path):
    store = EjecutorPlanStore(tmp_path)
    store.crear("1. Paso uno.\n2. Paso dos.\n", session_id="a")
    store.crear("1. Paso uno.\n2. Paso dos.\n", session_id="b")
    assert len(store.listar()) == 2
    assert len(store.listar(session_id="a")) == 1


# --------------------------------------------------------------------- #
# EjecutorPlanStore -- iniciar_siguiente respeta dependencias, no el orden
# textual del documento.
# --------------------------------------------------------------------- #


def test_iniciar_siguiente_respeta_dependencias_no_el_orden_del_texto(tmp_path: Path):
    store = EjecutorPlanStore(tmp_path)
    # El paso 1 (tal como aparece escrito) depende del 2 -- debe ejecutarse
    # el 2 primero aunque esté después en el orden de avance esperado.
    md = "1. Escribir el reporte. Depende del paso 2.\n2. Generar los datos.\n"
    plan = store.crear(md)
    plan_id = plan["id"]

    primero = store.iniciar_siguiente(plan_id)
    assert primero["descripcion"] == "Generar los datos."
    assert primero["estado"] == "en_curso"

    store.marcar_hecho(plan_id, primero["id"])

    segundo = store.iniciar_siguiente(plan_id)
    assert segundo["descripcion"] == "Escribir el reporte"

    plan_final = store.marcar_hecho(plan_id, segundo["id"])
    assert plan_final["estado"] == "completado"


def test_iniciar_siguiente_devuelve_none_cuando_no_hay_nada_listo(tmp_path: Path):
    store = EjecutorPlanStore(tmp_path)
    plan = store.crear("1. Único paso.\n")
    plan_id = plan["id"]
    paso = store.iniciar_siguiente(plan_id)
    store.marcar_hecho(plan_id, paso["id"])
    assert store.iniciar_siguiente(plan_id) is None


def test_iniciar_siguiente_lanza_si_ya_hay_un_paso_en_curso(tmp_path: Path):
    store = EjecutorPlanStore(tmp_path)
    plan = store.crear("1. Paso uno.\n2. Paso dos.\n")
    store.iniciar_siguiente(plan["id"])
    with pytest.raises(IDEEjecutorPlanError):
        store.iniciar_siguiente(plan["id"])


# --------------------------------------------------------------------- #
# EjecutorPlanStore -- fallar y RETOMAR sin repetir los pasos ya hechos.
# --------------------------------------------------------------------- #


def test_fallar_y_reintentar_no_repite_los_pasos_ya_hechos(tmp_path: Path):
    store = EjecutorPlanStore(tmp_path)
    md = "1. Paso uno.\n2. Paso dos.\n3. Paso tres.\n4. Paso cuatro.\n"
    plan = store.crear(md)
    plan_id = plan["id"]

    # Pasos 1-3 se completan con normalidad.
    for _ in range(3):
        paso = store.iniciar_siguiente(plan_id)
        store.marcar_hecho(plan_id, paso["id"], nota="ok")

    # El paso 4 falla.
    paso_4 = store.iniciar_siguiente(plan_id)
    assert paso_4["descripcion"] == "Paso cuatro."
    plan_tras_fallo = store.marcar_fallado(plan_id, paso_4["id"], motivo="el comando reventó")
    assert plan_tras_fallo["estado"] == "bloqueado"

    # Mientras está fallado, no se puede iniciar nada mientras siga
    # "en_curso"... en este caso ya no está en_curso (quedó "fallado"), así
    # que no hay nada más listo (el único pendiente es el que falló).
    assert store.iniciar_siguiente(plan_id) is None

    # Se "arregla" el paso 4 y se retoma DESDE AHÍ, sin tocar 1-3.
    plan_reintentado = store.reintentar_paso(plan_id, paso_4["id"])
    estados_por_orden = {p["orden"]: p["estado"] for p in plan_reintentado["pasos"]}
    assert estados_por_orden == {1: "hecho", 2: "hecho", 3: "hecho", 4: "pendiente"}

    paso_4_de_nuevo = store.iniciar_siguiente(plan_id)
    assert paso_4_de_nuevo["descripcion"] == "Paso cuatro."
    plan_final = store.marcar_hecho(plan_id, paso_4_de_nuevo["id"])
    assert plan_final["estado"] == "completado"
    # Las notas de los pasos 1-3 (marcados mucho antes) siguen intactas.
    notas = {p["orden"]: p["nota"] for p in plan_final["pasos"]}
    assert notas[1] == "ok"


def test_marcar_fallado_sin_motivo_lanza(tmp_path: Path):
    store = EjecutorPlanStore(tmp_path)
    plan = store.crear("1. Paso uno.\n")
    paso = store.iniciar_siguiente(plan["id"])
    with pytest.raises(IDEEjecutorPlanError):
        store.marcar_fallado(plan["id"], paso["id"], motivo="   ")


def test_marcar_hecho_falla_si_el_paso_no_esta_en_curso(tmp_path: Path):
    store = EjecutorPlanStore(tmp_path)
    plan = store.crear("1. Paso uno.\n2. Paso dos.\n")
    # paso 2 sigue "pendiente" -- todavía no lo reclamó iniciar_siguiente.
    paso_2_id = plan["pasos"][1]["id"]
    with pytest.raises(IDEEjecutorPlanError):
        store.marcar_hecho(plan["id"], paso_2_id)


def test_reintentar_paso_que_no_esta_fallado_lanza(tmp_path: Path):
    store = EjecutorPlanStore(tmp_path)
    plan = store.crear("1. Paso uno.\n")
    paso_id = plan["pasos"][0]["id"]
    with pytest.raises(IDEEjecutorPlanError):
        store.reintentar_paso(plan["id"], paso_id)


# --------------------------------------------------------------------- #
# EjecutorPlanStore -- omitir_paso.
# --------------------------------------------------------------------- #


def test_omitir_paso_libera_a_sus_dependientes(tmp_path: Path):
    store = EjecutorPlanStore(tmp_path)
    md = "1. Paso opcional.\n2. Paso final. Depende del paso 1.\n"
    plan = store.crear(md)
    plan_id = plan["id"]
    paso_1_id = plan["pasos"][0]["id"]

    # Se omite sin siquiera haberlo iniciado.
    plan_tras_omitir = store.omitir_paso(plan_id, paso_1_id, nota="ya no aplica")
    estados = {p["orden"]: p["estado"] for p in plan_tras_omitir["pasos"]}
    assert estados[1] == "omitido"

    listos = store.pasos_listos(plan_id)
    assert [p["orden"] for p in listos] == [2]


# --------------------------------------------------------------------- #
# EjecutorPlanStore -- cancelar.
# --------------------------------------------------------------------- #


def test_cancelar_bloquea_mas_avance(tmp_path: Path):
    store = EjecutorPlanStore(tmp_path)
    plan = store.crear("1. Paso uno.\n2. Paso dos.\n")
    store.cancelar(plan["id"], motivo="el dueño cambió de rumbo")
    with pytest.raises(IDEEjecutorPlanError):
        store.iniciar_siguiente(plan["id"])
    estado = store.obtener(plan["id"])
    assert estado["estado"] == "cancelado"
    assert estado["motivo_cancelacion"] == "el dueño cambió de rumbo"


def test_cancelar_plan_completado_lanza(tmp_path: Path):
    store = EjecutorPlanStore(tmp_path)
    plan = store.crear("1. Único paso.\n")
    paso = store.iniciar_siguiente(plan["id"])
    store.marcar_hecho(plan["id"], paso["id"])
    with pytest.raises(IDEEjecutorPlanError):
        store.cancelar(plan["id"])


def test_cancelar_dos_veces_lanza(tmp_path: Path):
    store = EjecutorPlanStore(tmp_path)
    plan = store.crear("1. Paso uno.\n")
    store.cancelar(plan["id"])
    with pytest.raises(IDEEjecutorPlanError):
        store.cancelar(plan["id"])


# --------------------------------------------------------------------- #
# Persistencia real: "cerrar la app a mitad" no debe perder el avance.
# --------------------------------------------------------------------- #


def test_el_avance_sobrevive_a_recrear_el_store_desde_el_mismo_disco(tmp_path: Path):
    store_a = EjecutorPlanStore(tmp_path)
    md = "1. Paso uno.\n2. Paso dos.\n3. Paso tres.\n"
    plan = store_a.crear(md, session_id="sesion-larga")
    plan_id = plan["id"]

    paso_1 = store_a.iniciar_siguiente(plan_id)
    store_a.marcar_hecho(plan_id, paso_1["id"], nota="listo antes de cerrar la app")

    # Simula reabrir la app: una instancia NUEVA del store, mismo directorio.
    store_b = EjecutorPlanStore(tmp_path)
    recargado = store_b.obtener(plan_id)
    estados = {p["orden"]: p["estado"] for p in recargado["pasos"]}
    assert estados == {1: "hecho", 2: "pendiente", 3: "pendiente"}
    assert recargado["pasos"][0]["nota"] == "listo antes de cerrar la app"

    # Y se puede seguir avanzando normalmente desde la instancia nueva.
    paso_2 = store_b.iniciar_siguiente(plan_id)
    assert paso_2["descripcion"] == "Paso dos."
    store_b.marcar_hecho(plan_id, paso_2["id"])
    assert store_b.obtener(plan_id)["estado"] == "en_progreso"


# --------------------------------------------------------------------- #
# ``workspace_id`` -- necesario para que ``SessionManager.avanzar_plan_
# externo`` sepa DÓNDE correr cada paso, incluso tras reabrir la app.
# --------------------------------------------------------------------- #


def test_workspace_id_sobrevive_a_recrear_el_store_desde_el_mismo_disco(tmp_path: Path):
    store_a = EjecutorPlanStore(tmp_path)
    plan = store_a.crear("1. Paso uno.\n", workspace_id="ws-123")
    assert plan["workspace_id"] == "ws-123"

    store_b = EjecutorPlanStore(tmp_path)
    recargado = store_b.obtener(plan["id"])
    assert recargado["workspace_id"] == "ws-123"


def test_plan_ejecutable_from_json_sin_workspace_id_no_revienta():
    """Un manifiesto persistido ANTES de que este campo existiera no debe
    reventar al recargarse -- vuelve ``None``, no ``KeyError``."""
    raw = {
        "id": "plan-viejo",
        "session_id": None,
        "titulo": "Un plan de antes de este campo",
        "formato_detectado": "numerada",
        "pasos": [],
        "advertencias": [],
        "cancelado": False,
        "motivo_cancelacion": None,
        "created_at": "2020-01-01T00:00:00+00:00",
        "updated_at": "2020-01-01T00:00:00+00:00",
    }
    plan = PlanEjecutable.from_json(raw)
    assert plan.workspace_id is None


# --------------------------------------------------------------------- #
# Cableado con SessionManager (ide_sessions.py): ``crear_plan_externo`` /
# ``avanzar_plan_externo``, el ciclo que describe la sección "Integración"
# del docstring del módulo. ``WorkersIDEAgent.run`` va parcheado -- mismo
# patrón que ``test_ide_sessions_plan.py`` -- porque lo que se prueba acá es
# CÓMO ``SessionManager`` orquesta el ciclo (reclama el paso listo, lo corre,
# marca hecho/fallado, respeta el orden de dependencias), no si ``run()``
# honra el portón de verificación -- eso ya lo fija
# ``test_ide_workers_agent.py`` con el proveedor real, y ``avanzar_plan_
# externo`` corre por el MISMO ``WorkersIDEAgent.run()`` sin ningún código
# de verificación propio, así que lo hereda gratis.
# --------------------------------------------------------------------- #


def _make_manager_real(tmp_path: Path):
    from edecan_companion.ide_sessions import SessionManager
    from edecan_companion.ide_workspaces import WorkspaceStore

    project = tmp_path / "proyecto"
    project.mkdir()
    workspaces = WorkspaceStore(tmp_path / "ide")
    manager = SessionManager(tmp_path / "ide", workspaces)
    workspace = workspaces.authorize(str(project))
    return manager, workspace


async def _sesion_lista(manager, workspace_id: str, prompt: str) -> str:
    """Arranca y espera un turno normal (ya con ``WorkersIDEAgent.run``
    parcheado por el llamador) -- deja la sesión en ``completed``, lista
    para que ``avanzar_plan_externo`` la use."""
    started = manager.start_agent(workspace_id, prompt)
    session_id = started["session"]["id"]
    for _ in range(200):
        state = manager.read(session_id, "agent", 0)
        if state["session"]["status"] not in {"starting", "running"}:
            return session_id
        await asyncio.sleep(0.01)
    pytest.fail(f"La sesión {session_id} nunca dejó de estar 'running'.")


async def test_avanzar_plan_externo_ejecuta_los_pasos_en_orden_y_los_marca_hecho(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    manager, workspace = _make_manager_real(tmp_path)
    prompts_vistos: list[str] = []

    async def fake_run(_self, **kwargs):
        prompts_vistos.append(kwargs["prompt"])
        kwargs["write_event"]("assistant_final", f"Listo #{len(prompts_vistos)}.")

    monkeypatch.setattr("edecan_companion.ide_workers_agent.WorkersIDEAgent.run", fake_run)

    session_id = await _sesion_lista(manager, workspace["id"], "Prepara el terreno")

    plan = manager.crear_plan_externo(
        workspace["id"],
        "1. Preparar el entorno.\n2. Correr las pruebas. Depende del paso 1.\n",
        session_id=session_id,
    )
    plan_id = plan["id"]
    assert plan["workspace_id"] == workspace["id"]
    assert plan["estado"] == "pendiente"

    resultado_1 = await manager.avanzar_plan_externo(session_id, plan_id)
    assert resultado_1["terminado"] is False
    assert resultado_1["ok"] is True
    assert resultado_1["paso"]["descripcion"] == "Preparar el entorno."
    assert resultado_1["plan"]["pasos"][0]["estado"] == "hecho"
    # El paso 2 depende del 1: todavía no puede estar "en_curso" ni "hecho".
    assert resultado_1["plan"]["pasos"][1]["estado"] == "pendiente"

    resultado_2 = await manager.avanzar_plan_externo(session_id, plan_id)
    assert resultado_2["terminado"] is False
    assert resultado_2["ok"] is True
    # Sin el punto final: la cláusula de dependencia ("Depende del paso 1.")
    # se quita junto con la puntuación que la seguía (ver
    # ``_quitar_clausula_dependencia``, comportamiento ya existente de
    # ``interpretar_markdown`` -- no es parte de este cableado).
    assert resultado_2["paso"]["descripcion"] == "Correr las pruebas"

    resultado_3 = await manager.avanzar_plan_externo(session_id, plan_id)
    assert resultado_3["terminado"] is True
    assert resultado_3["plan"]["estado"] == "completado"

    # El texto exacto del paso llegó al sub-agente como prompt real.
    assert any("Paso a ejecutar: Preparar el entorno." in p for p in prompts_vistos)
    assert any("Paso a ejecutar: Correr las pruebas" in p for p in prompts_vistos)

    # avanzar_plan_externo no es dueño del ciclo de vida de la sesión: queda
    # en el mismo estado en que estaba antes de avanzar el plan.
    final_state = manager.read(session_id, "agent", 0)
    assert final_state["session"]["status"] == "completed"


async def test_avanzar_plan_externo_marca_fallado_si_el_paso_pide_confirmacion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    manager, workspace = _make_manager_real(tmp_path)

    async def fake_run(_self, **kwargs):
        if "Paso a ejecutar" in kwargs["prompt"]:
            kwargs["write_event"](
                "confirmation_required",
                '{"tool_call_id": "x", "name": "ejecutar_pentestgpt_autorizado"}',
            )
            return
        kwargs["write_event"]("assistant_final", "listo")

    monkeypatch.setattr("edecan_companion.ide_workers_agent.WorkersIDEAgent.run", fake_run)

    session_id = await _sesion_lista(manager, workspace["id"], "Prepara el terreno")
    plan = manager.crear_plan_externo(
        workspace["id"], "1. Pentest activo del servidor.\n", session_id=session_id
    )

    resultado = await manager.avanzar_plan_externo(session_id, plan["id"])
    assert resultado["ok"] is False
    assert "confirmación humana" in resultado["motivo"]
    assert resultado["plan"]["pasos"][0]["estado"] == "fallado"
    assert resultado["plan"]["estado"] == "bloqueado"


async def test_avanzar_plan_externo_rechaza_si_la_sesion_ya_esta_ocupada(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from edecan_companion.ide_sessions import IDESessionError

    manager, workspace = _make_manager_real(tmp_path)

    async def fake_run(_self, **kwargs):
        kwargs["write_event"]("assistant_final", "listo")

    monkeypatch.setattr("edecan_companion.ide_workers_agent.WorkersIDEAgent.run", fake_run)

    session_id = await _sesion_lista(manager, workspace["id"], "Prepara el terreno")
    plan = manager.crear_plan_externo(
        workspace["id"], "1. Paso único.\n", session_id=session_id
    )

    session = manager._get(session_id, "agent")
    session.metadata["status"] = "running"  # simula un turno normal en curso

    with pytest.raises(IDESessionError):
        await manager.avanzar_plan_externo(session_id, plan["id"])

    # No se tocó ningún paso -- el rechazo fue antes de reclamar nada.
    assert manager.ejecutor_planes.obtener(plan["id"])["pasos"][0]["estado"] == "pendiente"
