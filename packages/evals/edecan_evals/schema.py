"""Modelos Pydantic de una suite de evaluación (WP-15, ver `ARCHITECTURE.md` §10.6, §10.7).

Sin dependencia de paquetes hermanos: solo `pydantic`. Estos modelos son el
formato que `edecan_evals.loader` parsea desde YAML (`packages/evals/suites/*.yaml`)
y que `edecan_evals.runner` ejecuta/evalúa.

- `Suite`: una suite completa (un archivo YAML).
- `Caso`: un caso individual dentro de una suite — puede ser multi-turno
  (`mensajes` es una lista; el caso de `memoria.yaml` usa esto para "decirle"
  un hecho al asistente en un turno y preguntar por él en el siguiente).
- `Esperado`: las aserciones que `edecan_evals.runner.evaluar_caso` verifica
  contra la transcripción real del caso.
- `GuionEntry`: una entrada del `guion` opcional de una `Suite`, consumida por
  `edecan_evals.fakes.FakeLLMProvider` para responder de forma determinista
  según un regex aplicado al último mensaje.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

# Nombres EXACTOS de las 17 herramientas del toolkit no-premium, pinned en
# ARCHITECTURE.md §10.14. `edecan_evals.runner` registra un doble fake de cada
# una para que el agente (real o el doble local de pruebas) siempre tenga una
# herramienta válida que "llamar", sin importar qué suite se esté corriendo.
NOMBRES_HERRAMIENTAS_TOOLKIT: tuple[str, ...] = (
    "crear_recordatorio",
    "listar_recordatorios",
    "agenda_eventos",
    "crear_evento",
    "buscar_correo",
    "enviar_correo",
    "buscar_contactos",
    "gestionar_contacto",
    "registrar_transaccion",
    "resumen_finanzas",
    "consultar_documentos",
    "buscar_web",
    "generar_contenido",
    "publicar_social",
    "usar_computadora",
    "hora_actual",
    "calculadora",
)


class GuionEntry(BaseModel):
    """Una respuesta canned del `guion` de una suite.

    Exactamente uno de `texto`/`tool` debe estar presente: `texto` produce una
    respuesta final de texto (`stop_reason="end"`); `tool` (+ `args`
    opcionales) produce una invocación de herramienta (`stop_reason="tool_use"`).
    """

    texto: str | None = None
    tool: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _exactamente_uno(self) -> GuionEntry:
        if bool(self.texto) == bool(self.tool):
            raise ValueError(
                "GuionEntry requiere exactamente uno de 'texto' o 'tool' (no ambos, no ninguno)."
            )
        return self


class Esperado(BaseModel):
    """Aserciones esperadas para un `Caso` (evaluadas por `edecan_evals.runner.evaluar_caso`)."""

    tools_llamadas: list[str] | None = None
    """Secuencia EXACTA (orden incluido) de nombres de herramientas invocadas
    (comparada contra los eventos `tool_start` del turno). `None` = no se verifica."""

    contiene: list[str] | None = None
    """Subcadenas que deben aparecer (case-insensitive) en el texto final."""

    no_contiene: list[str] | None = None
    """Subcadenas que NO deben aparecer (case-insensitive) en el texto final."""

    rechaza: bool = False
    """Si es `True`, el caso solo aprueba si el texto final "suena" a una
    negativa del agente (ver `edecan_evals.runner._parece_rechazo`)."""


class Caso(BaseModel):
    """Un caso de evaluación dentro de una `Suite`."""

    id: str
    persona: dict[str, Any] = Field(default_factory=dict)
    """Override parcial de `edecan_schemas.PersonaConfig` (todos sus campos
    tienen default, así que un dict parcial —incluso vacío— es válido)."""

    mensajes: list[str]
    """Turnos de usuario, en orden. Un caso con 2+ mensajes es multi-turno:
    `edecan_evals.runner` acumula el historial entre turnos, así que
    `memoria.yaml` puede "enseñarle" un hecho al asistente en el primer
    mensaje y preguntar por él en el segundo."""

    esperado: Esperado

    @field_validator("mensajes")
    @classmethod
    def _al_menos_un_mensaje(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("'mensajes' no puede estar vacío")
        return v


class Suite(BaseModel):
    """Una suite de evaluación completa (un archivo YAML en `packages/evals/suites/`)."""

    nombre: str
    descripcion: str = ""
    casos: list[Caso]
    guion: dict[str, GuionEntry] = Field(default_factory=dict)
    """Mapa `regex_del_último_mensaje -> GuionEntry`, consumido por
    `FakeLLMProvider`. Se prueba en el orden de definición (los dicts de
    Python/PyYAML preservan orden de inserción); la primera coincidencia
    gana. No hace falta cubrir todos los mensajes: lo que no matchea cae al
    valor por defecto de `FakeLLMProvider`."""

    @field_validator("casos")
    @classmethod
    def _al_menos_un_caso(cls, v: list[Caso]) -> list[Caso]:
        if not v:
            raise ValueError("una suite debe tener al menos un caso")
        return v
