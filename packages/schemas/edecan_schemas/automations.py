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


AccionDef = AgentInstructionAccion
"""Alias del contrato de acción vigente (única variante hoy, ver docstring
del módulo). NO es `Annotated[..., Field(discriminator=...)]` como
`TriggerDef`: Pydantic v2 requiere una unión real (2+ miembros) para eso."""

AccionDefAdapter: TypeAdapter[AccionDef] = TypeAdapter(AccionDef)
