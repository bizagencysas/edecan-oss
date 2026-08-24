"""Contrato de herramienta del agente: `Tool`, `ToolContext`, `ToolResult`.

Firmas EXACTAS pinned en `ARCHITECTURE.md` §10.7 — cualquier herramienta
concreta (`edecan_toolkit`, `edecan_premium`) y el propio `edecan_core.agent`
se escriben contra este contrato al pie de la letra.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from ..veracidad import InfoFidelidad

ENV_SIN_CONFIRMACIONES = "EDECAN_SIN_CONFIRMACIONES"


def confirmaciones_desactivadas() -> bool:
    """Si este despliegue renunció por completo al gate `Tool.dangerous`.

    Por defecto NO: quien recién instala Edecán no espera que un modelo publique,
    envíe o marque un teléfono sin un freno visible, y para esa persona la tarjeta
    de confirmación es lo que hace la diferencia entre una sorpresa y una decisión.

    Pero un asistente PERSONAL es otro trato. Cuando el único usuario es el dueño y
    acaba de pedir la acción con todas sus letras, la tarjeta le pregunta justo lo
    que él mismo dijo hace un segundo: deja de ser un freno y pasa a ser un peaje.
    Peor aún, un turno detenido esperando una aprobación que la app no alcanzó a
    pintar se ve exactamente igual que un cuelgue — que fue el caso real que llevó
    a este interruptor.

    `EDECAN_SIN_CONFIRMACIONES=1` lo apaga TODO de una vez, no herramienta por
    herramienta: media docena de interruptores sueltos es la forma segura de dejar
    uno prendido sin darse cuenta y volver a ver el mismo cuelgue.

    Lo que esto NO afloja: los `requires_flags` del plan, la validación de argumentos
    de cada herramienta, ni los permisos del sistema operativo. Solo se salta la
    tarjeta de "¿confirmas?" -- no convierte una acción imposible en posible.
    """
    valor = os.getenv(ENV_SIN_CONFIRMACIONES)
    if valor is None:
        return False
    return valor.strip().lower() not in {"0", "false", "no", "off", ""}


@dataclass
class ToolResult:
    """Resultado de ejecutar una `Tool.run`.

    `content` es el texto que se le devuelve al modelo (como turno
    `role="tool"`, ver `edecan_core.agent`). `data` es opcional y sirve para
    que quien orquesta (p. ej. `edecan_api`) adjunte información estructurada
    sin tener que parsear `content`. `requires_confirmation` queda disponible
    para que una `Tool` señale, desde dentro de `run`, que el resultado en sí
    necesita confirmación adicional (distinto del gate `Tool.dangerous`, que
    lo exige *antes* de ejecutar — ver `Agent.run_turn`). `presentation` es
    el único canal deliberado para bloques ricos tipados: `data` arbitrario,
    incluido el que venga de MCPs, nunca puede acuñar UI por accidente.

    `fidelidad` es el contrato de veracidad (`edecan_core.veracidad`): cuando
    esta `Tool` usó un `ProveedorDeclarado` (real o simulado) para construir
    `content`/`data`, mete aquí `provider.info_fidelidad()`. `Agent.run_turn`
    lo lee UNA vez y lo reparte a los dos destinos que importan — el turno
    `role="tool"` que ve el modelo, y `ToolEndEvent.fidelidad` que llega a la
    app del dueño por el stream. `None` (default) para la mayoría de tools,
    que no dependen de un proveedor externo real/simulado.

    `is_error` marca que esta `run()` NO produjo el resultado pedido — falta
    un argumento obligatorio, un símbolo no reconocido, etc. — a diferencia
    del contenido plausible que antes se devolvía igual (p. ej. `buscar_web`
    con `consulta` vacía respondía "Dime qué quieres que busque en la web.",
    un texto que un modelo puede confundir con una respuesta ya dada y
    terminar reintentando la misma llamada vacía varias veces en el mismo
    turno). `Agent.run_turn` lo traduce al `is_error: true` del bloque
    `tool_result` (Anthropic lo lee de forma nativa como señal de fallo;
    en proveedores que no soportan ese campo, el propio `content` ya debe
    leerse como un error accionable, no solo como texto). Default `False`
    para no cambiar el comportamiento de ninguna tool existente.
    """

    content: str
    data: dict[str, Any] | None = None
    requires_confirmation: bool = False
    presentation: list[dict[str, Any]] | None = None
    fidelidad: InfoFidelidad | None = None
    is_error: bool = False
    citations: list[dict[str, Any]] = field(default_factory=list)
    """Citas/ fuentes estructuradas que esta tool generó (§18, §100).

    Cada entrada es un dict con ``id``, ``title``, ``url``, ``source`` (dominio),
    opcionalmente ``author``, ``date``, ``excerpt``, ``retrieved_at``.
    ``Agent.run_turn`` las proyecta a ``ToolEndEvent.citations`` para que
    el cliente las muestre como chips tocables debajo de la respuesta.
    """


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

    `register()` de `ToolRegistry` no aplica políticas de proveedor: cada
    herramienta valida su vía oficial, permisos y confirmaciones.
    """

    name: str
    description: str
    input_schema: dict[str, Any]
    requires_flags: frozenset[str] = frozenset()
    dangerous: bool = False

    category: str = "utility"
    """Categoría semántica para el router de tools (§6): read, write,
    external_comm, destructive, admin, utility, research, creative,
    vision, voice, code, browser."""

    risk_level: str = "low"
    """Nivel de riesgo: none, low, medium, high, critical (§6, §41)."""

    latency_class: str = "interactive"
    """Clase de latencia: instant, interactive, slow, background (§74)."""

    cost_class: str = "free"
    """Clase de costo: free, cheap, moderate, expensive (§121)."""

    timeout_seconds: float = 60.0
    """Timeout de ejecución en segundos (§62)."""

    retry_policy: str = "none"
    """Política de retry: none, retry_safe, retry_idempotent (§62)."""

    idempotent: bool = False
    """Si la tool es idempotente y segura para retry (§64)."""

    inverse: str | None = None
    """Descripción human-readable de cómo revertir la última acción de esta tool.

    Solo METADATA (PHASE2.md §64): cuando está presente, `Agent` registra un
    `ActionEffect` (`edecan_core.action_ledger`) con `reversible=True` tras una
    ejecución exitosa, para que el agente pueda responder "¿qué cambiaste?"
    (§69) y ofrecer "deshacer". La reversión REAL la implementa cada tool que
    declara este atributo — aquí solo se describe cómo. P. ej.:

        inverse = "restaurar el archivo editado desde el backup X"

    `None` (default) = la acción no es reversible y no se registra en el
    ledger. No rompe ninguna tool existente: es un atributo de clase opcional
    con default que las subclases actuales simplemente no sobrescriben.
    """

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
