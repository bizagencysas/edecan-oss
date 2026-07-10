"""Contrato de herramienta del agente: `Tool`, `ToolContext`, `ToolResult`.

Firmas EXACTAS pinned en `ARCHITECTURE.md` §10.7 — cualquier herramienta
concreta (`edecan_toolkit`, `edecan_premium`) y el propio `edecan_core.agent`
se escriben contra este contrato al pie de la letra.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any
from uuid import UUID


@dataclass
class ToolResult:
    """Resultado de ejecutar una `Tool.run`.

    `content` es el texto que se le devuelve al modelo (como turno
    `role="tool"`, ver `edecan_core.agent`). `data` es opcional y sirve para
    que quien orquesta (p. ej. `edecan_api`) adjunte información estructurada
    sin tener que parsear `content`. `requires_confirmation` queda disponible
    para que una `Tool` señale, desde dentro de `run`, que el resultado en sí
    necesita confirmación adicional (distinto del gate `Tool.dangerous`, que
    lo exige *antes* de ejecutar — ver `Agent.run_turn`).
    """

    content: str
    data: dict[str, Any] | None = None
    requires_confirmation: bool = False


@dataclass
class ToolContext:
    """Contexto de ejecución que recibe cada `Tool.run` (y que arma `Agent.run_turn`).

    `session`/`settings`/`llm`/`vault` son `Any` a propósito: `edecan_core` no
    depende de `edecan_db` ni de `edecan_llm` (ver README de este paquete) —
    cada `Tool` concreta (en `edecan_toolkit`/`premium/`) sí declara esas
    dependencias y sabe con qué tipo real está tratando en cada campo:

    - `session`: la `AsyncSession` de `edecan_db.session.get_session` (SQL
      parametrizado, RLS ya activado para `tenant_id`).
    - `settings`: la configuración de la app (`edecan_api.config.Settings` o
      equivalente) — típicamente se lee con `getattr(settings, "X", default)`.
    - `llm`: acceso opcional a un `LLMRouter` (o similar) para herramientas
      que necesiten completions propias (p. ej. generación de contenido).
    - `vault`: el `TokenVault` para leer credenciales de conectores/Twilio.

    `extras` es el cajón de mano: `edecan_core.agent.Agent.run_turn` lee de
    ahí `"memory_store"` (un `MemoryStore`, opcional) y
    `"approved_tool_calls"` (un `set[str]` de `tool_call_id` ya confirmados
    por el usuario para herramientas `dangerous`). `edecan_api` inyecta
    además `"companion"` (ver ARCHITECTURE.md §10.7), `"memory_embedder"`
    (un `Embedder` opcional que usa `ConsultarDocumentosTool` de
    `edecan_toolkit.documentos` para buscar por distancia coseno; ausente si
    el tenant no tiene un proveedor de embeddings real configurado),
    `"flags"` (el mismo `dict` de flags del plan del tenant que recibe
    `run_turn(flags=...)`, para que una `Tool` que llame a `ctx.llm.complete`
    por su cuenta —p. ej. `GenerarContenidoTool` en `edecan_toolkit.contenido`—
    respete el mismo downgrade de modelo por plan) y cualquier otra clave que
    una `Tool` concreta necesite.
    """

    tenant_id: UUID
    user_id: UUID
    session: Any
    settings: Any
    llm: Any
    vault: Any
    extras: dict[str, Any]


class Tool(ABC):
    """Una herramienta que el agente puede invocar durante `Agent.run_turn`.

    Las subclases fijan `name`/`description`/`input_schema` (JSON Schema del
    argumento `args` que recibe `run`) como atributos de clase, y
    opcionalmente:

    - `requires_flags`: flags de plan del tenant (ver `edecan_schemas.plans`)
      que deben estar TODOS activos para que `ToolRegistry.specs()` ofrezca
      la herramienta al modelo. Vacío (default) = siempre disponible.
    - `dangerous`: si es `True`, `Agent.run_turn` exige una confirmación
      explícita del usuario (`tool_call_id` presente en
      `ctx.extras["approved_tool_calls"]`) antes de ejecutarla — si no está
      pre-aprobada, el turno se detiene y emite `confirmation_required` en
      vez de correr `run`.

    `register()` de `ToolRegistry` rechaza cualquier `Tool` cuyo `name`/
    `description` mencione la red social vetada (ARCHITECTURE.md §0.2).
    """

    name: str
    description: str
    input_schema: dict[str, Any]
    requires_flags: frozenset[str] = frozenset()
    dangerous: bool = False

    @abstractmethod
    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        """Ejecuta la herramienta con `args` y devuelve un `ToolResult`.

        `args` viene de lo que decidió el modelo (validado contra
        `input_schema` solo del lado del proveedor LLM, sin garantías
        fuertes) — cada `Tool` debe validar lo que le importe y devolver un
        `ToolResult` con `content` explicando el problema en vez de lanzar,
        cuando el error es "de negocio" (p. ej. falta un argumento). Las
        excepciones inesperadas sí puede lanzarlas: `Agent.run_turn` las
        atrapa y las convierte en un `ToolResult` de error sin tumbar el
        turno completo.
        """
        raise NotImplementedError
