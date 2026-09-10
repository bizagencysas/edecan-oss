"""Selección determinística de bot especialista desde lenguaje natural.

El dueño habla en frases cotidianas; el equipo elige quién responde sin pedir
nombres de bots ni herramientas. La ambigüedad fuerte se resuelve en el turno
con ``preguntar_al_usuario``, no bloqueando el encargo.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from edecan_core.bot_persona import worker_display_name

# Arquetipos alineados con OficioEquipo + frontend/backend/community.
_ARCHETYPE_KEYWORDS: dict[str, frozenset[str]] = {
    "frontend_ui": frozenset(
        {
            "android",
            "boton",
            "botones",
            "componente",
            "css",
            "formulario",
            "frontend",
            "html",
            "interfaz",
            "ios",
            "layout",
            "login",
            "logout",
            "pantalla",
            "pantallas",
            "sesion",
            "signin",
            "signup",
            "swift",
            "swiftui",
            "ui",
            "ux",
            "webview",
        }
    ),
    "backend_api": frozenset(
        {
            "api",
            "backend",
            "curl",
            "endpoint",
            "endpoints",
            "fastapi",
            "health",
            "healthz",
            "infra",
            "migracion",
            "postgres",
            "servidor",
            "smoke",
            "smokea",
            "smokear",
            "sql",
            "worker",
        }
    ),
    "community_social": frozenset(
        {
            "borrador",
            "community",
            "contenido",
            "facebook",
            "instagram",
            "linkedin",
            "marketing",
            "post",
            "posts",
            "publicar",
            "redacta",
            "redactar",
            "redes",
            "social",
            "threads",
            "tiktok",
            "tweet",
            "x.com",
        }
    ),
    "bugs_qa": frozenset(
        {
            "bug",
            "bugs",
            "crash",
            "error",
            "excepcion",
            "falla",
            "fallo",
            "regresion",
            "reproduce",
            "reproducir",
            "roto",
            "ticket",
        }
    ),
    "producto": frozenset(
        {
            "analytics",
            "feature",
            "kpi",
            "metricas",
            "producto",
            "roadmap",
            "usuario",
            "usuarios",
        }
    ),
    "ventas": frozenset(
        {
            "crm",
            "outbound",
            "pipeline",
            "prospecto",
            "prospectos",
            "ventas",
        }
    ),
    "jefe_staff": frozenset(
        {
            "coordina",
            "coordinar",
            "delega",
            "equipo",
            "organiza",
            "prioriza",
            "staff",
        }
    ),
    "viajes": frozenset(
        {
            "hotel",
            "hoteles",
            "reserva",
            "viaje",
            "viajes",
            "vuelo",
            "vuelos",
        }
    ),
}

_WORKER_HINTS: dict[str, frozenset[str]] = {
    "frontend_ui": frozenset(
        {"diseño", "diseno", "frontend", "interfaz", "ios", "mobile", "swift", "ui", "ux"}
    ),
    "backend_api": frozenset(
        {"api", "backend", "devops", "infra", "ingenieria", "servidor", "software"}
    ),
    "community_social": frozenset(
        {
            "community",
            "contenido",
            "linkedin",
            "manager",
            "marketing",
            "redes",
            "social",
        }
    ),
    "bugs_qa": frozenset({"bug", "bugs", "calidad", "qa", "reproduc", "testing"}),
    "producto": frozenset({"product", "producto", "pm", "metricas"}),
    "ventas": frozenset({"outbound", "sales", "ventas"}),
    "jefe_staff": frozenset({"chief", "coordin", "jefe", "staff"}),
    "viajes": frozenset({"hotel", "travel", "viaje", "vuelo"}),
}

_LEXICAL_STOPWORDS = frozenset(
    {
        "algo",
        "arregla",
        "arreglar",
        "con",
        "crear",
        "de",
        "el",
        "esta",
        "este",
        "haz",
        "hacer",
        "la",
        "las",
        "lo",
        "los",
        "me",
        "mi",
        "mis",
        "para",
        "por",
        "que",
        "un",
        "una",
        "yo",
    }
)


@dataclass(frozen=True)
class BotRoutingResult:
    agent_id: str
    display_name: str
    archetype: str
    score: float
    reason: str
    ambiguous: bool
    runner_up_name: str | None = None


def _normalize(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text.casefold())
    return "".join(ch for ch in folded if not unicodedata.combining(ch))


def _tokens(text: str) -> set[str]:
    normalized = _normalize(text)
    raw = re.findall(r"[a-z0-9]+", normalized)
    return {tok for tok in raw if len(tok) > 1 and tok not in _LEXICAL_STOPWORDS}


def _worker_corpus(worker: Mapping[str, object]) -> str:
    parts: list[str] = []
    for key in (
        "display_name",
        "name",
        "purpose",
        "job_description",
        "role_title",
        "role_short",
        "instructions",
        "personality",
    ):
        value = worker.get(key)
        if value:
            parts.append(str(value))
    return " ".join(parts)


def _intent_archetype_scores(user_tokens: set[str]) -> dict[str, int]:
    scores: dict[str, int] = {}
    for archetype, keywords in _ARCHETYPE_KEYWORDS.items():
        hits = len(user_tokens.intersection(keywords))
        if hits:
            scores[archetype] = hits
    return scores


def _worker_archetype_scores(corpus_tokens: set[str]) -> dict[str, int]:
    scores: dict[str, int] = {}
    for archetype, hints in _WORKER_HINTS.items():
        hits = len(corpus_tokens.intersection(hints))
        if hits:
            scores[archetype] = hits
    return scores


def score_worker_for_intent(
    worker: Mapping[str, object],
    user_text: str,
    *,
    intent_scores: dict[str, int] | None = None,
) -> tuple[float, str, str]:
    """Puntúa un bot contra el texto del dueño. Devuelve (score, arquetipo, razón)."""

    user_tokens = _tokens(user_text)
    intent = intent_scores if intent_scores is not None else _intent_archetype_scores(user_tokens)
    corpus_tokens = _tokens(_worker_corpus(worker))

    if not intent and not user_tokens:
        return 0.0, "general", "sin señales de intención"

    best_archetype = "general"
    best_component = 0.0
    for archetype, intent_hits in intent.items():
        worker_hits = _worker_archetype_scores(corpus_tokens).get(archetype, 0)
        direct = len(user_tokens.intersection(_ARCHETYPE_KEYWORDS.get(archetype, frozenset())))
        direct_corpus = len(
            corpus_tokens.intersection(_ARCHETYPE_KEYWORDS.get(archetype, frozenset()))
        )
        component = (
            float(intent_hits) * 3.0
            + float(worker_hits) * 2.5
            + float(direct) * 2.0
            + float(direct_corpus) * 1.5
        )
        if component > best_component:
            best_component = component
            best_archetype = archetype

    # Nombre visible que contiene palabras del pedido (p. ej. «Bugs»).
    name_tokens = _tokens(worker_display_name(worker))
    name_overlap = len(user_tokens.intersection(name_tokens))
    best_component += float(name_overlap) * 1.0

    reason_parts: list[str] = []
    if intent:
        top_intent = max(intent.items(), key=lambda item: item[1])[0]
        reason_parts.append(f"intención {top_intent.replace('_', ' ')}")
    if best_archetype != "general":
        reason_parts.append(f"perfil {best_archetype.replace('_', ' ')}")
    reason = ", ".join(reason_parts) if reason_parts else "mejor coincidencia del equipo"

    return best_component, best_archetype, reason


def select_responder_for_team(
    workers: Sequence[Mapping[str, object]],
    user_text: str,
    *,
    coordinator_id: str | None = None,
) -> BotRoutingResult:
    """Elige el bot que debe tomar el turno del equipo."""

    if not workers:
        raise ValueError("workers vacío")
    if len(workers) == 1:
        only = workers[0]
        agent_id = str(only.get("id") or "")
        return BotRoutingResult(
            agent_id=agent_id,
            display_name=worker_display_name(only),
            archetype="general",
            score=0.0,
            reason="único miembro del equipo",
            ambiguous=False,
        )

    user_tokens = _tokens(user_text)
    intent_scores = _intent_archetype_scores(user_tokens)

    ranked: list[tuple[float, str, str, Mapping[str, object]]] = []
    for worker in workers:
        score, archetype, reason = score_worker_for_intent(
            worker, user_text, intent_scores=intent_scores
        )
        ranked.append((score, archetype, reason, worker))

    ranked.sort(key=lambda row: row[0], reverse=True)

    # Desempate estable: coordinador solo si empatan en señal débil.
    if coordinator_id and len(ranked) >= 2 and ranked[0][0] == ranked[1][0]:
        for index, (_, _, _, worker) in enumerate(ranked):
            if str(worker.get("id") or "") == coordinator_id:
                ranked.insert(0, ranked.pop(index))
                break

    top_score, top_archetype, top_reason, top_worker = ranked[0]
    runner_name: str | None = None
    ambiguous = False
    if len(ranked) >= 2:
        second_score, _, _, second_worker = ranked[1]
        runner_name = worker_display_name(second_worker)
        if top_score <= 0:
            ambiguous = True
        elif second_score > 0 and (top_score - second_score) <= max(1.5, top_score * 0.25):
            ambiguous = True

    if top_score <= 0 and coordinator_id:
        for worker in workers:
            if str(worker.get("id") or "") == coordinator_id:
                top_worker = worker
                top_reason = "coordinador por defecto"
                top_archetype = "jefe_staff"
                break

    return BotRoutingResult(
        agent_id=str(top_worker.get("id") or ""),
        display_name=worker_display_name(top_worker),
        archetype=top_archetype,
        score=top_score,
        reason=top_reason,
        ambiguous=ambiguous,
        runner_up_name=runner_name,
    )
