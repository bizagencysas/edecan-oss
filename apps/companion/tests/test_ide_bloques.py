"""Canal de bloques ricos del IDE: la herramienta, el portero y el viaje entero.

Lo que fija esta suite, en orden de importancia:
1. El bloque llega ENTERO desde la llamada del modelo hasta el evento
   persistido de la sesión (que es lo que el teléfono lee).
2. Una gráfica degenerada se rechaza con un motivo que le sirve al modelo, y
   sin escribir nada en el hilo.
3. Una tabla con celdas faltantes no revienta ni corre los datos de columna.
4. El canal es la ÚNICA puerta: un `presentation` con basura no acuña UI.
5. El espejo con `edecan_schemas.ide_blocks` no se desincronizó.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from edecan_companion import ide_workers_agent as agent_module
from edecan_companion.ide_bloques import (
    MAX_TABLE_CELL_CHARS,
    MAX_TABLE_ROWS,
    IDEBloqueError,
    construir_bloque,
    validar_bloques,
)
from edecan_companion.ide_sessions import SessionManager
from edecan_companion.ide_workers_agent import WorkersIDEAgent
from edecan_companion.ide_workspaces import WorkspaceStore
from edecan_llm.base import CompletionRequest, StreamChunk, ToolCall
from edecan_llm.workers_ai import MODELO_IDE_POR_DEFECTO

TABLA_OK: dict[str, Any] = {
    "titulo": "Costo por proveedor",
    "columnas": [
        {"clave": "proveedor", "titulo": "Proveedor"},
        {"clave": "costo", "titulo": "USD / 1M", "alineacion": "right"},
    ],
    "filas": [
        {"proveedor": "Workers AI", "costo": "0.11"},
        {"proveedor": "Otro", "costo": "1.20"},
        {"proveedor": "Tercero", "costo": "3.00"},
    ],
}

GRAFICA_OK: dict[str, Any] = {
    "tipo": "lineas",
    "titulo": "Tiempo de build",
    "eje_y": "ms",
    "series": [
        {
            "nombre": "main",
            "puntos": [
                {"etiqueta": "v1", "valor": 420},
                {"etiqueta": "v2", "valor": 510},
                {"etiqueta": "v3", "valor": 505},
            ],
        }
    ],
}


# ---------------------------------------------------------------------------
# Construcción
# ---------------------------------------------------------------------------


def test_la_tabla_conserva_columnas_filas_y_alineacion():
    bloque, resultado = construir_bloque("mostrar_tabla", TABLA_OK)

    assert bloque["type"] == "table"
    assert bloque["title"] == "Costo por proveedor"
    assert [columna["key"] for columna in bloque["columns"]] == ["proveedor", "costo"]
    assert bloque["columns"][1]["align"] == "right"
    assert bloque["rows"][0] == {"proveedor": "Workers AI", "costo": "0.11"}
    assert resultado["ok"] is True
    assert resultado["filas"] == 3


def test_el_texto_de_respaldo_trae_los_datos_reales_no_un_resumen():
    """El respaldo lo arma el código, no el modelo: es lo que ven el /export,
    el historial reinyectado y cualquier cliente que aún no dibuje bloques."""
    bloque, _ = construir_bloque("mostrar_tabla", TABLA_OK)

    respaldo = bloque["fallback_text"]
    assert "Workers AI" in respaldo
    assert "0.11" in respaldo
    assert "Proveedor" in respaldo


def test_los_numeros_de_una_celda_se_escriben_sin_redondear():
    bloque, _ = construir_bloque(
        "mostrar_tabla",
        {
            "columnas": [{"clave": "a", "titulo": "A"}, {"clave": "b", "titulo": "B"}],
            "filas": [{"a": 1, "b": 0.30000000000000004}, {"a": 2, "b": 1.5}],
        },
    )

    assert bloque["rows"][0] == {"a": "1", "b": "0.30000000000000004"}


def test_la_grafica_normaliza_el_tipo_y_conserva_los_puntos():
    bloque, resultado = construir_bloque("mostrar_grafica", GRAFICA_OK)

    assert bloque["chart_kind"] == "line"
    assert bloque["y_label"] == "ms"
    assert bloque["series"][0]["points"][1] == {"label": "v2", "value": 510.0}
    assert resultado["puntos"] == 3


def test_una_tabla_mas_larga_que_el_tope_se_recorta_y_lo_confiesa():
    filas = [{"a": str(indice)} for indice in range(MAX_TABLE_ROWS + 20)]
    bloque, resultado = construir_bloque(
        "mostrar_tabla", {"columnas": [{"clave": "a", "titulo": "A"}], "filas": filas}
    )

    assert len(bloque["rows"]) == MAX_TABLE_ROWS
    assert f"Se muestran {MAX_TABLE_ROWS} de {len(filas)} filas" in bloque["note"]
    assert "nota" in resultado


# ---------------------------------------------------------------------------
# La regla dura: una gráfica que no se entiende
# ---------------------------------------------------------------------------


def test_una_sola_serie_de_dos_puntos_se_niega_y_sugiere_la_tabla():
    with pytest.raises(IDEBloqueError) as exc:
        construir_bloque(
            "mostrar_grafica",
            {
                "tipo": "barras",
                "titulo": "Antes y después",
                "series": [
                    {
                        "nombre": "latencia",
                        "puntos": [
                            {"etiqueta": "antes", "valor": 900},
                            {"etiqueta": "después", "valor": 300},
                        ],
                    }
                ],
            },
        )

    motivo = str(exc.value)
    assert "dos puntos son una diferencia" in motivo
    assert "mostrar_tabla" in motivo


def test_todos_los_valores_iguales_se_niega():
    with pytest.raises(IDEBloqueError, match="idénticos"):
        construir_bloque(
            "mostrar_grafica",
            {
                "tipo": "barras",
                "titulo": "Todo igual",
                "series": [
                    {
                        "nombre": "a",
                        "puntos": [
                            {"etiqueta": "x", "valor": 3},
                            {"etiqueta": "y", "valor": 3},
                            {"etiqueta": "z", "valor": 3},
                        ],
                    }
                ],
            },
        )


def test_series_sobre_ejes_distintos_se_niegan():
    with pytest.raises(IDEBloqueError, match="mismas etiquetas del eje"):
        construir_bloque(
            "mostrar_grafica",
            {
                "tipo": "lineas",
                "titulo": "Mezcla",
                "series": [
                    {
                        "nombre": "a",
                        "puntos": [
                            {"etiqueta": "v1", "valor": 1},
                            {"etiqueta": "v2", "valor": 2},
                            {"etiqueta": "v3", "valor": 3},
                        ],
                    },
                    {
                        "nombre": "b",
                        "puntos": [
                            {"etiqueta": "otra", "valor": 1},
                            {"etiqueta": "cosa", "valor": 5},
                            {"etiqueta": "mas", "valor": 9},
                        ],
                    },
                ],
            },
        )


def test_una_grafica_sin_titulo_se_niega():
    with pytest.raises(IDEBloqueError, match="titulo"):
        construir_bloque("mostrar_grafica", {**GRAFICA_OK, "titulo": ""})


def test_un_valor_no_numerico_se_niega_con_el_campo_por_nombre():
    with pytest.raises(IDEBloqueError, match="'valor' debe ser un número"):
        construir_bloque(
            "mostrar_grafica",
            {
                "tipo": "lineas",
                "titulo": "Con texto",
                "series": [
                    {
                        "nombre": "a",
                        "puntos": [
                            {"etiqueta": "v1", "valor": "420 ms"},
                            {"etiqueta": "v2", "valor": 2},
                            {"etiqueta": "v3", "valor": 3},
                        ],
                    }
                ],
            },
        )


# ---------------------------------------------------------------------------
# Tabla con celdas faltantes
# ---------------------------------------------------------------------------


def test_una_celda_demasiado_larga_se_recorta_CON_marca():
    """Una celda cortada tiene que verse cortada.

    Sin el "…", una ruta o una URL recortada se lee como el valor completo: la
    celda no revienta, se ve perfecta y dice otra cosa. El resultado queda en
    exactamente el tope para que las revalidaciones de aguas abajo, que cortan
    al mismo número, no se coman la marca.
    """
    ruta = "/Users/example/proyecto/" + "sub/" * 40 + "archivo.py"
    assert len(ruta) > MAX_TABLE_CELL_CHARS
    bloque, _ = construir_bloque(
        "mostrar_tabla",
        {
            "columnas": [{"clave": "ruta", "titulo": "Ruta"}, {"clave": "n", "titulo": "N"}],
            "filas": [{"ruta": ruta, "n": "1"}, {"ruta": "corta.py", "n": "2"}],
        },
    )
    celda = bloque["rows"][0]["ruta"]
    assert len(celda) == MAX_TABLE_CELL_CHARS
    assert celda.endswith("…")
    assert celda[:-1] == ruta[: MAX_TABLE_CELL_CHARS - 1]
    # Una celda que cabe se queda intacta: la marca solo aparece si hubo corte.
    assert bloque["rows"][1]["ruta"] == "corta.py"


def test_el_recorte_de_celda_sobrevive_al_portero():
    """El bloque ya recortado vuelve a pasar por `validar_bloques` sin perder
    la marca -- si el portero recortara otra vez a secas, se comería el "…".
    """
    ruta = "x" * 400
    bloque, _ = construir_bloque(
        "mostrar_tabla",
        {
            "columnas": [{"clave": "r", "titulo": "R"}],
            "filas": [{"r": ruta}, {"r": "b"}, {"r": "c"}],
        },
    )
    [revalidado] = validar_bloques([bloque])
    assert revalidado["rows"][0]["r"].endswith("…")
    assert len(revalidado["rows"][0]["r"]) == MAX_TABLE_CELL_CHARS


def test_una_celda_faltante_deja_hueco_solo_en_su_columna():
    """Documentado: sin dato = clave ausente = el cliente pinta vacío.

    Con filas posicionales, la celda faltante correría todas las siguientes
    bajo la columna equivocada -- una tabla que se dibuja bien y miente.
    """
    bloque, _ = construir_bloque(
        "mostrar_tabla",
        {
            "columnas": [
                {"clave": "a", "titulo": "A"},
                {"clave": "b", "titulo": "B"},
                {"clave": "c", "titulo": "C"},
            ],
            "filas": [{"a": "1", "c": "3"}, {"b": "2"}],
        },
    )

    assert bloque["rows"] == [{"a": "1", "c": "3"}, {"b": "2"}]
    assert "—" in bloque["fallback_text"]


def test_una_clave_sin_columna_se_descarta_y_la_tabla_sigue_viva():
    bloque, _ = construir_bloque(
        "mostrar_tabla",
        {
            "columnas": [{"clave": "a", "titulo": "A"}],
            "filas": [{"a": "1", "colada": "no se dibuja"}, {"a": "2"}],
        },
    )

    assert bloque["rows"] == [{"a": "1"}, {"a": "2"}]
    assert "no se dibuja" not in json.dumps(bloque, ensure_ascii=False)


def test_una_tabla_donde_ninguna_clave_coincide_se_niega_explicando_por_que():
    with pytest.raises(IDEBloqueError, match="las mismas de 'columnas'"):
        construir_bloque(
            "mostrar_tabla",
            {"columnas": [{"clave": "a", "titulo": "A"}], "filas": [{"z": "1"}, {"y": "2"}]},
        )


def test_una_fila_en_lista_se_niega_diciendo_la_forma_correcta():
    with pytest.raises(IDEBloqueError, match="no una lista"):
        construir_bloque(
            "mostrar_tabla",
            {"columnas": [{"clave": "a", "titulo": "A"}], "filas": [["1"], ["2"]]},
        )


def test_una_clave_con_acento_se_niega_porque_viaja_como_llave_json():
    with pytest.raises(IDEBloqueError, match="letras ASCII"):
        construir_bloque(
            "mostrar_tabla",
            {"columnas": [{"clave": "año", "titulo": "Año"}], "filas": [{"año": "2026"}]},
        )


# ---------------------------------------------------------------------------
# El portero del canal
# ---------------------------------------------------------------------------


def test_el_portero_descarta_lo_que_no_es_un_bloque_dibujable():
    bloque, _ = construir_bloque("mostrar_tabla", TABLA_OK)

    validos = validar_bloques(
        [
            bloque,
            {"type": "html", "html": "<script>alert(1)</script>"},
            {"type": "table"},
            "texto suelto",
            None,
        ]
    )

    assert [item["type"] for item in validos] == ["table"]


def test_el_portero_reconstruye_el_respaldo_en_vez_de_confiar_en_el_de_afuera():
    """El texto de respaldo es lo que ven los clientes viejos: si viniera de
    afuera, sería texto arbitrario entrando por el canal de UI."""
    bloque, _ = construir_bloque("mostrar_tabla", TABLA_OK)
    bloque["fallback_text"] = "Todo bien, aprobado por el banco."

    (validado,) = validar_bloques([bloque])

    assert "aprobado por el banco" not in validado["fallback_text"]
    assert "Workers AI" in validado["fallback_text"]


def test_el_portero_rechaza_un_tipo_de_grafica_que_no_reconoce():
    """Sin default: dibujar como líneas algo que se declaró de otro tipo sería
    cambiar en silencio lo que la gráfica dice."""
    bloque, _ = construir_bloque("mostrar_grafica", GRAFICA_OK)
    bloque["chart_kind"] = "pastel"

    assert validar_bloques([bloque]) == []


def test_el_portero_no_deja_pasar_una_grafica_degenerada_aunque_venga_armada():
    bloque, _ = construir_bloque("mostrar_grafica", GRAFICA_OK)
    bloque["series"][0]["points"] = bloque["series"][0]["points"][:2]

    assert validar_bloques([bloque]) == []


def test_el_portero_ignora_lo_que_no_es_una_lista():
    assert validar_bloques(None) == []
    assert validar_bloques({"type": "table"}) == []


# ---------------------------------------------------------------------------
# El viaje entero: del modelo al evento de la sesión
# ---------------------------------------------------------------------------


class _ProviderTabla:
    """Pide `mostrar_tabla` y luego cierra el turno."""

    name = "fake"

    def __init__(self, model: str) -> None:
        self.model = model
        self.requests: list[CompletionRequest] = []
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True

    async def stream(self, request: CompletionRequest):
        self.requests.append(request)
        if len(self.requests) == 1:
            yield StreamChunk(
                type="tool_call",
                tool_call=ToolCall(id="tabla-1", name="mostrar_tabla", arguments=TABLA_OK),
            )
            return
        yield StreamChunk(type="text", text="Workers AI es la opción más barata.")


class _ProviderGraficaDegenerada:
    """Pide una gráfica imposible, escucha el motivo y cae a la tabla."""

    name = "fake"

    def __init__(self, model: str) -> None:
        self.model = model
        self.requests: list[CompletionRequest] = []
        self.motivos: list[str] = []
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True

    async def stream(self, request: CompletionRequest):
        self.requests.append(request)
        if len(self.requests) == 1:
            yield StreamChunk(
                type="tool_call",
                tool_call=ToolCall(
                    id="grafica-1",
                    name="mostrar_grafica",
                    arguments={
                        "tipo": "barras",
                        "titulo": "Antes y después",
                        "series": [
                            {
                                "nombre": "latencia",
                                "puntos": [
                                    {"etiqueta": "antes", "valor": 900},
                                    {"etiqueta": "después", "valor": 300},
                                ],
                            }
                        ],
                    },
                ),
            )
            return
        self.motivos.append(str(request.messages[-1].content))
        yield StreamChunk(type="text", text="La latencia bajó de 900 ms a 300 ms.")


def _agente(tmp_path: Path) -> WorkersIDEAgent:
    class _Workspaces:
        def root(self, workspace_id: str) -> Path:
            return tmp_path

    class _Files:
        pass

    return WorkersIDEAgent(_Workspaces(), _Files())


class _Escritor:
    """Doble de `EventWriter` que respeta el contrato (acepta `presentation`)."""

    def __init__(self) -> None:
        self.eventos: list[tuple[str, str, list[dict[str, Any]] | None]] = []

    def __call__(
        self,
        event_type: str,
        text: str,
        *,
        presentation: list[dict[str, Any]] | None = None,
    ) -> None:
        self.eventos.append((event_type, text, presentation))


@pytest.mark.asyncio
async def test_el_bloque_llega_entero_al_evento_del_turno(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    provider = _ProviderTabla(MODELO_IDE_POR_DEFECTO)
    monkeypatch.setattr(agent_module, "WorkersAIProvider", lambda model: provider)
    escritor = _Escritor()

    await _agente(tmp_path).run(
        workspace_id="workspace",
        prompt="Compara el costo de los proveedores.",
        write_event=escritor,
        cancelled=lambda: False,
    )

    (evento,) = [item for item in escritor.eventos if item[0] == "blocks"]
    _, texto, presentation = evento
    assert presentation is not None
    (bloque,) = presentation
    assert bloque["type"] == "table"
    assert bloque["rows"][0] == {"proveedor": "Workers AI", "costo": "0.11"}
    # El texto del evento nunca va vacío: es lo que leen el /export, el
    # historial reinyectado y los clientes que aún no dibujan bloques.
    assert "Workers AI" in texto

    resultado_tool = json.loads(str(provider.requests[1].messages[-1].content[0]["content"]))
    assert resultado_tool["ok"] is True
    assert "No repitas estos datos en texto" in resultado_tool["aviso"]


@pytest.mark.asyncio
async def test_una_grafica_degenerada_no_escribe_nada_y_le_explica_al_modelo(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    provider = _ProviderGraficaDegenerada(MODELO_IDE_POR_DEFECTO)
    monkeypatch.setattr(agent_module, "WorkersAIProvider", lambda model: provider)
    escritor = _Escritor()

    await _agente(tmp_path).run(
        workspace_id="workspace",
        prompt="Grafica la latencia antes y después.",
        write_event=escritor,
        cancelled=lambda: False,
    )

    assert not [item for item in escritor.eventos if item[0] == "blocks"]
    assert "dos puntos son una diferencia" in provider.motivos[0]
    assert "mostrar_tabla" in provider.motivos[0]
    assert any(tipo == "assistant_final" for tipo, _, _ in escritor.eventos)


@pytest.mark.asyncio
async def test_el_bloque_sobrevive_al_jsonl_de_la_sesion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """De la llamada del modelo al evento que el teléfono lee, sin perder nada."""
    proyecto = tmp_path / "proyecto"
    proyecto.mkdir()
    estado = tmp_path / "ide"
    manager = SessionManager(estado, WorkspaceStore(estado))
    workspace = manager.workspaces.authorize(str(proyecto))

    provider = _ProviderTabla(MODELO_IDE_POR_DEFECTO)
    monkeypatch.setattr(agent_module, "WorkersAIProvider", lambda model: provider)

    sesion = manager.start_agent(workspace["id"], "Compara el costo de los proveedores.")
    session_id = sesion["session"]["id"]
    for _ in range(200):
        estado_actual = manager.read(session_id, "agent", 0)
        if estado_actual["session"]["status"] != "running":
            break
        await asyncio.sleep(0.02)

    # El canal es la ÚNICA puerta: los demás eventos del turno (status, tool,
    # assistant_final...) siguen siendo texto pelado, sin `presentation`.
    otros = [item for item in estado_actual["events"] if item["type"] != "blocks"]
    assert otros and not any("presentation" in item for item in otros)

    eventos = [item for item in estado_actual["events"] if item["type"] == "blocks"]
    assert len(eventos) == 1
    bloque = eventos[0]["presentation"][0]
    assert bloque["type"] == "table"
    assert bloque["columns"][1]["align"] == "right"
    assert bloque["rows"][2] == {"proveedor": "Tercero", "costo": "3.00"}

    # Y sobrevive al disco, que es de donde se rehidrata al reabrir la app.
    jsonl = (estado / "ide-session-events" / f"{session_id}.jsonl").read_text("utf-8")
    persistidos = [json.loads(fila) for fila in jsonl.splitlines()]
    guardado = [fila for fila in persistidos if fila.get("type") == "blocks"]
    assert guardado[0]["presentation"] == eventos[0]["presentation"]


# ---------------------------------------------------------------------------
# Las superficies que solo leen texto
# ---------------------------------------------------------------------------


def test_el_export_a_markdown_conserva_la_tabla_como_tabla():
    """El respaldo ya tiene forma de tabla de Markdown, y el export ES Markdown."""
    from edecan_companion.ide_sesion_extras import exportar_markdown

    bloque, _ = construir_bloque("mostrar_tabla", TABLA_OK)
    markdown = exportar_markdown(
        {"title": "Sesión", "kind": "agent"},
        [
            {"cursor": 1, "type": "user", "text": "Compara los proveedores."},
            {"cursor": 2, "type": "blocks", "text": bloque["fallback_text"]},
        ],
    )

    assert "**Edecán:**\n\nCosto por proveedor" in markdown
    assert "Proveedor | USD / 1M" in markdown
    assert "--- | ---" in markdown


@pytest.mark.asyncio
async def test_el_mensaje_siguiente_recuerda_la_tabla_que_la_persona_esta_viendo(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Sin esto, un "ordénala por costo" le llega a un modelo que no sabe qué
    mostró y tendría que volver a medirlo todo."""
    proyecto = tmp_path / "proyecto"
    proyecto.mkdir()
    estado = tmp_path / "ide"
    manager = SessionManager(estado, WorkspaceStore(estado))
    workspace = manager.workspaces.authorize(str(proyecto))

    prompts: list[str] = []

    async def fake_run(_self, **kwargs):
        prompts.append(kwargs["prompt"])
        if len(prompts) == 1:
            bloque, _ = construir_bloque("mostrar_tabla", TABLA_OK)
            kwargs["write_event"]("blocks", bloque["fallback_text"], presentation=[bloque])
        kwargs["write_event"]("assistant_final", "Listo.")

    monkeypatch.setattr("edecan_companion.ide_workers_agent.WorkersIDEAgent.run", fake_run)

    primera = manager.start_agent(workspace["id"], "Compara costos", conversation_id="conv-1")
    for _ in range(200):
        if manager.read(primera["session"]["id"], "agent", 0)["session"]["status"] != "running":
            break
        await asyncio.sleep(0.02)

    manager.start_agent(workspace["id"], "Ordénala por costo", conversation_id="conv-1")
    for _ in range(200):
        if manager.read(primera["session"]["id"], "agent", 0)["session"]["status"] != "running":
            break
        await asyncio.sleep(0.02)

    assert len(prompts) == 2
    assert "Workers AI" in prompts[1]
    assert "0.11" in prompts[1]


# ---------------------------------------------------------------------------
# Espejo con el contrato del servidor
# ---------------------------------------------------------------------------


def test_el_espejo_con_edecan_schemas_no_se_desincronizo():
    """El companion no depende de `edecan_schemas` a propósito (se instala solo
    en la máquina de la persona), así que la misma regla vive dos veces. Este
    test es el que impide que se separen: corre en el monorepo, se salta en
    una instalación suelta del companion.
    """
    schemas = pytest.importorskip("edecan_schemas.ide_blocks")
    from edecan_companion import ide_bloques

    for nombre in (
        "MAX_TABLE_COLUMNS",
        "MAX_TABLE_ROWS",
        "MAX_TABLE_CELL_CHARS",
        "MAX_CHART_SERIES",
        "MAX_CHART_POINTS",
        "MIN_SINGLE_SERIES_POINTS",
        "MAX_BLOCKS_PER_EVENT",
    ):
        assert getattr(ide_bloques, nombre) == getattr(schemas, nombre), nombre

    casos = [
        ([{"name": "a", "points": [{"label": "x", "value": 1}, {"label": "y", "value": 2}]}], True),
        (
            [
                {
                    "name": "a",
                    "points": [
                        {"label": "x", "value": 1},
                        {"label": "y", "value": 2},
                        {"label": "z", "value": 3},
                    ],
                }
            ],
            False,
        ),
        (
            [
                {
                    "name": "a",
                    "points": [
                        {"label": "x", "value": 5},
                        {"label": "y", "value": 5},
                        {"label": "z", "value": 5},
                    ],
                }
            ],
            True,
        ),
        (
            [
                {"name": "a", "points": [{"label": "x", "value": 1}, {"label": "y", "value": 2}]},
                {"name": "b", "points": [{"label": "x", "value": 3}, {"label": "y", "value": 4}]},
            ],
            False,
        ),
        (
            [
                {"name": "a", "points": [{"label": "x", "value": 1}, {"label": "y", "value": 2}]},
                {"name": "b", "points": [{"label": "p", "value": 3}, {"label": "q", "value": 4}]},
            ],
            True,
        ),
    ]
    for series, esperado_degenerado in casos:
        propio = ide_bloques.motivo_grafica_degenerada(series)
        ajeno = schemas.motivo_grafica_degenerada(
            [schemas.ChartSeries.model_validate(serie) for serie in series]
        )
        assert (propio is not None) is esperado_degenerado, series
        assert (ajeno is not None) is esperado_degenerado, series


def test_lo_que_construye_el_companion_valida_contra_el_contrato_del_servidor():
    schemas = pytest.importorskip("edecan_schemas.ide_blocks")

    for nombre, args in (("mostrar_tabla", TABLA_OK), ("mostrar_grafica", GRAFICA_OK)):
        bloque, _ = construir_bloque(nombre, args)
        validado = schemas.IDEBlockAdapter.validate_python(bloque)
        assert validado.model_dump(mode="json") == bloque


def test_el_servidor_deja_pasar_entero_lo_que_el_companion_emite():
    schemas = pytest.importorskip("edecan_schemas.ide_blocks")

    bloque, _ = construir_bloque("mostrar_tabla", TABLA_OK)
    (validado,) = schemas.ide_blocks_from_presentation([bloque])

    assert validado.model_dump(mode="json") == bloque
