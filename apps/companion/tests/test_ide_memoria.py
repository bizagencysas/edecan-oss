"""Pruebas de memoria persistente del proyecto -- ``ide_memoria.MemoriaStore``.

Cubre lo que pidió el encargo explícitamente: guardar, recuperar por
relevancia (no un volcado completo), el tope por workspace, y que el ruido
trivial no se guarde. Suma además las dos piezas de diseño que el módulo
documenta como decisiones propias: deduplicación por refuerzo (repetir el
mismo hecho no crea una fila nueva) y aislamiento entre workspaces.

El último bloque cubre las ADR (9.2 del plan): una decisión que además
guarda las alternativas descartadas, por qué se descartaron y qué la
invalidaría. Lo que más se prueba ahí no es que los campos se guarden --
eso es lo fácil-- sino las tres formas de perderlos: que se cuelen en un
tipo de recuerdo donde no significan nada, que un recuerdo guardado antes
de que existieran deje de leerse, y que reforzar una decisión sin repetir
el porqué lo borre.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from edecan_companion.ide_memoria import (
    MAX_ALTERNATIVA_CHARS,
    MAX_ALTERNATIVAS,
    MAX_CONTENT_CHARS,
    MAX_PROMPT_BLOCK_CHARS,
    IDEMemoriaError,
    MemoriaStore,
)
from edecan_companion.ide_workspaces import WorkspaceStore


def _make_store(tmp_path: Path, **kwargs) -> tuple[MemoriaStore, str]:
    state_dir = tmp_path / "state"
    project = tmp_path / "proyecto"
    project.mkdir()
    workspaces = WorkspaceStore(state_dir)
    registro = workspaces.authorize(str(project))
    memoria = MemoriaStore(state_dir, workspaces, **kwargs)
    return memoria, registro["id"]


# --------------------------------------------------------------------- #
# Guardar y recuperar por relevancia.
# --------------------------------------------------------------------- #


def test_remember_y_recall_basico_encuentra_por_palabras_en_comun(tmp_path: Path):
    memoria, workspace_id = _make_store(tmp_path)
    memoria.remember(
        workspace_id,
        "Los tests de pytest deben correr con el intérprete de .venv, nunca con pyenv.",
        "error_evitar",
    )

    resultados = memoria.recall(workspace_id, "¿cómo corro los tests de pytest en este repo?")

    assert len(resultados) == 1
    assert "pytest" in resultados[0]["content"]
    assert resultados[0]["score"] > 0.0


def test_recall_no_vuelca_todo_si_no_hay_coincidencia_lexica(tmp_path: Path):
    """El criterio central del módulo: recall() filtra por relevancia, no
    devuelve "lo que sea" cuando nada de lo guardado toca la pregunta."""

    memoria, workspace_id = _make_store(tmp_path)
    memoria.remember(
        workspace_id,
        "El logo de la marca siempre lleva la letra D con degradado Aurora.",
        "convencion",
    )

    resultados = memoria.recall(workspace_id, "¿dónde se configura el webhook de facturación?")

    assert resultados == []


def test_recall_ordena_por_relevancia_y_respeta_k(tmp_path: Path):
    memoria, workspace_id = _make_store(tmp_path)
    memoria.remember(
        workspace_id,
        "El router de IDE vive en routers/ide.py y es un cuello de botella del equipo.",
        "ubicacion",
    )
    memoria.remember(
        workspace_id,
        "Las sesiones del IDE se manejan en ide_sessions.py, otro cuello de botella.",
        "ubicacion",
    )
    memoria.remember(
        workspace_id,
        "El logo de la marca siempre lleva la letra D con degradado Aurora.",
        "convencion",
    )

    resultados = memoria.recall(workspace_id, "dónde está el router de sesiones del IDE", k=1)

    assert len(resultados) == 1
    contenido = resultados[0]["content"]
    assert "ide_sessions.py" in contenido or "routers/ide.py" in contenido


def test_recall_marca_los_recuerdos_devueltos_como_usados(tmp_path: Path):
    memoria, workspace_id = _make_store(tmp_path)
    memoria.remember(workspace_id, "El intérprete de tests vive en .venv/bin/python.", "ubicacion")

    antes = memoria.list_notes(workspace_id)[0]
    assert antes["use_count"] == 1

    memoria.recall(workspace_id, "intérprete de tests python")
    despues = memoria.list_notes(workspace_id)[0]
    assert despues["use_count"] == 2
    assert despues["last_used_at"] >= antes["last_used_at"]


# --------------------------------------------------------------------- #
# Ruido trivial: lo que NO debe guardarse.
# --------------------------------------------------------------------- #


@pytest.mark.parametrize("contenido", ["ok", "Gracias!", "listo", "  Vale  ", "bien", "genial"])
def test_frases_triviales_se_rechazan(tmp_path: Path, contenido: str):
    memoria, workspace_id = _make_store(tmp_path)
    with pytest.raises(IDEMemoriaError):
        memoria.remember(workspace_id, contenido, "decision")


def test_contenido_demasiado_corto_se_rechaza(tmp_path: Path):
    memoria, workspace_id = _make_store(tmp_path)
    with pytest.raises(IDEMemoriaError):
        memoria.remember(workspace_id, "usa npm", "convencion")


def test_contenido_demasiado_largo_se_rechaza(tmp_path: Path):
    memoria, workspace_id = _make_store(tmp_path)
    with pytest.raises(IDEMemoriaError):
        memoria.remember(workspace_id, "x" * (MAX_CONTENT_CHARS + 1), "convencion")


def test_kind_invalido_se_rechaza(tmp_path: Path):
    memoria, workspace_id = _make_store(tmp_path)
    with pytest.raises(IDEMemoriaError):
        memoria.remember(workspace_id, "Esto es un hecho real sobre el repo.", "opinion")


def test_workspace_inexistente_se_rechaza(tmp_path: Path):
    memoria, _workspace_id = _make_store(tmp_path)
    with pytest.raises(IDEMemoriaError):
        memoria.remember("no-existe", "Esto es un hecho real sobre el repo.", "convencion")
    with pytest.raises(IDEMemoriaError):
        memoria.recall("no-existe", "algo")


# --------------------------------------------------------------------- #
# Deduplicación por refuerzo.
# --------------------------------------------------------------------- #


def test_guardar_el_mismo_hecho_dos_veces_refuerza_no_duplica(tmp_path: Path):
    memoria, workspace_id = _make_store(tmp_path)
    memoria.remember(
        workspace_id, "Nunca usar voseo en el contenido generado para LinkedIn.", "convencion",
        importance=0.4,
    )
    memoria.remember(
        workspace_id, "  nunca usar voseo en el contenido generado para linkedin.  ",
        "convencion", importance=0.9,
    )

    notas = memoria.list_notes(workspace_id)
    assert len(notas) == 1
    assert notas[0]["use_count"] == 2
    assert notas[0]["importance"] == 0.9  # se queda con la mayor de las dos


# --------------------------------------------------------------------- #
# Tope por workspace.
# --------------------------------------------------------------------- #


def test_tope_por_workspace_purga_el_de_menor_importancia(tmp_path: Path):
    memoria, workspace_id = _make_store(tmp_path, max_notes_per_workspace=2)
    memoria.remember(
        workspace_id, "Hecho poco importante sobre el estilo de comentarios.", "convencion",
        importance=0.1,
    )
    memoria.remember(
        workspace_id, "Hecho medianamente importante sobre el orden de imports.", "convencion",
        importance=0.5,
    )
    memoria.remember(
        workspace_id, "Hecho crítico: nunca hacer commit sin que lo pida el usuario.", "decision",
        importance=0.9,
    )

    notas = memoria.list_notes(workspace_id)
    contenidos = " ".join(nota["content"] for nota in notas)
    assert len(notas) == 2
    assert "poco importante" not in contenidos
    assert "crítico" in contenidos
    assert "medianamente importante" in contenidos


# --------------------------------------------------------------------- #
# Bloque de prompt.
# --------------------------------------------------------------------- #


def test_recall_as_prompt_block_none_sin_coincidencia(tmp_path: Path):
    memoria, workspace_id = _make_store(tmp_path)
    memoria.remember(workspace_id, "El logo lleva la letra D con degradado Aurora.", "convencion")

    assert memoria.recall_as_prompt_block(workspace_id, "facturación mensual") is None


def test_recall_as_prompt_block_arma_texto_listo_para_el_prompt(tmp_path: Path):
    memoria, workspace_id = _make_store(tmp_path)
    memoria.remember(
        workspace_id, "El intérprete correcto para tests es .venv/bin/python, no pyenv.",
        "error_evitar",
    )

    bloque = memoria.recall_as_prompt_block(workspace_id, "qué intérprete uso para los tests")

    assert bloque is not None
    assert "error_evitar" in bloque
    assert ".venv/bin/python" in bloque


# --------------------------------------------------------------------- #
# Listado, borrado, y aislamiento entre workspaces.
# --------------------------------------------------------------------- #


def test_list_notes_no_filtra_por_relevancia(tmp_path: Path):
    memoria, workspace_id = _make_store(tmp_path)
    memoria.remember(workspace_id, "Primer hecho guardado sobre este repo de prueba.", "decision")
    memoria.remember(
        workspace_id, "Segundo hecho, sobre un tema completamente distinto.", "ubicacion"
    )

    assert len(memoria.list_notes(workspace_id)) == 2


def test_forget_borra_un_recuerdo_puntual(tmp_path: Path):
    memoria, workspace_id = _make_store(tmp_path)
    creado = memoria.remember(
        workspace_id, "Hecho que luego se va a olvidar a propósito.", "decision"
    )

    memoria.forget(workspace_id, creado["id"])

    assert memoria.list_notes(workspace_id) == []
    with pytest.raises(IDEMemoriaError):
        memoria.forget(workspace_id, creado["id"])


def test_memoria_no_se_filtra_entre_workspaces_distintos(tmp_path: Path):
    state_dir = tmp_path / "state"
    proyecto_a = tmp_path / "proyecto_a"
    proyecto_b = tmp_path / "proyecto_b"
    proyecto_a.mkdir()
    proyecto_b.mkdir()
    workspaces = WorkspaceStore(state_dir)
    workspace_a = workspaces.authorize(str(proyecto_a))["id"]
    workspace_b = workspaces.authorize(str(proyecto_b))["id"]
    memoria = MemoriaStore(state_dir, workspaces)

    memoria.remember(workspace_a, "Este hecho pertenece únicamente al proyecto A.", "decision")

    assert len(memoria.list_notes(workspace_a)) == 1
    assert memoria.list_notes(workspace_b) == []


# --------------------------------------------------------------------- #
# Aislamiento entre workspaces también en recall() (no solo en list_notes).
# --------------------------------------------------------------------- #


def test_recuerdo_de_proyecto_no_se_filtra_a_otro_workspace_en_recall(tmp_path: Path):
    """Un recuerdo de un workspace nunca debe aparecer al llamar ``recall``
    desde otro -- este módulo no tiene (ni debe tener) ningún alcance
    compartido entre proyectos; ver la sección del docstring del módulo
    sobre por qué un "hecho verificado global" es trabajo de
    ``ide_conocimiento.py``, no de este archivo."""

    state_dir = tmp_path / "state"
    proyecto_a = tmp_path / "proyecto_a"
    proyecto_b = tmp_path / "proyecto_b"
    proyecto_a.mkdir()
    proyecto_b.mkdir()
    workspaces = WorkspaceStore(state_dir)
    workspace_a = workspaces.authorize(str(proyecto_a))["id"]
    workspace_b = workspaces.authorize(str(proyecto_b))["id"]
    memoria = MemoriaStore(state_dir, workspaces)

    memoria.remember(
        workspace_a, "routers/ide.py es cuello de botella y no se toca sin avisar.", "ubicacion"
    )

    resultados = memoria.recall(workspace_b, "¿dónde está el cuello de botella de routers/ide.py?")

    assert resultados == []


def test_kind_hecho_global_ya_no_existe(tmp_path: Path):
    """Regresión: ``hecho_global`` fue una extensión que llegó a convivir en
    el mismo enum que expone la tool congelada ``recordar_nota_proyecto``
    (``ide_workers_agent.py``) sin fuente obligatoria ni caducidad, y con
    alcance global -- exactamente el agujero de contaminación entre
    proyectos que este módulo existe para evitar. Ver la sección del
    docstring del módulo: ese trabajo es de ``ide_conocimiento.py``, que sí
    exige fuente y hace caducar el hecho. Este tipo no debe volver a
    aceptarse acá."""

    memoria, workspace_id = _make_store(tmp_path)
    with pytest.raises(IDEMemoriaError):
        memoria.remember(
            workspace_id, "Un hecho cualquiera sobre el mundo exterior al repo.", "hecho_global"
        )


def test_datos_viejos_siguen_leyendose(tmp_path: Path):
    """Compatibilidad: una fila guardada por una versión anterior de este
    módulo sigue leyéndose y comportándose igual (ver docstring de
    ``MemoryNote.from_json``)."""

    state_dir = tmp_path / "state"
    project = tmp_path / "proyecto"
    project.mkdir()
    workspaces = WorkspaceStore(state_dir)
    workspace_id = workspaces.authorize(str(project))["id"]

    state_dir.mkdir(parents=True, exist_ok=True)
    fila_vieja = {
        "id": "nota-vieja-1",
        "workspace_id": workspace_id,
        "kind": "decision",
        "content": "No ser un fork de VS Code: decidido, no pendiente.",
        "importance": 0.8,
        "created_at": "2026-01-01T00:00:00Z",
        "last_used_at": "2026-01-01T00:00:00Z",
        "use_count": 1,
    }
    (state_dir / "ide-memoria.json").write_text(
        json.dumps({"version": 1, "notes": [fila_vieja]}), encoding="utf-8"
    )

    memoria = MemoriaStore(state_dir, workspaces)

    notas = memoria.list_notes(workspace_id)
    assert len(notas) == 1
    assert notas[0]["kind"] == "decision"

    resultados = memoria.recall(workspace_id, "fork de VS Code decidido")
    assert len(resultados) == 1
    assert resultados[0]["id"] == "nota-vieja-1"


def test_persiste_entre_instancias_del_store(tmp_path: Path):
    state_dir = tmp_path / "state"
    project = tmp_path / "proyecto"
    project.mkdir()
    workspaces = WorkspaceStore(state_dir)
    workspace_id = workspaces.authorize(str(project))["id"]

    primero = MemoriaStore(state_dir, workspaces)
    primero.remember(
        workspace_id, "Hecho que debe sobrevivir a un reinicio del companion.", "decision"
    )

    segundo = MemoriaStore(state_dir, workspaces)
    assert len(segundo.list_notes(workspace_id)) == 1


# --------------------------------------------------------------------- #
# ADRs: una decisión guarda su porqué, no solo su conclusión (9.2).
# --------------------------------------------------------------------- #

_DECISION = "La memoria del proyecto es 100% local: el companion no habla con base de datos."
_POR_QUE_NO = (
    "El companion se instala solo, en la máquina de quien lo usa; exigir un motor aparte "
    "rompe esa promesa el primer día."
)
_SE_INVALIDA_SI = "El companion pase a depender de un servidor propio por otro motivo ya aceptado."


def _store_desde_disco(tmp_path: Path, filas_para) -> tuple[MemoriaStore, str]:
    """Abre un store encima de un ``ide-memoria.json`` escrito a mano, como
    lo habría dejado una versión anterior del módulo (o una edición manual
    del usuario)."""

    state_dir = tmp_path / "state"
    project = tmp_path / "proyecto"
    project.mkdir()
    workspaces = WorkspaceStore(state_dir)
    workspace_id = workspaces.authorize(str(project))["id"]

    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "ide-memoria.json").write_text(
        json.dumps({"version": 1, "notes": filas_para(workspace_id)}, ensure_ascii=False),
        encoding="utf-8",
    )
    return MemoriaStore(state_dir, workspaces), workspace_id


def test_decision_guarda_alternativas_por_que_no_y_se_invalida_si(tmp_path: Path):
    memoria, workspace_id = _make_store(tmp_path)

    fila = memoria.remember(
        workspace_id,
        _DECISION,
        "decision",
        alternativas=["Postgres con pgvector", "SQLite"],
        por_que_no=_POR_QUE_NO,
        se_invalida_si=_SE_INVALIDA_SI,
    )

    assert fila["alternativas"] == ["Postgres con pgvector", "SQLite"]
    assert fila["por_que_no"] == _POR_QUE_NO
    assert fila["se_invalida_si"] == _SE_INVALIDA_SI
    assert memoria.list_notes(workspace_id)[0]["alternativas"] == [
        "Postgres con pgvector",
        "SQLite",
    ]


def test_una_decision_sin_su_porque_se_sigue_guardando_igual(tmp_path: Path):
    """Los tres campos son opcionales: exigir la ADR completa o nada
    terminaría en que no se guarda ninguna decisión."""

    memoria, workspace_id = _make_store(tmp_path)

    fila = memoria.remember(workspace_id, _DECISION, "decision")

    assert fila["kind"] == "decision"
    # No se serializan claves vacías: en disco esta fila es idéntica a una
    # guardada antes de que los campos de ADR existieran.
    assert "alternativas" not in fila
    assert "por_que_no" not in fila
    assert "se_invalida_si" not in fila


@pytest.mark.parametrize("kind", ["convencion", "ubicacion", "error_evitar"])
@pytest.mark.parametrize("campo", ["alternativas", "por_que_no", "se_invalida_si"])
def test_campos_de_adr_en_un_kind_que_no_es_decision_se_rechazan(
    tmp_path: Path, kind: str, campo: str
):
    """Una convención o una ubicación no descartan alternativas: enuncian
    algo que ya es así. Aceptarles el porqué "por si acaso" convertiría los
    cuatro tipos en cuatro cajones con los mismos campos."""

    memoria, workspace_id = _make_store(tmp_path)
    valores = {
        "alternativas": ["Postgres con pgvector"],
        "por_que_no": _POR_QUE_NO,
        "se_invalida_si": _SE_INVALIDA_SI,
    }

    with pytest.raises(IDEMemoriaError) as exc:
        memoria.remember(workspace_id, _DECISION, kind, **{campo: valores[campo]})

    assert "decision" in str(exc.value)
    assert memoria.list_notes(workspace_id) == []  # tampoco se guardó a medias


def test_campos_de_adr_vacios_no_activan_la_validacion(tmp_path: Path):
    """Un valor vacío es "no lo especifiqué", no "guárdame una ADR": quien
    llame con los tres parámetros siempre presentes (una tool, por ejemplo)
    no debe verse obligada a usar kind='decision'."""

    memoria, workspace_id = _make_store(tmp_path)

    fila = memoria.remember(
        workspace_id,
        "Los tests corren con uv desde apps/companion, nunca con el pytest del sistema.",
        "convencion",
        alternativas=[],
        por_que_no="   ",
        se_invalida_si=None,
    )

    assert fila["kind"] == "convencion"
    assert "por_que_no" not in fila


def test_una_lista_de_alternativas_con_puro_relleno_vacio_tampoco_activa_la_validacion(
    tmp_path: Path,
):
    """Mismo caso que el anterior, con la forma exacta en que lo manda quien
    llena todos los parámetros siempre: la lista existe pero no nombra
    ninguna opción. Rechazar el recuerdo por "traer ADR" sería rechazarlo por
    algo que dos líneas después se descarta por vacío."""

    memoria, workspace_id = _make_store(tmp_path)

    fila = memoria.remember(
        workspace_id,
        "Los tests corren con uv desde apps/companion, nunca con el pytest del sistema.",
        "convencion",
        alternativas=["", "   "],
    )

    assert fila["kind"] == "convencion"
    assert "alternativas" not in fila


def test_la_decision_se_encuentra_por_la_alternativa_que_alguien_quiere_retomar(tmp_path: Path):
    """El caso que justifica toda la pieza: quien está por revertir una
    decisión no escribe la conclusión ("no hablamos con base de datos"),
    escribe la opción que está a punto de retomar. Sin el porqué guardado,
    el recuerdo no aparece y la decisión se revierte sin que nadie sepa que
    ya se había evaluado."""

    memoria, workspace_id = _make_store(tmp_path)
    memoria.remember(workspace_id, _DECISION, "decision")

    pregunta = "¿y si indexamos con pgvector?"
    assert memoria.recall(workspace_id, pregunta) == []

    memoria.remember(
        workspace_id,
        _DECISION,
        "decision",
        alternativas=["Postgres con pgvector", "SQLite"],
        por_que_no=_POR_QUE_NO,
    )

    resultados = memoria.recall(workspace_id, pregunta)
    assert len(resultados) == 1
    assert resultados[0]["content"] == _DECISION


def test_reforzar_una_decision_le_agrega_el_porque_sin_duplicar_la_fila(tmp_path: Path):
    """La deduplicación mira solo la conclusión, así que anotar después las
    alternativas enriquece la ADR que ya existe en vez de crear una segunda
    que la contradiga a medias."""

    memoria, workspace_id = _make_store(tmp_path)
    memoria.remember(workspace_id, _DECISION, "decision")

    memoria.remember(
        workspace_id, _DECISION, "decision", alternativas=["SQLite"], por_que_no=_POR_QUE_NO
    )

    notas = memoria.list_notes(workspace_id)
    assert len(notas) == 1
    assert notas[0]["use_count"] == 2
    assert notas[0]["alternativas"] == ["SQLite"]
    assert notas[0]["por_que_no"] == _POR_QUE_NO


def test_reforzar_una_decision_suma_las_alternativas_en_vez_de_pisarlas(tmp_path: Path):
    """Cada sesión anota las opciones que ELLA evaluó, no la lista completa.

    Si la segunda escritura pisara a la primera, la opción descartada en
    marzo dejaría de encontrarse en junio -- y el descarte que ya nadie
    recuerda es justo el que alguien está a punto de revivir.
    """

    memoria, workspace_id = _make_store(tmp_path)
    memoria.remember(workspace_id, _DECISION, "decision", alternativas=["Postgres con pgvector"])

    memoria.remember(workspace_id, _DECISION, "decision", alternativas=["SQLite"])

    notas = memoria.list_notes(workspace_id)
    assert len(notas) == 1
    assert notas[0]["alternativas"] == ["Postgres con pgvector", "SQLite"]
    # Y lo que de verdad importa: la primera se sigue encontrando cuando
    # alguien está por proponerla otra vez.
    encontrados = memoria.recall(workspace_id, "¿y si indexamos con pgvector?")
    assert [hit["content"] for hit in encontrados] == [_DECISION]


def test_reforzar_repitiendo_una_alternativa_ya_guardada_no_la_duplica(tmp_path: Path):
    memoria, workspace_id = _make_store(tmp_path)
    memoria.remember(workspace_id, _DECISION, "decision", alternativas=["SQLite", "Postgres"])

    memoria.remember(workspace_id, _DECISION, "decision", alternativas=["  sqlite  ", "Redis"])

    assert memoria.list_notes(workspace_id)[0]["alternativas"] == ["SQLite", "Postgres", "Redis"]


def test_sumar_alternativas_por_encima_del_tope_avisa_y_no_refuerza_a_medias(tmp_path: Path):
    """El tope sigue valiendo sobre la lista fusionada: pasarlo se avisa en
    vez de recortar en silencio, porque recortar borraría justo el descarte
    que esta pieza existe para conservar."""

    memoria, workspace_id = _make_store(tmp_path)
    ya_guardadas = [f"Opción evaluada {i}" for i in range(MAX_ALTERNATIVAS)]
    memoria.remember(workspace_id, _DECISION, "decision", alternativas=ya_guardadas)

    with pytest.raises(IDEMemoriaError) as exc:
        memoria.remember(workspace_id, _DECISION, "decision", alternativas=["Una más"])

    assert "forget" in str(exc.value)
    nota = memoria.list_notes(workspace_id)[0]
    assert nota["alternativas"] == ya_guardadas
    assert nota["use_count"] == 1  # el intento fallido tampoco la reforzó


def test_reforzar_sin_repetir_el_porque_no_lo_borra(tmp_path: Path):
    memoria, workspace_id = _make_store(tmp_path)
    memoria.remember(
        workspace_id,
        _DECISION,
        "decision",
        alternativas=["SQLite"],
        por_que_no=_POR_QUE_NO,
        se_invalida_si=_SE_INVALIDA_SI,
    )

    memoria.remember(workspace_id, _DECISION, "decision", importance=0.9)

    nota = memoria.list_notes(workspace_id)[0]
    assert nota["alternativas"] == ["SQLite"]
    assert nota["por_que_no"] == _POR_QUE_NO
    assert nota["se_invalida_si"] == _SE_INVALIDA_SI


def test_reforzar_con_adr_un_recuerdo_que_no_es_decision_se_rechaza(tmp_path: Path):
    """Pegarle el porqué a la fila existente la dejaría siendo una
    'convencion' con alternativas descartadas. Se avisa en vez de cambiarle
    el tipo por debajo a un recuerdo que alguien ya clasificó."""

    memoria, workspace_id = _make_store(tmp_path)
    memoria.remember(workspace_id, _DECISION, "convencion")

    with pytest.raises(IDEMemoriaError) as exc:
        memoria.remember(workspace_id, _DECISION, "decision", por_que_no=_POR_QUE_NO)

    assert "forget" in str(exc.value)
    nota = memoria.list_notes(workspace_id)[0]
    assert nota["kind"] == "convencion"
    assert "por_que_no" not in nota
    assert nota["use_count"] == 1  # el intento fallido tampoco lo reforzó


# -- Validación de los campos de ADR (la misma que ya usa el contenido) -- #


def test_alternativas_como_texto_suelto_se_rechaza(tmp_path: Path):
    """Un texto es iterable: sin esta guarda, 'SQLite' quedaría guardado
    como seis alternativas de una letra cada una."""

    memoria, workspace_id = _make_store(tmp_path)

    with pytest.raises(IDEMemoriaError) as exc:
        memoria.remember(workspace_id, _DECISION, "decision", alternativas="Postgres, SQLite")

    assert "lista" in str(exc.value)


def test_demasiadas_alternativas_se_rechazan(tmp_path: Path):
    memoria, workspace_id = _make_store(tmp_path)

    with pytest.raises(IDEMemoriaError):
        memoria.remember(
            workspace_id,
            _DECISION,
            "decision",
            alternativas=[f"Opción evaluada {i}" for i in range(MAX_ALTERNATIVAS + 1)],
        )


@pytest.mark.parametrize("alternativa", ["ok", "gracias", "x" * (MAX_ALTERNATIVA_CHARS + 1)])
def test_alternativa_trivial_o_larguisima_se_rechaza(tmp_path: Path, alternativa: str):
    memoria, workspace_id = _make_store(tmp_path)

    with pytest.raises(IDEMemoriaError):
        memoria.remember(workspace_id, _DECISION, "decision", alternativas=[alternativa])


@pytest.mark.parametrize("razon", ["ok", "corto", "x" * (MAX_CONTENT_CHARS + 1), 42])
def test_por_que_no_invalido_se_rechaza(tmp_path: Path, razon):
    memoria, workspace_id = _make_store(tmp_path)

    with pytest.raises(IDEMemoriaError):
        memoria.remember(workspace_id, _DECISION, "decision", por_que_no=razon)


def test_alternativa_nombrada_dos_veces_se_guarda_una_sola_vez(tmp_path: Path):
    memoria, workspace_id = _make_store(tmp_path)

    fila = memoria.remember(
        workspace_id, _DECISION, "decision", alternativas=["SQLite", "  sqlite  ", "Postgres"]
    )

    assert fila["alternativas"] == ["SQLite", "Postgres"]


def test_una_alternativa_puede_ser_solo_un_nombre_corto(tmp_path: Path):
    """El mínimo de un recuerdo (12 caracteres) no aplica acá: una
    alternativa se nombra, no se explica, y 'SQLite' es una opción
    perfectamente real que se descartó."""

    memoria, workspace_id = _make_store(tmp_path)

    fila = memoria.remember(workspace_id, _DECISION, "decision", alternativas=["SQLite"])

    assert fila["alternativas"] == ["SQLite"]


# -- Bloque de prompt ---------------------------------------------------- #


def test_recall_as_prompt_block_muestra_el_porque_completo(tmp_path: Path):
    memoria, workspace_id = _make_store(tmp_path)
    memoria.remember(
        workspace_id,
        _DECISION,
        "decision",
        alternativas=["Postgres con pgvector", "SQLite"],
        por_que_no=_POR_QUE_NO,
        se_invalida_si=_SE_INVALIDA_SI,
    )

    bloque = memoria.recall_as_prompt_block(workspace_id, "dónde guardamos la memoria del proyecto")

    assert bloque is not None
    assert _DECISION in bloque
    assert "Postgres con pgvector" in bloque
    assert "SQLite" in bloque
    assert _POR_QUE_NO in bloque
    assert _SE_INVALIDA_SI in bloque
    assert "se invalida si" in bloque


def test_recall_as_prompt_block_sin_adr_se_ve_igual_que_siempre(tmp_path: Path):
    """Un recuerdo sin porqué no debe arrastrar viñetas vacías ni la
    advertencia sobre decisiones: el bloque tiene que hablar solo de lo que
    de verdad trae."""

    memoria, workspace_id = _make_store(tmp_path)
    memoria.remember(
        workspace_id, "El intérprete correcto para tests es .venv/bin/python, no pyenv.",
        "error_evitar",
    )

    bloque = memoria.recall_as_prompt_block(workspace_id, "qué intérprete uso para los tests")

    assert bloque is not None
    lineas = bloque.splitlines()
    assert len(lineas) == 2
    assert lineas[1].startswith("- (error_evitar) ")
    assert "·" not in bloque
    assert "se invalida si" not in bloque


def test_recall_as_prompt_block_corta_por_recuerdo_entero_no_a_mitad_de_una_razon(tmp_path: Path):
    """Media razón con toda la apariencia de estar completa es peor que
    ninguna: sobre esa media razón el modelo decide igual."""

    memoria, workspace_id = _make_store(tmp_path)
    relleno = "razón larga y detallada del descarte, escrita entera " * 12
    for i in range(6):
        memoria.remember(
            workspace_id,
            f"Decisión {i} sobre el motor de plantillas del proyecto.",
            "decision",
            alternativas=[f"Opción {j} " + "x" * 90 for j in range(MAX_ALTERNATIVAS)],
            por_que_no=relleno[:MAX_CONTENT_CHARS],
            se_invalida_si=relleno[:400],
        )

    bloque = memoria.recall_as_prompt_block(workspace_id, "motor de plantillas del proyecto")

    assert bloque is not None
    assert len(bloque) <= MAX_PROMPT_BLOCK_CHARS
    entradas = bloque.count("- (decision)")
    assert 0 < entradas < 6  # de verdad tuvo que recortar
    # Ninguna entrada quedó a medias: todas conservan su última viñeta.
    assert bloque.count("· se invalida si:") == entradas
    assert bloque.endswith(relleno[:400].strip())


# -- Compatibilidad con lo que ya está guardado en disco ----------------- #


def test_decision_vieja_sin_campos_de_adr_se_lee_sin_migracion(tmp_path: Path):
    """Requisito duro de la extensión: los recuerdos guardados antes de que
    estos campos existieran siguen cargando, recuperándose y formateándose
    igual, sin ningún paso de conversión."""

    def filas(workspace_id: str) -> list[dict]:
        return [
            {
                "id": "nota-vieja-adr",
                "workspace_id": workspace_id,
                "kind": "decision",
                "content": "No ser un fork de VS Code: decidido, no pendiente.",
                "importance": 0.8,
                "created_at": "2026-01-01T00:00:00Z",
                "last_used_at": "2026-01-01T00:00:00Z",
                "use_count": 1,
            }
        ]

    memoria, workspace_id = _store_desde_disco(tmp_path, filas)

    nota = memoria.list_notes(workspace_id)[0]
    assert nota["kind"] == "decision"
    assert "alternativas" not in nota
    assert "por_que_no" not in nota

    bloque = memoria.recall_as_prompt_block(workspace_id, "fork de VS Code decidido")
    assert bloque is not None
    assert "·" not in bloque

    # Y se le puede agregar el porqué después, sin duplicar la fila vieja.
    memoria.remember(
        workspace_id,
        "No ser un fork de VS Code: decidido, no pendiente.",
        "decision",
        alternativas=["Fork de VS Code"],
        por_que_no="Un fork nos ata al ciclo de releases de otro y a su deuda técnica entera.",
    )
    notas = memoria.list_notes(workspace_id)
    assert len(notas) == 1
    assert notas[0]["id"] == "nota-vieja-adr"
    assert notas[0]["alternativas"] == ["Fork de VS Code"]


def test_fila_con_adr_en_un_kind_que_no_es_decision_se_degrada_al_leer(tmp_path: Path):
    """Un archivo editado a mano puede traer la incoherencia que `remember`
    impide. Se ignoran los campos en vez de reventar la carga: la regla vale
    también para lo que ya está en disco, y perder el archivo entero por una
    fila rara sería peor."""

    def filas(workspace_id: str) -> list[dict]:
        return [
            {
                "id": "nota-incoherente",
                "workspace_id": workspace_id,
                "kind": "convencion",
                "content": "Español LATAM con tú, nunca voseo, tampoco en el contenido generado.",
                "importance": 0.9,
                "created_at": "2026-01-01T00:00:00Z",
                "last_used_at": "2026-01-01T00:00:00Z",
                "use_count": 1,
                "alternativas": ["Español neutro"],
                "por_que_no": "Razón que nunca debió guardarse en una convención.",
            }
        ]

    memoria, workspace_id = _store_desde_disco(tmp_path, filas)

    nota = memoria.list_notes(workspace_id)[0]
    assert nota["kind"] == "convencion"
    assert "alternativas" not in nota
    assert "por_que_no" not in nota


def test_el_porque_sobrevive_a_un_reinicio_del_companion(tmp_path: Path):
    state_dir = tmp_path / "state"
    project = tmp_path / "proyecto"
    project.mkdir()
    workspaces = WorkspaceStore(state_dir)
    workspace_id = workspaces.authorize(str(project))["id"]

    primero = MemoriaStore(state_dir, workspaces)
    primero.remember(
        workspace_id,
        _DECISION,
        "decision",
        alternativas=["Postgres con pgvector", "SQLite"],
        por_que_no=_POR_QUE_NO,
        se_invalida_si=_SE_INVALIDA_SI,
    )

    segundo = MemoriaStore(state_dir, workspaces)
    nota = segundo.list_notes(workspace_id)[0]
    assert nota["alternativas"] == ["Postgres con pgvector", "SQLite"]
    assert nota["por_que_no"] == _POR_QUE_NO
    assert nota["se_invalida_si"] == _SE_INVALIDA_SI
    # Y sigue siendo encontrable por la alternativa descartada, no solo por
    # la conclusión.
    assert len(segundo.recall(workspace_id, "¿y si indexamos con pgvector?")) == 1
