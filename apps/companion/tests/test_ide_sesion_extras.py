"""``ide_sesion_extras``: ``/background``, ``/tasks``, ``/export``, ``/copy`` y
``/permissions`` del IDE.

Lo que estas pruebas fijan como comportamiento:
- ``/background``/``/tasks``: solo se puede mandar a segundo plano una sesión
  en curso; ``/tasks`` junta agente+terminal de todos los workspaces, activas
  primero, y refleja el estado de segundo plano guardado; la marca sobrevive
  a recargar el store desde disco.
- ``/export``: junta conversación Y acciones (comandos/archivos), sin
  duplicados, y no revienta con una sesión sin charla o sin acciones.
- ``/copy``: toma SIEMPRE la última respuesta del agente (no una anterior ni
  un evento de otro tipo) y la aplana a texto plano.
- ``/permissions``: por defecto las tres categorías exigen confirmación;
  solo aflojar una categoría explícita cambia eso, queda en el historial, y
  el gate de solo-lectura nunca se activa.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from edecan_companion.ide_sesion_extras import (
    CATEGORIAS,
    HERRAMIENTA_A_CATEGORIA,
    IDESesionExtrasError,
    PermisosIDEError,
    PermissionsStore,
    SegundoPlanoStore,
    copiar_ultima_respuesta,
    enviar_a_segundo_plano,
    exportar_markdown,
    listar_tareas,
    texto_plano_desde_markdown,
    traer_a_primer_plano,
)


class _FakeManager:
    """Duplica solo la forma pública de ``SessionManager.list`` que usa este
    módulo — ``ide_sesion_extras`` no importa ``ide_sessions`` a propósito
    (ver su docstring), así que las pruebas tampoco necesitan la clase real
    (que abriría procesos ``pty``/hilos de verdad)."""

    def __init__(self, sessions: dict[str, list[dict[str, Any]]]) -> None:
        self._sessions = sessions

    def list(self, kind: str, workspace_id: str | None = None) -> dict[str, Any]:
        rows = [row for row in self._sessions.get(kind, []) if row.get("kind") == kind]
        if workspace_id is not None:
            rows = [row for row in rows if row.get("workspace_id") == workspace_id]
        return {"sessions": rows}


def _fila(
    session_id: str,
    kind: str,
    *,
    status: str = "running",
    workspace_id: str = "ws-1",
    workspace_name: str = "Proyecto",
    title: str = "Sesión",
    started_at: str | None = "2026-07-28T10:00:00+00:00",
    ended_at: str | None = None,
) -> dict[str, Any]:
    return {
        "id": session_id,
        "kind": kind,
        "workspace_id": workspace_id,
        "workspace_name": workspace_name,
        "title": title,
        "status": status,
        "started_at": started_at,
        "ended_at": ended_at,
    }


# --------------------------------------------------------------------- #
# /background + /tasks
# --------------------------------------------------------------------- #


def test_enviar_a_segundo_plano_marca_una_sesion_en_curso(tmp_path: Path) -> None:
    manager = _FakeManager({"agent": [_fila("a1", "agent", status="running")]})
    store = SegundoPlanoStore(tmp_path)

    tarea = enviar_a_segundo_plano(manager, store, "a1", "agent")

    assert tarea.en_segundo_plano is True
    assert store.esta_en_segundo_plano("a1") is True


def test_enviar_a_segundo_plano_rechaza_sesion_terminada(tmp_path: Path) -> None:
    manager = _FakeManager({"agent": [_fila("a1", "agent", status="completed")]})
    store = SegundoPlanoStore(tmp_path)

    with pytest.raises(IDESesionExtrasError, match="en curso"):
        enviar_a_segundo_plano(manager, store, "a1", "agent")


def test_enviar_a_segundo_plano_rechaza_sesion_inexistente(tmp_path: Path) -> None:
    manager = _FakeManager({"agent": []})
    store = SegundoPlanoStore(tmp_path)

    with pytest.raises(IDESesionExtrasError, match="no encontrada"):
        enviar_a_segundo_plano(manager, store, "no-existe", "agent")


def test_enviar_a_segundo_plano_rechaza_kind_desconocido(tmp_path: Path) -> None:
    manager = _FakeManager({})
    store = SegundoPlanoStore(tmp_path)

    with pytest.raises(IDESesionExtrasError, match="desconocido"):
        enviar_a_segundo_plano(manager, store, "a1", "notebook")


def test_traer_a_primer_plano_no_exige_que_siga_activa(tmp_path: Path) -> None:
    manager = _FakeManager({"agent": [_fila("a1", "agent", status="completed")]})
    store = SegundoPlanoStore(tmp_path)
    store.marcar("a1")

    tarea = traer_a_primer_plano(manager, store, "a1", "agent")

    assert tarea.en_segundo_plano is False
    assert store.esta_en_segundo_plano("a1") is False


def test_marca_de_segundo_plano_sobrevive_a_recargar_el_store(tmp_path: Path) -> None:
    primero = SegundoPlanoStore(tmp_path)
    primero.marcar("a1")

    segundo = SegundoPlanoStore(tmp_path)

    assert segundo.esta_en_segundo_plano("a1") is True


def test_listar_tareas_junta_agente_y_terminal_de_todos_los_workspaces(tmp_path: Path) -> None:
    manager = _FakeManager(
        {
            "agent": [
                _fila("a1", "agent", status="running", workspace_id="ws-1"),
                _fila("a2", "agent", status="completed", workspace_id="ws-2"),
            ],
            "terminal": [_fila("t1", "terminal", status="running", workspace_id="ws-2")],
        }
    )
    store = SegundoPlanoStore(tmp_path)
    store.marcar("t1")

    activas = listar_tareas(manager, store)
    ids_activas = {tarea.id for tarea in activas}
    assert ids_activas == {"a1", "t1"}

    todas = listar_tareas(manager, store, solo_activas=False)
    por_id = {tarea.id: tarea for tarea in todas}
    assert len(todas) == 3
    assert por_id["t1"].en_segundo_plano is True
    assert por_id["a1"].en_segundo_plano is False


def test_listar_tareas_pone_las_activas_primero(tmp_path: Path) -> None:
    manager = _FakeManager(
        {
            "agent": [
                _fila(
                    "terminada",
                    "agent",
                    status="completed",
                    started_at="2026-07-28T12:00:00+00:00",
                ),
                _fila(
                    "en_curso",
                    "agent",
                    status="running",
                    started_at="2026-07-28T09:00:00+00:00",
                ),
            ],
        }
    )
    store = SegundoPlanoStore(tmp_path)

    tareas = listar_tareas(manager, store, solo_activas=False)

    assert [tarea.id for tarea in tareas] == ["en_curso", "terminada"]


# --------------------------------------------------------------------- #
# /export
# --------------------------------------------------------------------- #


def _sesion_publica(**overrides: Any) -> dict[str, Any]:
    base = {
        "title": "Arreglar el bug de login",
        "workspace_name": "Edecán",
        "kind": "agent",
        "status": "completed",
        "started_at": "2026-07-28T10:00:00+00:00",
        "ended_at": "2026-07-28T10:05:00+00:00",
    }
    base.update(overrides)
    return base


def test_exportar_markdown_incluye_conversacion_y_acciones() -> None:
    eventos = [
        {"type": "user", "text": "arregla el bug de login"},
        {"type": "command", "text": "pytest -q"},
        {"type": "file", "text": "apps/api/auth.py"},
        {"type": "command", "text": "pytest -q"},  # repetido: no debe duplicarse
        {"type": "output", "text": "ruido de stdout"},
        {"type": "assistant_final", "text": "Listo, ya corregí el bug."},
    ]

    markdown = exportar_markdown(_sesion_publica(), eventos)

    assert "# Arreglar el bug de login" in markdown
    assert "**Tú:** arregla el bug de login" in markdown
    assert "**Edecán:** Listo, ya corregí el bug." in markdown
    assert markdown.count("pytest -q") == 1
    assert "apps/api/auth.py" in markdown
    assert "ruido de stdout" not in markdown


def test_exportar_markdown_sin_charla_ni_acciones_no_revienta() -> None:
    markdown = exportar_markdown(_sesion_publica(), [])

    assert "Sin mensajes registrados" in markdown
    assert "No se registraron comandos ni archivos tocados" in markdown


# --------------------------------------------------------------------- #
# /copy
# --------------------------------------------------------------------- #


def test_copiar_ultima_respuesta_usa_la_mas_reciente() -> None:
    eventos = [
        {"type": "assistant_final", "text": "primera respuesta"},
        {"type": "user", "text": "otra cosa"},
        {"type": "assistant_final", "text": "**segunda** respuesta"},
    ]

    assert copiar_ultima_respuesta(eventos) == "segunda respuesta"


def test_copiar_ultima_respuesta_sin_respuesta_lanza_error() -> None:
    with pytest.raises(IDESesionExtrasError, match="respuesta"):
        copiar_ultima_respuesta([{"type": "user", "text": "hola"}])


@pytest.mark.parametrize(
    ("markdown", "esperado"),
    [
        ("# Título\ncontenido", "Título\ncontenido"),
        ("**negrita** y _no toques esto_", "negrita y _no toques esto_"),
        ("texto con *cursiva* simple", "texto con cursiva simple"),
        ("usa `codigo_inline()` aquí", "usa codigo_inline() aquí"),
        ("mira [el enlace](https://ejemplo.com)", "mira el enlace"),
        ("```python\nx = 1\n```", "x = 1"),
    ],
)
def test_texto_plano_desde_markdown_quita_marcas_comunes(markdown: str, esperado: str) -> None:
    assert texto_plano_desde_markdown(markdown) == esperado


# --------------------------------------------------------------------- #
# /permissions
# --------------------------------------------------------------------- #


def test_politica_por_defecto_exige_confirmacion_en_las_tres_categorias(tmp_path: Path) -> None:
    store = PermissionsStore(tmp_path)

    assert store.politica_actual() == dict.fromkeys(CATEGORIAS, False)
    for categoria in CATEGORIAS:
        assert store.requiere_confirmacion(categoria) is True


def test_herramientas_de_solo_lectura_nunca_exigen_confirmacion(tmp_path: Path) -> None:
    store = PermissionsStore(tmp_path)

    for herramienta in ("listar_archivos", "leer_archivo", "buscar_en_archivos", "algo_inventado"):
        assert herramienta not in HERRAMIENTA_A_CATEGORIA
        assert store.requiere_confirmacion_para_herramienta(herramienta) is False


@pytest.mark.parametrize(
    ("herramienta", "categoria"),
    [
        ("ejecutar_comando", "ejecutar_comandos"),
        ("escribir_archivo", "escribir_archivos"),
        ("editar_archivo", "escribir_archivos"),
        ("buscar_web", "acceder_red"),
    ],
)
def test_herramientas_gateadas_exigen_confirmacion_por_defecto(
    tmp_path: Path, herramienta: str, categoria: str
) -> None:
    store = PermissionsStore(tmp_path)

    assert HERRAMIENTA_A_CATEGORIA[herramienta] == categoria
    assert store.requiere_confirmacion_para_herramienta(herramienta) is True


def test_permitir_automatico_afloja_solo_esa_categoria(tmp_path: Path) -> None:
    store = PermissionsStore(tmp_path)

    store.permitir_automatico("escribir_archivos")

    assert store.requiere_confirmacion("escribir_archivos") is False
    assert store.requiere_confirmacion("ejecutar_comandos") is True
    assert store.requiere_confirmacion("acceder_red") is True
    assert store.requiere_confirmacion_para_herramienta("escribir_archivo") is False
    assert store.requiere_confirmacion_para_herramienta("ejecutar_comando") is True


def test_permitir_automatico_queda_en_el_historial_auditado(tmp_path: Path) -> None:
    store = PermissionsStore(tmp_path)

    cambio = store.permitir_automatico("acceder_red")

    historial = store.historial()
    assert len(historial) == 1
    assert historial[0]["categoria"] == "acceder_red"
    assert historial[0]["automatico"] is True
    assert historial[0]["en"] == cambio.en


def test_exigir_confirmacion_revierte_el_aflojado(tmp_path: Path) -> None:
    store = PermissionsStore(tmp_path)
    store.permitir_automatico("ejecutar_comandos")

    store.exigir_confirmacion("ejecutar_comandos")

    assert store.requiere_confirmacion("ejecutar_comandos") is True
    assert len(store.historial()) == 2


def test_politica_de_permisos_sobrevive_a_recargar_el_store(tmp_path: Path) -> None:
    primero = PermissionsStore(tmp_path)
    primero.permitir_automatico("escribir_archivos")

    segundo = PermissionsStore(tmp_path)

    assert segundo.requiere_confirmacion("escribir_archivos") is False
    assert segundo.requiere_confirmacion("ejecutar_comandos") is True
    assert len(segundo.historial()) == 1


def test_categoria_desconocida_lanza_error(tmp_path: Path) -> None:
    store = PermissionsStore(tmp_path)

    with pytest.raises(PermisosIDEError, match="desconocida"):
        store.requiere_confirmacion("vuela_al_espacio")
    with pytest.raises(PermisosIDEError, match="desconocida"):
        store.permitir_automatico("vuela_al_espacio")
