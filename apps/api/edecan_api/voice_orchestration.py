"""Orquestación de voz (Wave I): router de intención determinista.

Mientras el dueño habla con Edecán puede pedir en un mismo turno que delegue
trabajo a agentes ("dile al Developer que compruebe el deployment") sin cortar
la conversación. Este módulo decide, a partir del texto transcrito, si hay
intención de delegación y —si la hay— separa el pedido en:

1. **delegado**: sub-tareas con `{target, instruction, kind}` para encolar por
   el camino existente `delegar_mision` (`DelegarMisionTool`).
2. **reply_text**: el resto del pedido, que Edecán responde YA en el turno
   (keep-talking), p. ej. "busca un restaurante para esta noche".

## Diseño: determinista-primero, LLM opcional (nunca único)

El parser es 100% determinista (patrones de palabras clave/regex). El
clasificador LLM es un gancho OPCIONAL que solo se consulta cuando el parser
determinista no encontró nada — y jamás puede anular una detección determinista
(p. ej. para volver "no-delegación" algo que el parser ya detectó). En Wave I
ese gancho llega sin cablear (el servicio pasa `None`), de modo que la única
fuente de delegación es el parser; no se fabrica ninguna llamada a modelo.

## Seguridad

Una delegación desde voz NUNCA ejecuta por su cuenta una acción irreversible
(compras, pagos, transferencias, borrados, firmas, contrataciones): el parser
marca `requires_approval=True` y el servicio no la encola — la deja pendiente
de aprobación humana y lo dice en voz alta. El resto de delegaciones reutiliza
los mismos gates de `delegar_mision` (flag `agents.missions` y
`limits.missions_per_day`): si el tenant no tiene cupo, la tool devuelve error
y el turno responde normal (fail-open), nunca una voz muerta.

## Limitación conocida (declarada, no escondida)

La separación de cláusulas se hace por conectores naíf (`,`, `;`, ` y `, ` o `,
` además`, ` también`, ` luego`, ` después`, ` mientras`). Una instrucción que
contenga un " y " interno (p. ej. "dile al Developer que revise el deployment
y reporta") se parte en dos cláusulas y la segunda se trata como keep-talking.
Resolver targets con nombre ("Developer" → `worker_id` real, para el handoff
`persistent_agent_handoffs`) es seguimiento de Wave II: acá el target viaja como
metadato y se encola la MISMA misión `delegar_mision` (sin handoff, porque un
turno de voz no es un worker y `source_worker_id` no existe en ese contexto).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Mapping
from typing import Any, Literal

from pydantic import BaseModel, Field


class VoiceDelegation(BaseModel):
    """Una sub-tarea delegada detectada en el texto transcrito."""

    target: str = ""
    """Agente/worker nombrado ("Developer"), o "" para una misión genérica."""

    instruction: str
    """La instrucción imperativa tal cual se pronunció ("compruebe el deployment")."""

    kind: Literal["agent", "mission"] = "agent"
    """`agent` = delegación con destino nombrado; `mission` = misión genérica."""

    requires_approval: bool = False
    """True si la instrucción es una acción irreversible (compra/pago/borrado...):
    el servicio NO debe encolarla sin aprobación humana."""


class VoiceOrchestration(BaseModel):
    """Resultado tipado del router: lo que se delega y lo que se responde ya."""

    delegated: list[VoiceDelegation] = Field(default_factory=list)
    reply_text: str = ""
    """Resto del pedido que Edecán responde ahora (keep-talking). Vacío si TODO
    el turno era delegación."""


# ---------------------------------------------------------------------------
# Verbos de delegación con destino explícito ("dile a X que Y", "encarga a X...").
# Orden importa: los verbos largos van antes que sus prefijos ("dile" antes que
# "di"), y "di" lleva \b para no capturar "dilema"/"divino".
# ---------------------------------------------------------------------------

_TARGETED_VERBS_QUE = (
    "dile|díle|dígale|digale|"
    "pídele|pidele|pide|"
    "avísale|avisale|avisa|"
    "cuéntale|cuentale|"
    "escríbele|escribele|"
    "ordénale|ordenale|ordena|"
    "mándale|mandale|manda|"
    "encárgale|encargale|encarga|"
    "repórtale|reportale|"
    "coméntale|comentale|"
    "\\bdi\\b"
)

_RE_TARGETED_QUE = re.compile(
    rf"^\s*(?:{_TARGETED_VERBS_QUE})\s+a(?:l|la|los|las)?\s+"
    r"(?P<target>.+?)\s+que\s+(?P<instruction>.+?)\s*$",
    re.IGNORECASE,
)

# "pon a X a Y" / "pon a X que Y" (dos preposiciones distintas del resto).
_RE_TARGETED_PON = re.compile(
    r"^\s*(?:ponte|ponle|pónle|pon)\s+a(?:l|la|los|las)?\s+"
    r"(?P<target>.+?)\s+(?:a|que)\s+(?P<instruction>.+?)\s*$",
    re.IGNORECASE,
)

# Misión genérica sin destino nombrado ("encarga una misión que ...").
_RE_MISSION = re.compile(
    r"^\s*(?:encarga|encargate|crea|prepara|organiza)\s+(?:una\s+)?"
    r"(?:misión|mision|tarea|investigación|investigacion)\s+"
    r"(?:que|para(?:\s+que)?)\s+(?P<instruction>.+?)\s*$",
    re.IGNORECASE,
)

# Conectores entre cláusulas de un mismo turno hablado.
_RE_CLAUSE_SPLIT = re.compile(
    r"\s+[yo]\s+|\s*[,;]\s*|\s+(?:además|ademas|también|tambien|luego|después|despues|mientras)\b",
    re.IGNORECASE,
)

_TERMINOS_IRREVERSIBLES = {
    # compras / pagos / transferencias
    "paga", "pagar", "pague", "paguen", "abona", "abonar", "abone",
    "compra", "comprar", "compre", "compres",
    "transfiere", "transferir", "transfiera", "transfieres",
    "cobra", "cobrar", "cobre",
    # firmas / contrataciones / despidos
    "firma", "firmar", "firme",
    "contrata", "contratar", "contrate",
    "despide", "despedir", "despida",
    # borrados / despublicación / resets
    "borra", "borrar", "borre",
    "elimina", "eliminar", "elimine",
    "despublica", "despublicar",
    "resetea", "resetear", "restablece", "restablecer",
}

_FRASES_IRREVERSIBLES = (
    "envia dinero",
    "envia una transferencia",
    "haz un pago",
    "hace un pago",
    "cierra la cuenta",
    "cancela la cuenta",
    "da de baja",
    "borra todos",
    "elimina todos",
    "despliega a produccion",
    "publica en produccion",
)


def _fold(text: str) -> str:
    """Minúsculas y sin tildes, para comparar sin depender del acento del habla."""
    return "".join(
        c for c in unicodedata.normalize("NFD", text.lower())
        if unicodedata.category(c) != "Mn"
    )


def _normalize_target(target: str) -> str:
    target = target.strip()
    target = re.sub(r"^(el|la|los|las)\s+", "", target, flags=re.IGNORECASE)
    return target.strip()


def resolve_worker_id(workers: list[Mapping[str, Any]], target: str) -> str | None:
    """Resuelve el nombre de un worker ("Developer", "Research"...) al `id`
    real de `persistent_agents` del tenant (product design).

    Matchea contra `name`/`display_name`/`role_title` sin distinguir
    mayúsculas ni tildes (`_fold`). Orden determinista: primero coincidencia
    exacta en `name` → `display_name` → `role_title`; si ninguna exacta, se
    acepta subcadena en el mismo orden. Devuelve `None` si no hay match o si
    el worker no trae `id`.
    """
    objetivo = _fold(target)
    if not objetivo:
        return None
    campos = ("name", "display_name", "role_title")
    for campo in campos:
        for worker in workers:
            if _fold(str(worker.get(campo) or "")) == objetivo:
                worker_id = str(worker.get("id") or "").strip()
                return worker_id or None
    for campo in campos:
        for worker in workers:
            valor = _fold(str(worker.get(campo) or ""))
            if valor and (objetivo in valor or valor in objetivo):
                worker_id = str(worker.get("id") or "").strip()
                return worker_id or None
    return None


def _clean_instruction(instruction: str) -> str:
    return instruction.strip().strip(" ,;.!?")


def _requiere_aprobacion(instruction: str) -> bool:
    folded = _fold(instruction)
    tokens = set(re.findall(r"[a-z]+", folded))
    if tokens & _TERMINOS_IRREVERSIBLES:
        return True
    return any(frase in folded for frase in _FRASES_IRREVERSIBLES)


def _split_clauses(text: str) -> list[str]:
    parts = _RE_CLAUSE_SPLIT.split(text)
    return [p.strip(" ,;") for p in parts if p.strip(" ,;")]


def _match_delegation(clause: str) -> VoiceDelegation | None:
    m = _RE_TARGETED_QUE.match(clause) or _RE_TARGETED_PON.match(clause)
    if m:
        target = _normalize_target(m.group("target"))
        instruction = _clean_instruction(m.group("instruction"))
        if target and instruction:
            return VoiceDelegation(
                target=target,
                instruction=instruction,
                kind="agent",
                requires_approval=_requiere_aprobacion(instruction),
            )
    m = _RE_MISSION.match(clause)
    if m:
        instruction = _clean_instruction(m.group("instruction"))
        if instruction:
            return VoiceDelegation(
                target="",
                instruction=instruction,
                kind="mission",
                requires_approval=_requiere_aprobacion(instruction),
            )
    return None


def _route_deterministico(text: str) -> VoiceOrchestration | None:
    texto = (text or "").strip()
    if not texto:
        return None
    clauses = _split_clauses(texto)
    delegated: list[VoiceDelegation] = []
    remainder: list[str] = []
    for clause in clauses:
        delegation = _match_delegation(clause)
        if delegation is not None:
            delegated.append(delegation)
        else:
            remainder.append(clause)
    if not delegated:
        return None
    return VoiceOrchestration(
        delegated=delegated,
        reply_text=" ".join(remainder).strip(),
    )


def route_voice_intent(
    text: str,
    llm_classifier: Callable[[str], VoiceOrchestration | None] | None = None,
) -> VoiceOrchestration | None:
    """Decide si el turno contiene intención de delegación y lo divide.

    Determinista-primero: el parser de patrones es la ÚNICA fuente de
    delegación en Wave I. `llm_classifier`, si se provee, solo se consulta
    cuando el parser no detectó nada (fallback), nunca para anular una
    detección determinista, y su salida se valida antes de devolverse.
    Devuelve `None` si no hay intención de delegación (el turno corre normal).
    """
    determinista = _route_deterministico(text)
    if determinista is not None:
        return determinista
    if llm_classifier is None:
        return None
    resultado = llm_classifier(text)
    if isinstance(resultado, VoiceOrchestration):
        return resultado
    return None