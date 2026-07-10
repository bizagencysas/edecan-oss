"""WP-V6-02 — pin de paridad de flag/límite desde el lado del PAQUETE de
tools (`edecan_automations`), complementario al cruce completo tool<->router
en `apps/api/tests/test_v6_sweep_flags.py`.

`packages/automations/edecan_automations/tools.py` define SUS PROPIAS
constantes locales `FLAG_AUTOMATIONS_RULES`/`LIMIT_AUTOMATIONS_ACTIVE`
(strings, no importadas de `edecan_schemas.plans`) — mismo patrón deliberado
de desacoplamiento entre paquetes hermanos que `edecan_ads._FLAG_ADS`/
`edecan_browser._FLAG_BROWSER`/`edecan_voice._FLAG_VOICE_WEB`/
`edecan_travel._FLAG_TRAVEL` (ARCHITECTURE.md §10.1: paquetes hermanos no se
importan entre sí). Ese patrón es exactamente donde un desync de valores
podría colarse en silencio (alguien cambia `edecan_schemas.plans.
FLAG_AUTOMATIONS_RULES` y nadie actualiza la copia local) — `edecan_schemas`
SÍ es una dependencia legítima de este paquete (`pyproject.toml`, es el
paquete de esquemas base, no un "hermano" en el sentido de la regla de
arriba), así que este test la importa para comparar.
"""

from __future__ import annotations

from edecan_automations.tools import (
    FLAG_AUTOMATIONS_RULES as FLAG_LOCAL,
)
from edecan_automations.tools import (
    LIMIT_AUTOMATIONS_ACTIVE as LIMIT_LOCAL,
)
from edecan_automations.tools import GestionarAutomatizacionTool
from edecan_schemas.plans import FLAG_AUTOMATIONS_RULES, LIMIT_AUTOMATIONS_ACTIVE


def test_constantes_locales_coinciden_con_edecan_schemas_plans() -> None:
    """Si `edecan_schemas.plans.FLAG_AUTOMATIONS_RULES`/
    `LIMIT_AUTOMATIONS_ACTIVE` cambian de valor algún día y esta copia local
    no se actualiza junto con ellas, `requires_flags`/`_bajo_limite` de
    `GestionarAutomatizacionTool` quedarían gateando un flag/límite que ya
    no es el real del plan -- fail-closed en la práctica (el flag "nuevo"
    del catálogo simplemente nunca estaría `True` para nadie hasta que se
    sincronice), pero de todos modos un bug de producto real. Este test lo
    pinnea explícito."""
    assert FLAG_LOCAL == FLAG_AUTOMATIONS_RULES
    assert LIMIT_LOCAL == LIMIT_AUTOMATIONS_ACTIVE


def test_tool_requires_flags_usa_la_constante_local() -> None:
    assert GestionarAutomatizacionTool.requires_flags == frozenset({FLAG_LOCAL})
    # ... y por transitividad con el test de arriba, también la canónica:
    assert GestionarAutomatizacionTool.requires_flags == frozenset({FLAG_AUTOMATIONS_RULES})


def test_tool_es_dangerous_para_las_cuatro_acciones() -> None:
    """`dangerous=True` cubre TODA la tool (crear/listar/activar/desactivar),
    no solo crear/activar -- decisión documentada en el docstring de la
    clase (limitación de `Tool.dangerous` como atributo de clase, no por
    `accion`). Pin explícito: si algún día el framework soporta un
    `dangerous` por-acción y alguien lo aprovecha acá, este test obliga a
    revisar conscientemente esa migración en vez de que quede como un
    cambio de comportamiento silencioso."""
    assert GestionarAutomatizacionTool.dangerous is True
