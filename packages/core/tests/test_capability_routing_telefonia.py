"""Las tres herramientas de telefonía se ofrecen JUNTAS o no se ofrecen.

Regresión de un fallo real: con 111 herramientas registradas, el selector ofrecía solo ~9 por
turno. Para "llama al +1... usa el agente la asistente" entraba `llamar_contacto` pero NO
`listar_agentes_llamadas`. El modelo hacía lo correcto -- verificar que el agente existe antes
de marcar -- recibía "herramienta desconocida", se confundía, agotaba sus ciclos y el turno
moría sin llamar ni responder: en la app el mensaje aparecía y se borraba solo.

Ofrecer "marcar" sin ofrecer "consultar a quién marcar" es un catálogo incoherente.
"""

from __future__ import annotations

import pytest
from edecan_core.capability_routing import select_tool_specs
from edecan_schemas import ToolSpec

TELEFONIA = frozenset(
    {"configurar_agente_llamadas", "listar_agentes_llamadas", "llamar_contacto"}
)


def _catalogo_grande() -> list[ToolSpec]:
    """Catálogo por encima del umbral que activa la selección por subconjunto.

    Por debajo, `select_tool_specs` devuelve todo y el test no probaría nada.
    """
    relleno = [
        ToolSpec(
            name=f"herramienta_{i}",
            description=f"Herramienta de relleno {i}",
            input_schema={},
        )
        for i in range(60)
    ]
    telefonia = [
        ToolSpec(name=n, description=f"Herramienta de llamadas: {n}", input_schema={})
        for n in sorted(TELEFONIA)
    ]
    return [*relleno, *telefonia]


@pytest.mark.parametrize(
    "texto",
    [
        "Llama al +10005550100. Usa el agente la asistente.",
        "llama a mi mamá",
        "hazme una llamada telefónica a soporte",  # con acento: el normalizador lo resuelve
        "marca al banco",
        "quiero hacer una llamada",
    ],
)
def test_pedir_una_llamada_ofrece_las_tres(texto: str) -> None:
    ofrecidas = {spec.name for spec in select_tool_specs(_catalogo_grande(), texto)}

    assert TELEFONIA <= ofrecidas, f"faltaron: {sorted(TELEFONIA - ofrecidas)}"


@pytest.mark.parametrize(
    "texto",
    [
        "escribe un post de LinkedIn sobre tecnología",
        "cuánto gasté este mes",
    ],
)
def test_un_turno_ajeno_no_arrastra_las_de_telefonia(texto: str) -> None:
    # El grupo no debe ensuciar turnos que no tienen nada que ver: cada herramienta de más
    # es contexto que el modelo tiene que descartar.
    ofrecidas = {spec.name for spec in select_tool_specs(_catalogo_grande(), texto)}

    assert not (TELEFONIA & ofrecidas)
