"""Minutas de reunión a partir de una transcripción: funciones **puras** (sin
red, sin base de datos) que arman el prompt para el LLM del tenant y parsean
su respuesta (`ARCHITECTURE.md` §15, WP-V6-05).

`construir_prompt_minutas` no llama a ningún proveedor — el llamador
(`apps/worker/edecan_worker/handlers/process_meeting.py`) es quien invoca
`ctx.llm`/`llm_router` con el texto que arma esta función, mismo criterio de
separación que `edecan_creative.podcast.validar_guion` (puro) frente a
`sintetizar_segmento` (hace la llamada de red).

`parsear_minutas` devuelve el dataclass `Minutas` (con `.to_dict()` para el
shape `{resumen, decisiones[], acciones[], temas[]}` que persiste
`process_meeting`), no un `dict` crudo — mismo criterio que
`edecan_creative.podcast.validar_guion` devuelve `list[SegmentoPodcast]` en
vez de `list[dict]`: un dataclass tipado es más seguro para el resto del
código (`process_meeting.py`) que sigue el mismo patrón, y sigue siendo
trivialmente serializable a la forma "dict tolerante a fences" que describe
el contrato con `.to_dict()`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

_MAX_TRANSCRIPT_CHARS = 60_000  # cota de seguridad para no reventar el contexto del LLM
_MAX_TITULO_CHARS = 200

_SYSTEM_INSTRUCCIONES = (
    "Eres un asistente que redacta minutas de reuniones a partir de una "
    "transcripción. Respondes EXCLUSIVAMENTE con un objeto JSON (sin texto "
    "antes ni después, sin explicaciones) con esta forma exacta:\n"
    "{\n"
    '  "resumen": "string — resumen de 3 a 6 frases de lo tratado",\n'
    '  "decisiones": ["string", "..."] — decisiones concretas que se tomaron,\n'
    '  "acciones": [{"tarea": "string", "responsable": "string o null"}],\n'
    '  "temas": ["string", "..."] — temas/etiquetas breves de la reunión\n'
    "}\n"
    "Si la transcripción no menciona decisiones, acciones o temas claros, "
    "devuelve una lista vacía para ese campo en vez de inventar contenido. "
    "Nunca agregues campos adicionales ni texto fuera del JSON. Responde en "
    "español salvo que la transcripción esté mayoritariamente en otro idioma."
)


@dataclass(frozen=True)
class AccionMinuta:
    """Una tarea/acción concreta detectada en la reunión."""

    tarea: str
    responsable: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return {"tarea": self.tarea, "responsable": self.responsable}


@dataclass(frozen=True)
class Minutas:
    """Minutas estructuradas de una reunión — el shape que persiste
    `process_meeting` en `meetings.resumen`/`decisiones`/`acciones`/`temas`."""

    resumen: str
    decisiones: list[str] = field(default_factory=list)
    acciones: list[AccionMinuta] = field(default_factory=list)
    temas: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "resumen": self.resumen,
            "decisiones": list(self.decisiones),
            "acciones": [a.to_dict() for a in self.acciones],
            "temas": list(self.temas),
        }


def construir_prompt_minutas(transcript: str, titulo: str | None = None) -> str:
    """Arma el prompt (en español) que le pide al LLM del tenant las minutas
    de la reunión `titulo` a partir de `transcript`. Puro — no llama a nada.

    `transcript` se acota a `_MAX_TRANSCRIPT_CHARS` (60 000 caracteres, cota
    de seguridad de contexto) tomando el PRINCIPIO del texto — igual criterio
    que otros truncados del repo (p. ej. `edecan_creative.podcast`
    `_cap_str`): mejor una minuta parcial de una transcripción larga que
    reventar la llamada al LLM por exceso de tokens.
    """
    titulo_normalizado = (titulo or "").strip()[:_MAX_TITULO_CHARS] or "Reunión sin título"
    texto = (transcript or "").strip()
    truncado = len(texto) > _MAX_TRANSCRIPT_CHARS
    texto = texto[:_MAX_TRANSCRIPT_CHARS]
    if not texto:
        texto = "(la transcripción llegó vacía — no se detectó voz o el STT no está conectado)"

    aviso_truncado = (
        "\n\n[Nota: la transcripción se truncó por longitud; las minutas se basan "
        "solo en la parte incluida arriba.]"
        if truncado
        else ""
    )

    return (
        f"{_SYSTEM_INSTRUCCIONES}\n\n"
        f"Título de la reunión: {titulo_normalizado}\n\n"
        f"Transcripción:\n{texto}{aviso_truncado}"
    )


_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)


def _quitar_fences(texto: str) -> str:
    """Quita fences de markdown (` ```json ... ``` ` o ` ``` ... ``` `) del
    inicio/fin del texto, si están — tolerante a que el LLM no las use."""
    limpio = texto.strip()
    limpio = _FENCE_RE.sub("", limpio).strip()
    return limpio


def _extraer_primer_objeto_json(texto: str) -> str | None:
    """Si `texto` trae contenido antes/después del objeto JSON (p. ej. "Aquí
    están las minutas: {...}"), recorta desde la primera `{` hasta la `}`
    correspondiente (balanceando llaves, ignorando las que aparecen dentro de
    strings). `None` si no encuentra un objeto balanceado."""
    inicio = texto.find("{")
    if inicio == -1:
        return None
    profundidad = 0
    dentro_string = False
    escapando = False
    for i in range(inicio, len(texto)):
        char = texto[i]
        if escapando:
            escapando = False
            continue
        if char == "\\" and dentro_string:
            escapando = True
            continue
        if char == '"':
            dentro_string = not dentro_string
            continue
        if dentro_string:
            continue
        if char == "{":
            profundidad += 1
        elif char == "}":
            profundidad -= 1
            if profundidad == 0:
                return texto[inicio : i + 1]
    return None


def _como_lista_de_strings(valor: object) -> list[str]:
    if not isinstance(valor, list):
        return []
    return [str(item).strip() for item in valor if str(item).strip()]


def _como_acciones(valor: object) -> list[AccionMinuta]:
    if not isinstance(valor, list):
        return []
    acciones: list[AccionMinuta] = []
    for item in valor:
        if isinstance(item, dict):
            tarea = str(item.get("tarea") or "").strip()
            if not tarea:
                continue
            responsable_raw = item.get("responsable")
            responsable = str(responsable_raw).strip() if responsable_raw else None
            acciones.append(AccionMinuta(tarea=tarea, responsable=responsable or None))
        elif isinstance(item, str) and item.strip():
            acciones.append(AccionMinuta(tarea=item.strip(), responsable=None))
    return acciones


def parsear_minutas(texto_llm: str) -> Minutas:
    """Parsea la respuesta del LLM a `Minutas`, tolerante a formato
    imperfecto — NUNCA lanza:

    - Quita fences de markdown (` ```json ... ``` `) si están.
    - Si hay texto antes/después del objeto JSON, extrae solo el objeto
      balanceado.
    - Si el JSON es inválido o no es un objeto, cae a un `Minutas` con
      `resumen` = el texto crudo (recortado) y las tres listas vacías — así
      la reunión igual queda "procesada" con algo útil en vez de perder toda
      la respuesta del modelo.
    - Campos con tipo inesperado (p. ej. `decisiones` no es una lista) se
      tratan como ausentes (lista vacía) en vez de reventar.
    """
    crudo = (texto_llm or "").strip()
    if not crudo:
        return Minutas(resumen="El modelo no devolvió ninguna minuta.")

    candidato = _quitar_fences(crudo)
    try:
        data = json.loads(candidato)
    except json.JSONDecodeError:
        objeto = _extraer_primer_objeto_json(candidato)
        data = None
        if objeto is not None:
            try:
                data = json.loads(objeto)
            except json.JSONDecodeError:
                data = None

    if not isinstance(data, dict):
        return Minutas(resumen=crudo[:4000])

    resumen = str(data.get("resumen") or "").strip() or crudo[:4000]
    return Minutas(
        resumen=resumen,
        decisiones=_como_lista_de_strings(data.get("decisiones")),
        acciones=_como_acciones(data.get("acciones")),
        temas=_como_lista_de_strings(data.get("temas")),
    )
