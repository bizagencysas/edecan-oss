"""`RestrictedRegistry` — defensa en profundidad para los sub-agentes de una
misión (`ARCHITECTURE.md` §10.7, `ROADMAP_V2.md` §7.9; evolución del gate en
`WP-V4-05`).

Envuelve el `ToolRegistry` COMPLETO del proceso (el mismo que usa el agente
principal, poblado por `ToolRegistry.load_entry_points`) y lo recorta, para
UN paso de UNA misión, a la intersección `allowed_tools ∩ tools visibles` del
perfil que ejecuta ese paso (`profiles.AgentProfile.allowed_tools`) — "tools
visibles" excluye SIEMPRE las `dangerous=True` salvo que el perfil declare
`permite_dangerous_con_confirmacion=True` (`profiles.AgentProfile`, ver su
docstring), en cuyo caso las tools `dangerous` de `allowed_tools` SÍ se
exponen.

## De "ocultar" a "pausar + aprobación humana explícita"

Antes de `WP-V4-05` esta clase era la única forma de tratar una tool
`dangerous`: quedaba invisible para SIEMPRE, sin importar `allowed_tools` —
"defensa en profundidad" contra un error humano al declarar el perfil (ver
`profiles.py`), pero también un techo duro: ningún perfil podía usar una
tool `dangerous` NUNCA, ni siquiera con aprobación humana en el medio.

`permite_dangerous_con_confirmacion` no baja esa guardia, la traslada a un
punto de control distinto y más fuerte — un humano explícito, no un booleano
estático:

1. Con el flag en `True`, `.get()`/`.specs()` SÍ exponen la tool `dangerous`
   (siempre y solo si además está en `allowed_tools` — el flag nunca "regala"
   una tool fuera de la lista del perfil).
2. El sub-agente puede pedirla, pero `edecan_core.agent.Agent.run_turn`
   (`ARCHITECTURE.md` §10.7) la intercepta ANTES de ejecutarla: si
   `tool.dangerous and call.id not in approved_tool_calls`, emite
   `confirmation_required` y DETIENE el turno sin correr nada — el `Agent`
   ya hacía esto para cualquier tool dangerous que SÍ pudiera ver; antes de
   este WP, `RestrictedRegistry` nunca dejaba llegar una tool dangerous hasta
   ahí, así que ese camino jamás se ejercitaba desde un perfil de misión.
3. `edecan_agents.orchestrator.Orchestrator.run` traduce ese evento en
   `waiting_confirmation` (misión Y paso), persiste `{id, name, args}`
   pendientes, y RETORNA sin ejecutar nada más (ver docstring de
   `orchestrator.py`, sección "Confirmación pendiente").
4. Solo cuando un humano aprueba explícito (`POST /v1/missions/{id}/confirm`,
   nunca automático) `Orchestrator._run_resumed_step` ejecuta la tool
   aprobada — contra el `ToolRegistry` COMPLETO, no el recortado de este
   perfil, con el mismo criterio que ya vale para `dangerous` en el chat
   normal: la aprobación la dio un humano, no el perfil.

Es decir: el guardrail sigue siendo el mismo principio ("nada peligroso se
auto-aprueba", `ROADMAP_V2.md` §7.9) pero ahora tiene DOS modos honestos en
vez de uno solo:

- `permite_dangerous_con_confirmacion=False` (default — comportamiento
  IDÉNTICO al de antes de este campo): la tool dangerous queda invisible,
  como si no existiera. Sigue siendo el modo correcto para un perfil que NO
  tiene ninguna tool `dangerous` en su `allowed_tools` (p. ej. los tres
  perfiles P0: `research`/`data_analyst`/`content`) — no hay nada que pausar
  porque no hay nada peligroso que pedir.
- `permite_dangerous_con_confirmacion=True`: la tool existe para el
  sub-agente, pero CUALQUIER intento de usarla pausa la misión entera hasta
  que un humano la apruebe explícitamente. Nunca es "ejecución directa
  silenciosa" — eso lo sigue impidiendo `Agent.run_turn` sin importar este
  flag.

Con cualquiera de los dos modos, esta clase sigue siendo "defensa en
profundidad", no la única barrera: `profiles.py` documenta, perfil por
perfil, cuáles de sus `allowed_tools` son `dangerous=True` hoy — un error
humano futuro (agregar una tool dangerous a `allowed_tools` sin marcar el
flag) deja la tool invisible (falla cerrado, modo `False`), nunca ejecutándose
sin aprobación.
"""

from __future__ import annotations

from typing import Any


class RestrictedRegistry:
    """Envoltorio de sólo-lectura sobre un `ToolRegistry` (duck-typed: solo
    necesita `.get(name)` y `.specs(flags)`, la misma superficie que
    `Agent.run_turn` consume de `edecan_core.tools.registry.ToolRegistry`)."""

    def __init__(
        self,
        wrapped: Any,
        allowed_tools: frozenset[str],
        *,
        permite_dangerous_con_confirmacion: bool = False,
    ) -> None:
        self._wrapped = wrapped
        self._allowed = frozenset(allowed_tools)
        self._permite_dangerous_con_confirmacion = permite_dangerous_con_confirmacion

    def get(self, name: str) -> Any | None:
        """`None` si `name` no está en `allowed_tools`, si el registro
        envuelto no la conoce (p. ej. el paquete que la trae todavía no
        aterrizó), o si es `dangerous=True` y `permite_dangerous_con_confirmacion`
        es `False` (el default) — en los tres casos el efecto es el mismo que
        "herramienta desconocida" para `Agent.run_turn`: no la ejecuta y
        sigue con un mensaje de error hacia el modelo, sin detener el turno
        completo (ARCHITECTURE.md §10.7).

        Con `permite_dangerous_con_confirmacion=True`, una tool `dangerous`
        que SÍ está en `allowed_tools` se devuelve tal cual — no se ejecuta
        acá ni en ningún otro punto de esta clase: sigue siendo
        `Agent.run_turn` quien la detiene con `confirmation_required` si no
        viene pre-aprobada (ver docstring del módulo)."""
        if name not in self._allowed:
            return None
        tool = self._wrapped.get(name)
        if tool is None:
            return None
        if getattr(tool, "dangerous", False) and not self._permite_dangerous_con_confirmacion:
            return None
        return tool

    def specs(self, flags: dict[str, Any]) -> list[Any]:
        """`ToolSpec` del registro envuelto, ya filtrados por `flags` (delega
        en `.specs()` del registro completo, que aplica `requires_flags`), y
        recortados otra vez por `.get()` — así el modelo del sub-agente ni
        siquiera SABE que una tool fuera de su perfil (o dangerous sin
        `permite_dangerous_con_confirmacion`) existe."""
        return [spec for spec in self._wrapped.specs(flags) if self.get(spec.name) is not None]
