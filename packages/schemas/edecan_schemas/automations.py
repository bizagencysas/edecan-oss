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

**Segunda variante (paridad Aria, 30-jul-2026): `CreateLinkedinPostAccion`.**
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
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter


class ScheduleTrigger(BaseModel):
    """Disparo por agenda: `rrule` sigue RFC 5545 (p. ej. `"FREQ=DAILY;BYHOUR=9"`)."""

    kind: Literal["schedule"] = "schedule"
    rrule: str


class WebhookTrigger(BaseModel):
    """Disparo por webhook entrante.

    `hook_secret` es el secreto que el emisor externo debe presentar (p. ej.
    header `X-Hook-Secret` o query param, a decisión de WP-V2-07) para que
    `POST /v1/hooks/{automation_id}` acepte la llamada — el `id` de la
    automatización por sí solo NO autentica nada (es visible/adivinable).
    """

    kind: Literal["webhook"] = "webhook"
    hook_secret: str


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
    una organización configurada por el tenant, p. ej. `"acme"`) — `None`
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


AccionDef = Annotated[
    AgentInstructionAccion | CreateLinkedinPostAccion, Field(discriminator="kind")
]
"""Unión discriminada por `kind`: `"agent_instruction"` | `"create_linkedin_post"`
(ver docstring del módulo, "Segunda variante")."""

AccionDefAdapter: TypeAdapter[AccionDef] = TypeAdapter(AccionDef)
