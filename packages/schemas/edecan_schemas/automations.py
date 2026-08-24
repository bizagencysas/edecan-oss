"""Forma del JSON guardado en `automations.trigger`/`automations.accion`
(ROADMAP_V2.md §7.4, §7.7, dueño WP-V2-01; consumido por WP-V2-07).

`TriggerDef` es una unión discriminada por `kind` (mismo patrón que
`edecan_schemas.chat.AgentEvent`, §10.7): `"schedule"` (agenda por `rrule`,
RFC 5545 — `edecan_automations` la interpreta con `python-dateutil` para
calcular `automations.next_run_at`) o `"webhook"` (disparo entrante en
`POST /v1/hooks/{automation_id}`, autenticado con `hook_secret`, nunca el
`id` a secas — ver ROADMAP_V2.md §7.6, prefix público "secreto por
automatización"). `AccionDef` hoy solo declara la variante
`"agent_instruction"` (§7.7: `edecan_automations.gestionar_automatizacion` no
ofrece ninguna otra) — es un alias simple, no una unión discriminada, porque
Pydantic v2 exige al menos 2 miembros para `Field(discriminator=...)`; el
campo `kind` de `AgentInstructionAccion` ya queda pinned para que, el día que
exista una segunda variante, `AccionDef` pase a ser
`Annotated[VarianteA | VarianteB, Field(discriminator="kind")]` sin romper
filas ya guardadas (`kind` siempre estuvo en el JSON).

**Segunda variante (paridad REFERENCIA, 30-jul-2026): `CreateLinkedinPostAccion`.**
`AccionDef` YA es la unión discriminada que el párrafo anterior anticipaba.
Existe para las automatizaciones SEMBRADAS por el sistema (los 5 posts/día
de LinkedIn, ver `apps/local/edecan_local/linkedin_automations_seed.py`):
en vez de correr un turno de agente headless que TIENE que decidir con un
LLM qué tool llamar y con qué argumentos (`agent_instruction`, con el riesgo
de que el modelo elija mal el destino o se salte la imagen), esta variante
encola DIRECTO el job `create_linkedin_post`
(`apps/worker/edecan_worker/handlers/create_linkedin_post.py`) con los
parámetros ya fijos — cero LLM en el camino de DISPARO, el LLM solo entra
DENTRO del motor de contenido que ese job corre. Ver
`apps/worker/edecan_worker/handlers/run_automation.py`, sección "Delegación
directa a create_linkedin_post", para el porqué de esta elección sobre
`agent_instruction`. `seed_id` (opcional) es la clave de idempotencia que usa
el sembrador para no duplicar filas en cada arranque — nunca la usa el motor
de ejecución, es puramente informativa/administrativa.

**Condición opcional (PHASE2.md §60, §62).** Además del `trigger`/`accion`, una
automatización puede llevar un `condition` (columna top-level
`automations.condition`, migración `0039_automation_condition`) que filtra si
una corrida debida se ejecuta o se salta: "si ocurre X y además Y pero no Z".
`Condition` (una `ConditionClause` o una lista combinada con AND) describe esa
forma; `ConditionAdapter` la valida. `None`/ausente = corre siempre, el
comportamiento histórico exacto. `edecan_automations.engine.evaluate_condition`
la evalúa contra un contexto de runtime y NUNCA lanza.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, TypeAdapter

# ---------------------------------------------------------------------------
# Condición opcional (PHASE2.md §60, §62) — la forma de `automations.condition`
# ---------------------------------------------------------------------------

ConditionOp = Literal["eq", "neq", "gt", "gte", "lt", "lte", "contains", "exists"]
"""Operadores soportados por una condición de automatización (§60): comparación
(`eq`/`neq`), orden (`gt`/`gte`/`lt`/`lte`), pertenencia (`contains`) y
presencia no-nula (`exists`, que ignora `value`)."""


class ConditionClause(BaseModel):
    """Una comparación atómica `field op value`.

    `field` se resuelve contra el contexto de runtime que arma el motor (p. ej.
    `"last_result"`, `"failure_count"`, `"hour"`; admite ruta punteada como
    `"detalle.foo"`), `op` es uno de `ConditionOp` y `value` el valor esperado.
    `value` es `Any` porque una misma cláusula puede comparar enteros, strings,
    fechas o nada (para `exists`)."""

    field: str
    op: ConditionOp
    value: Any = None


Condition = ConditionClause | list[ConditionClause]
"""Una condición es UNA cláusula o una LISTA de cláusulas combinadas con AND
(todas deben cumplirse). Es la forma del JSON guardado en
`automations.condition` (columna top-level, migración `0039_automation_condition`).
`None`/ausente = sin condición = la automatización corre siempre (comportamiento
histórico exacto, backwards-compatible)."""

ConditionAdapter: TypeAdapter[Condition] = TypeAdapter(Condition)


class ScheduleTrigger(BaseModel):
    """Disparo por agenda: `rrule` sigue RFC 5545 (p. ej. `"FREQ=DAILY;BYHOUR=9"`)."""

    kind: Literal["schedule"] = "schedule"
    rrule: str
    # Condición opcional que filtra si el disparo procede (PHASE2 §60, ver
    # `Condition`). Opcional y backwards-compatible: un trigger sin `condition`
    # valida igual que siempre y dispara siempre.
    condition: Condition | None = None


class WebhookTrigger(BaseModel):
    """Disparo por webhook entrante.

    `hook_secret` es el secreto que el emisor externo debe presentar (p. ej.
    header `X-Hook-Secret` o query param, a decisión de WP-V2-07) para que
    `POST /v1/hooks/{automation_id}` acepte la llamada — el `id` de la
    automatización por sí solo NO autentica nada (es visible/adivinable).
    """

    kind: Literal["webhook"] = "webhook"
    hook_secret: str
    # Condición opcional que filtra si el disparo procede (PHASE2 §60, ver
    # `Condition`). Opcional y backwards-compatible (ver `ScheduleTrigger`).
    condition: Condition | None = None


TriggerDef = Annotated[ScheduleTrigger | WebhookTrigger, Field(discriminator="kind")]
"""Unión discriminada por `kind`: `"schedule"` | `"webhook"` (ROADMAP_V2.md §7.4)."""

TriggerDefAdapter: TypeAdapter[TriggerDef] = TypeAdapter(TriggerDef)


class AgentInstructionAccion(BaseModel):
    """Única variante pinned hoy (§7.7): corre `instruccion` en modo headless
    con el toolset seguro de la automatización, opcionalmente con un perfil
    de agente concreto (`agente`, keys de ROADMAP_V2.md §7.9; `None` = agente
    genérico headless de WP-V2-07)."""

    kind: Literal["agent_instruction"] = "agent_instruction"
    instruccion: str
    agente: str | None = None


class CreateLinkedinPostAccion(BaseModel):
    """Encola `create_linkedin_post` directo, sin turno de agente (ver
    docstring del módulo, "Segunda variante"). `destino` sigue la forma de
    `edecan_creative.marcas.is_valid_destination_id` (`"personal"` o el id de
    una organización configurada por el tenant, p. ej. `"organization"`) — `None`
    deja que el motor lo resuelva él mismo (arriesga preguntar si hay 2+
    destinos configurados; el sembrador SIEMPRE lo fija explícito para no
    depender de eso en un run headless, ver el módulo de sembrado). `tema`
    vacío dispara el tema/formato rotativo interno del motor, igual que
    dejarlo fuera del payload de `create_linkedin_post`."""

    kind: Literal["create_linkedin_post"] = "create_linkedin_post"
    destino: str | None = None
    tema: str | None = None
    con_imagen: bool = True
    seed_id: str | None = None


class GymCheckinAccion(BaseModel):
    """Tercera variante (feature gimnasio): dispara el check-in proactivo
    "¿Vas a ir al gym hoy?".

    Igual que `create_linkedin_post`, NO corre un turno de agente headless: el
    handler `run_gym_checkin` (`apps/worker/edecan_worker/handlers/run_gym_checkin.py`)
    publica la card de la pregunta en el chat principal y envía un push con
    `category="GYM_CHECKIN"`, de forma determinista (cero LLM en el camino de
    DISPARO). `objetivo` (opcional) fija el objetivo del plan que se generará
    si el usuario responde "sí"; `None` deja que `edecan_gym.generar_plan` lo
    derive del historial."""

    kind: Literal["gym_checkin"] = "gym_checkin"
    objetivo: str | None = None
    seed_id: str | None = None


AccionDef = Annotated[
    AgentInstructionAccion | CreateLinkedinPostAccion | GymCheckinAccion,
    Field(discriminator="kind"),
]
"""Unión discriminada por `kind`: `"agent_instruction"` | `"create_linkedin_post"`
| `"gym_checkin"` (ver docstring del módulo, "Segunda variante" y la docstring
de `GymCheckinAccion`)."""

AccionDefAdapter: TypeAdapter[AccionDef] = TypeAdapter(AccionDef)
