"""Contrato de veracidad: que un proveedor simulado no pueda pasar por real.

## Por qué existe este módulo

Medido en la instalación viva de Alex: `StubTTS` (`edecan_voice.stubs`)
devuelve 0.5 s de silencio absoluto con HTTP 200 y la tool le dice al modelo
"puedes escucharlo desde tus archivos" — la clase SABE que es un stub
(`isinstance(provider, StubTTS)` se calcula un par de líneas más abajo, en
`edecan_voice.tools.SintetizarVozTool.run`) y ese booleano se usa solo para
elegir la extensión del archivo, nunca para avisar. El mismo patrón se repitió
en `StubQuotes`, `StubImageProvider`, `StubSearch`, `StubTravelProvider`... —
seis familias de proveedores, cada una reinventando su propia detección ad
hoc, ninguna obligada a declarar nada.

Este módulo es la RAÍZ del arreglo: un contrato que hace que declarar sea
obligatorio en vez de opcional, para que el proveedor número siete que
alguien agregue el mes que viene no pueda repetir el patrón.

## Las tres piezas

1. `Fidelidad` / `InfoFidelidad`: el vocabulario común — qué es un proveedor
   (real o simulado), de dónde sale el dato, y qué falta para que sea real.
2. `ProveedorDeclarado`: la clase base que TODO proveedor (real o stub) debe
   heredar. `__init_subclass__` revienta con `TypeError` al definir la clase
   (import time, no en un test que alguien podría no correr) si falta
   declarar `familia`/`fidelidad`/`fuente`, o si es SIMULADO sin decir qué
   falta (`motivo_simulado`).
3. El punto de entrega: cada `Tool` que use un `ProveedorDeclarado` mete
   `provider.info_fidelidad()` en `ToolResult.fidelidad` (`edecan_core.tools.
   base.ToolResult`). `Agent.run_turn` (`edecan_core.agent`, el único `yield`
   que entrega cada resultado de tool) lee ese campo UNA vez y lo reparte a
   los dos destinos que de verdad importan: el turno `role="tool"` que ve el
   modelo (para que no afirme que algo simulado ya está disponible) y
   `ToolEndEvent.fidelidad` que viaja por el stream SSE hasta la app del
   dueño. Un log no cuenta como ninguno de los dos: en la Mac los logs del
   sidecar mueren solos.
"""

from __future__ import annotations

from abc import ABC
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar


class Fidelidad(StrEnum):
    """Qué tan real es lo que entrega un proveedor concreto.

    Deliberadamente solo dos valores declarables. NO existe un tercer valor
    "no sé" a propósito: `edecan_schemas.chat.*.source_mode` ya tiene
    `"unknown"` con default implícito, y ese default es exactamente el hueco
    que dejó pasar el silencio de 0.5s sin avisar — "no sé" se pintaba igual
    de neutro que "sí es real". Quien no declara, no compila (ver
    `ProveedorDeclarado.__init_subclass__`): así "no sé" deja de ser un
    estado posible en tiempo de ejecución.
    """

    REAL = "live"
    SIMULADO = "demo"


@dataclass(frozen=True)
class InfoFidelidad:
    """Lo que un proveedor concreto declara sobre el resultado que acaba de
    producir, listo para viajar tal cual hasta `ToolResult.fidelidad` y de
    ahí a los dos destinos de `edecan_core.agent.Agent.run_turn`."""

    familia: str
    """Qué tipo de proveedor es: "tts", "stt", "cotizaciones", "imagenes"..."""

    fidelidad: Fidelidad
    fuente: str
    """De dónde sale el dato cuando es REAL (p. ej. "ElevenLabs", "Deepgram");
    o el nombre de qué se está simulando cuando es SIMULADO (p. ej.
    "silencio offline")."""

    motivo_simulado: str | None = None
    """Obligatorio cuando `fidelidad is Fidelidad.SIMULADO`
    (`ProveedorDeclarado.__init_subclass__` lo exige): qué falta para que
    esto sea real y cómo resolverlo — nunca solo "es un stub"."""

    def aviso_para_el_modelo(self) -> str:
        """Texto a anteponer al turno `role="tool"` que ve el modelo.

        Cadena vacía cuando `fidelidad is REAL`: un proveedor real no
        necesita ningún prefijo, y `Agent.run_turn` usa esa cadena vacía como
        señal de "no antepongas nada" (ver `agent.py`, el `yield` que
        construye el `tool_result` block).
        """
        if self.fidelidad is Fidelidad.REAL:
            return ""
        return (
            f"[FUENTE SIMULADA — {self.familia}={self.fuente}: {self.motivo_simulado}. "
            "No afirmes que esto ya está disponible para el dueño ni que es el resultado "
            "real: dilo explícitamente como una simulación/demo, con el motivo exacto de "
            "arriba si el dueño pregunta por qué.]"
        )


class ProveedorDeclarado(ABC):
    """Clase base que TODO proveedor (real o simulado) de Edecán debe heredar.

    No reemplaza los `Protocol`/ABC de cada paquete (`STTProvider`,
    `TTSProvider`, `QuoteProvider`...) — se hereda ADEMÁS de esos, mismo
    patrón que cualquier mixin. Lo que aporta es exclusivamente
    `__init_subclass__`: la enforcement que hoy no existe en ningún lado.

    Antes de este módulo, siete de nueve clases stub del repo (medido:
    `StubImageProvider`, `StubAdsProvider`, `StubTravelProvider`,
    `StubTrackingProvider`, `StubVehiclesProvider`, `StubQuotes`,
    `StubSearch`) no heredaban de NADA — los protocolos que sí existen son
    `Protocol` estructurales, así que cumplirlos es tener el método, no
    haber declarado absolutamente nada. Esta clase es la que faltaba.
    """

    familia: ClassVar[str]
    fidelidad: ClassVar[Fidelidad]
    fuente: ClassVar[str]
    motivo_simulado: ClassVar[str | None] = None

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        faltan = [
            atributo
            for atributo in ("familia", "fidelidad", "fuente")
            if getattr(cls, atributo, None) is None
        ]
        if faltan:
            raise TypeError(
                f"{cls.__qualname__} no declara {faltan}: todo proveedor de Edecán tiene "
                "que decir qué es (`familia`), si es real o simulado (`fidelidad`) y de "
                "dónde sale el dato (`fuente`) como atributos de clase — ver "
                "packages/core/edecan_core/veracidad.py. Un proveedor que no declara esto "
                "es exactamente el patrón que le costó tiempo al dueño (StubTTS devolviendo "
                "silencio como si fuera audio real)."
            )
        if cls.fidelidad is Fidelidad.SIMULADO and not cls.motivo_simulado:
            raise TypeError(
                f"{cls.__qualname__} se declara SIMULADO pero no dice qué falta para ser "
                "real (`motivo_simulado`, ClassVar[str]): un stub mudo — que declara que es "
                "falso pero no dice ni por qué ni cómo arreglarlo — es exactamente lo que "
                "este contrato existe para prohibir."
            )

    def fidelidad_efectiva(self) -> Fidelidad:
        """Override este método (no el `ClassVar`) cuando la fidelidad real
        depende de config en tiempo de ejecución en vez de ser fija por
        clase — p. ej. la MISMA clase resolviendo sandbox vs. production
        según credenciales (`ResilientTravelProvider` es el caso real ya
        existente en el repo, `packages/travel/edecan_travel/providers.py`).
        Por defecto devuelve el `ClassVar` declarado, que cubre el caso
        común: una clase que SIEMPRE es real, o SIEMPRE es simulada.
        """
        return self.fidelidad

    def info_fidelidad(self) -> InfoFidelidad:
        """Construye el `InfoFidelidad` de ESTA instancia, listo para
        `ToolResult.fidelidad` — ver docstring del módulo."""
        return InfoFidelidad(
            familia=self.familia,
            fidelidad=self.fidelidad_efectiva(),
            fuente=self.fuente,
            motivo_simulado=self.motivo_simulado,
        )
