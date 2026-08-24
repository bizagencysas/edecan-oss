"""Importador local y privado desde una instalación heredada de asistente.

La implementación reconoce exclusivamente un esquema canónico neutral. Si la
instalación anterior usa otra estructura, un ``--source-map`` privado y externo
al repositorio traduce claves canónicas a rutas relativas dentro de la carpeta
fuente. El código público no necesita conocer nombres históricos.

Principios:

* dry-run por defecto; ``--apply`` es obligatorio para escribir;
* solo lee una lista blanca de archivos de datos y prompts;
* no carga credenciales salvo con el opt-in explícito ``--import-credentials``;
* redacta secretos accidentales antes de persistir texto;
* usa ids UUIDv5 deterministas para que repetir la importación sea idempotente;
* conserva el corpus privado fuera del repositorio, con permisos 0700/0600;
* importa horarios como automatizaciones deshabilitadas. La persona revisa y
  activa cada una; ninguna publicación ni llamada se dispara al migrar.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import hashlib
import json
import logging
import os
import re
import sys
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from edecan_core.memory import DEFAULT_EMBEDDINGS_DIM, HashEmbedder

logger = logging.getLogger(__name__)

_NAMESPACE = uuid.UUID("ca19d59d-ae5c-5e82-b247-45cffb2ae294")
_SOURCE_TAG = "private-import:legacy-assistant"
_PRIVATE_IMPORT_MARKER = "EDECAN_PRIVATE_LEGACY_STYLE_V1"
_MAX_MESSAGE_CHARS = 64_000
_MAX_FACT_CHARS = 4_000
_MAX_FACTS = 500
_MAX_PROFILE_ITEMS = 20
_SOURCE_PATH_DEFAULTS = {
    "identity_context": "identity/context.txt",
    "assistant_persona": "persona/instructions.md",
    "structured_profile": "memory/profile.json",
    "semantic_memory": "memory/items.jsonl",
    "writing_style": "writing/style.md",
    "writing_corpus": "writing/corpus.txt",
    "conversation_index": "conversations/index.json",
    "conversation_directory": "conversations/items",
    "global_history": "conversations/history.jsonl",
    "schedules": "automations/schedules.json",
    "assistant_call_prompt": "calls/assistant.md",
    "sales_call_prompt": "calls/sales.md",
    "voice_config": "calls/voice.json",
    "linkedin_editorial": "social/linkedin.md",
    "x_editorial": "social/x.md",
}
_PYTHON_CONSTANT_KEYS = {
    "assistant_voice_id",
    "assistant_opening_message",
    "sales_voice_id",
    "sales_voice_map",
}

_SECRET_PATTERNS = (
    re.compile(
        r"(?i)\b(api[_ -]?key|secret|token|password|passwd|authorization)"
        r"\s*[:=]\s*[\"']?[^\s,\"']{8,}"
    ),
    re.compile(r"\b(?:sk-(?:proj-|ant-)?|gh[pousr]_|github_pat_)[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bAKIA[A-Z0-9]{16}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
)
_TEST_CONVERSATION = re.compile(
    r"(?i)^(?:prueba|test|debug|eval|diagnostico|antimentira|c[1-9]\d*|d[1-9]\d*)"
)
_PREFERENCE_WORDS = re.compile(
    r"(?i)\b(prefiere|preferencia|le gusta|no le gusta|quiere|no quiere|evita|odia|tono|estilo)\b"
)
_RELATION_WORDS = re.compile(
    r"(?i)\b(hija|hijo|pareja|esposa|esposo|familia|socia|socio|madre|padre|perro|mascota)\b"
)
_COMPANY_WORDS = re.compile(
    r"(?i)\b(empresa|compañ[ií]a|llc|sas|inc\.?|negocio|sociedad|startup|corporaci[oó]n)\b"
)
_PROJECT_WORDS = re.compile(
    r"(?i)\b(proyecto|app|aplicaci[oó]n|producto|plataforma|repo|repositorio|software|startup)\b"
)
_GOAL_WORDS = re.compile(
    r"(?i)\b(meta|objetivo|quiere lograr|planea|plan|aspira|futuro|mudarse|alcanzar)\b"
)
_HABIT_WORDS = re.compile(
    r"(?i)\b(h[aá]bito|rutina|siempre|normalmente|suele|trabaja|usa|workflow|flujo)\b"
)


@dataclass(frozen=True)
class LegacyMessage:
    role: str
    text: str
    created_at: datetime


@dataclass(frozen=True)
class LegacyConversation:
    source_id: str
    title: str
    messages: tuple[LegacyMessage, ...]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class LegacyAutomation:
    source_id: str
    name: str
    description: str
    rrule: str
    instruction: str


@dataclass(frozen=True)
class LegacyPhoneAgent:
    name: str
    agent_name: str
    persona_prompt: str
    default_goal: str
    opening_message: str
    knowledge_context: str
    required_information: str
    voice_id: str | None
    operating_profile: dict[str, str]
    handles_inbound: bool
    handles_outbound: bool
    is_default: bool
    is_inbound_default: bool


@dataclass(frozen=True)
class LegacyCredential:
    connector_key: str
    external_account_id: str
    display_name: str
    access_token: str = field(repr=False)
    refresh_token: str | None = field(repr=False)
    scopes: tuple[str, ...]
    token_type: str
    expires_at: datetime | None


@dataclass(frozen=True)
class SourceLayout:
    paths: dict[str, Path]
    python_constants: dict[str, str]


@dataclass
class ImportPlan:
    facts: list[str] = field(default_factory=list)
    identity: dict[str, str] = field(default_factory=dict)
    profile_summary: str = ""
    profile_lists: dict[str, list[str]] = field(default_factory=dict)
    persona_style: str = ""
    social_profiles: dict[str, dict[str, Any]] = field(default_factory=dict)
    phone_agents: list[LegacyPhoneAgent] = field(default_factory=list)
    credentials: list[LegacyCredential] = field(default_factory=list, repr=False)
    automations: list[LegacyAutomation] = field(default_factory=list)
    conversations: list[LegacyConversation] = field(default_factory=list)
    private_documents: dict[str, str] = field(default_factory=dict)
    skipped_conversations: int = 0

    def public_counts(self) -> dict[str, int]:
        return {
            "facts": len(self.facts),
            "phone_agents": len(self.phone_agents),
            "credentials": len(self.credentials),
            "social_profiles": len(self.social_profiles),
            "automations_disabled": len(self.automations),
            "conversations": len(self.conversations),
            "messages": sum(len(item.messages) for item in self.conversations),
            "private_documents": len(self.private_documents),
            "skipped_conversations": self.skipped_conversations,
        }


def redact_secrets(value: str) -> str:
    clean = value.replace("\x00", "")
    for pattern in _SECRET_PATTERNS:
        clean = pattern.sub("[SECRETO OMITIDO]", clean)
    return clean


def _read_text(path: Path, *, max_bytes: int) -> str:
    try:
        if not path.is_file() or path.stat().st_size > max_bytes:
            return ""
        return redact_secrets(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return ""


def _read_json(path: Path, *, max_bytes: int) -> Any:
    raw = _read_text(path, max_bytes=max_bytes)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _validate_relative_source_path(value: Any, *, key: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"La ruta de {key} debe ser texto relativo no vacío.")
    relative = Path(value.strip())
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"La ruta de {key} debe permanecer dentro de --source.")
    return relative


def _load_source_layout(source: Path, source_map_path: Path | None) -> SourceLayout:
    overrides: dict[str, Any] = {}
    python_constants: dict[str, str] = {}
    if source_map_path is not None:
        resolved_map = source_map_path.expanduser().resolve()
        repo_root = Path(__file__).resolve().parents[3]
        if _inside(resolved_map, repo_root):
            raise ValueError("El --source-map privado debe quedar fuera del repositorio.")
        payload = _read_json(resolved_map, max_bytes=256_000)
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            raise ValueError("El --source-map privado debe tener schema_version=1.")
        raw_paths = payload.get("paths")
        if not isinstance(raw_paths, dict):
            raise ValueError("El --source-map privado debe contener un objeto paths.")
        unknown_paths = set(raw_paths) - set(_SOURCE_PATH_DEFAULTS)
        if unknown_paths:
            raise ValueError(
                "El --source-map contiene claves de ruta desconocidas: "
                + ", ".join(sorted(unknown_paths))
            )
        overrides = dict(raw_paths)
        raw_constants = payload.get("python_constants", {})
        if not isinstance(raw_constants, dict):
            raise ValueError("python_constants debe ser un objeto.")
        unknown_constants = set(raw_constants) - _PYTHON_CONSTANT_KEYS
        if unknown_constants:
            raise ValueError(
                "El --source-map contiene claves de constantes desconocidas: "
                + ", ".join(sorted(unknown_constants))
            )
        for key, value in raw_constants.items():
            if not isinstance(value, str) or not re.fullmatch(
                r"[A-Za-z_][A-Za-z0-9_]{0,127}", value
            ):
                raise ValueError(f"python_constants.{key} debe ser un identificador válido.")
            python_constants[key] = value

    resolved_paths: dict[str, Path] = {}
    for key, default in _SOURCE_PATH_DEFAULTS.items():
        relative = _validate_relative_source_path(overrides.get(key, default), key=key)
        candidate = (source / relative).resolve()
        if not _inside(candidate, source):
            raise ValueError(f"La ruta de {key} sale de --source.")
        resolved_paths[key] = candidate
    return SourceLayout(paths=resolved_paths, python_constants=python_constants)


def _read_source_for_ast(path: Path, *, max_bytes: int) -> str:
    """Lee código como datos, sin ejecutarlo ni alterar su sintaxis.

    Solo se usa para extraer con ``literal_eval`` constantes expresamente
    permitidas. Ninguna otra asignación se devuelve ni se persiste.
    """

    try:
        if not path.is_file() or path.stat().st_size > max_bytes:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _stable_id(kind: str, value: str) -> uuid.UUID:
    return uuid.uuid5(_NAMESPACE, f"{kind}:{value}")


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _parse_time(value: Any, fallback: datetime | None = None) -> datetime:
    default = fallback or datetime.now(UTC)
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), UTC)
        except (OSError, OverflowError, ValueError):
            return default
    text = str(value or "").strip()
    if not text:
        return default
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=parsed.tzinfo or UTC).astimezone(UTC)
    except ValueError:
        return default


def _unique(values: Iterable[str], *, max_items: int = _MAX_PROFILE_ITEMS) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = re.sub(r"\s+", " ", redact_secrets(str(value))).strip()
        key = _normalize(clean)
        if not clean or key in seen:
            continue
        seen.add(key)
        output.append(clean[:_MAX_FACT_CHARS])
        if len(output) >= max_items:
            break
    return output


def _parse_identity(about: str) -> dict[str, str]:
    aliases = {
        "nombre preferido": "nombre_preferido",
        "nombre completo": "nombre_completo",
        "nacimiento": "fecha_nacimiento",
        "nacionalidad": "pais",
        "residencia principal conocida": "ciudad",
        "rol": "ocupacion",
        "idioma preferido": "idioma_preferido",
        "forma de trato": "forma_de_trato",
    }
    identity: dict[str, str] = {}
    for raw_line in about.splitlines()[:120]:
        if ":" not in raw_line:
            continue
        label, value = raw_line.split(":", 1)
        field_name = aliases.get(_normalize(label).rstrip("."))
        clean = value.strip()
        if field_name and clean:
            identity[field_name] = clean[:160]
    if about:
        first_section = about.split("\n2.", 1)[0]
        identity["biografia"] = re.sub(r"\s+", " ", first_section).strip()[:1_000]
    return identity


def _classify_profile(facts: list[str]) -> dict[str, list[str]]:
    buckets: dict[str, list[str]] = {
        "gustos": [],
        "proyectos": [],
        "metas": [],
        "relaciones": [],
        "empresas": [],
        "habitos": [],
    }
    for fact in facts:
        if _RELATION_WORDS.search(fact):
            buckets["relaciones"].append(fact)
        elif _COMPANY_WORDS.search(fact):
            buckets["empresas"].append(fact)
        elif _PROJECT_WORDS.search(fact):
            buckets["proyectos"].append(fact)
        elif _GOAL_WORDS.search(fact):
            buckets["metas"].append(fact)
        elif _HABIT_WORDS.search(fact):
            buckets["habitos"].append(fact)
        elif _PREFERENCE_WORDS.search(fact):
            buckets["gustos"].append(fact)
        else:
            buckets["proyectos"].append(fact)
    return {key: _unique(values) for key, values in buckets.items()}


def _profile_summary(identity: dict[str, str], facts: list[str]) -> str:
    pieces: list[str] = []
    name = identity.get("nombre_preferido") or identity.get("nombre_completo")
    if name:
        pieces.append(f"Te llamas {name}.")
    if identity.get("ocupacion"):
        pieces.append(identity["ocupacion"].rstrip(".") + ".")
    if identity.get("ciudad"):
        pieces.append(f"Tu residencia principal conocida es {identity['ciudad'].rstrip('.')}.")
    if not pieces and facts:
        pieces.append(facts[0])
    return " ".join(pieces)[:500]


def _extract_python_constants(
    path: Path,
    *,
    names: dict[str, str],
) -> dict[str, Any]:
    raw = _read_source_for_ast(path, max_bytes=2_000_000)
    if not raw or not names:
        return {}
    try:
        module = ast.parse(raw)
    except SyntaxError:
        return {}
    reverse_names = {source_name: canonical_key for canonical_key, source_name in names.items()}
    output: dict[str, Any] = {}
    for node in module.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Name):
                continue
            canonical_key = reverse_names.get(target.id)
            if canonical_key is None:
                continue
            try:
                output[canonical_key] = ast.literal_eval(node.value)
            except (ValueError, TypeError):
                # Algunas instalaciones configuran la voz por entorno y dejan
                # un fallback literal. El importador nunca ejecuta el módulo:
                # para el id de voz solo toma el último literal de texto.
                if canonical_key == "assistant_voice_id":
                    fallback = _last_literal_string(node.value)
                    if fallback:
                        output[canonical_key] = fallback
    return output


def _load_voice_config(path: Path, *, python_constants: dict[str, str]) -> dict[str, Any]:
    if path.suffix.casefold() == ".json":
        payload = _read_json(path, max_bytes=256_000)
        if not isinstance(payload, dict):
            return {}
        return {
            key: payload[key]
            for key in _PYTHON_CONSTANT_KEYS
            if key in payload
        }
    if python_constants:
        return _extract_python_constants(path, names=python_constants)
    return {}


def _last_literal_string(node: ast.AST) -> str | None:
    """Obtiene el fallback literal final sin ejecutar expresiones del módulo."""

    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value.strip() or None
    if isinstance(node, ast.BoolOp):
        for value in reversed(node.values):
            if fallback := _last_literal_string(value):
                return fallback
        return None
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        if node.func.attr == "strip":
            return _last_literal_string(node.func.value)
    return None


def _detect_agent_name(prompt: str, fallback: str) -> str:
    patterns = (
        r"(?m)^[Ee]res\s+\*\*([^*\n]+)\*\*",
        r"(?m)^[Ee]res\s+([A-ZÁÉÍÓÚÑ][^,.\n]{1,70})",
    )
    for pattern in patterns:
        match = re.search(pattern, prompt)
        if match:
            return re.sub(r"\s+", " ", match.group(1)).strip()[:80]
    return fallback[:80]


def _prompt_parts(prompt: str) -> tuple[str, str, str, str]:
    clean = redact_secrets(prompt).strip()
    return clean[:4_000], clean[4_000:10_000], clean[10_000:13_000], clean[13_000:17_000]


def _negative_rules(prompt: str) -> str:
    lines = [
        re.sub(r"\s+", " ", line).strip(" -*")
        for line in prompt.splitlines()
        if re.search(r"(?i)\b(nunca|jam[aá]s|prohibid|no debes|no puedes)\b", line)
    ]
    return "\n".join(_unique(lines, max_items=24))[:4_000] or (
        "No inventar hechos, compromisos, precios ni permisos. No actuar fuera del objetivo "
        "de la llamada. Escalar cuando falte información o autoridad."
    )


def _phone_agent(
    *,
    prompt: str,
    fallback_name: str,
    opening: str,
    voice_id: str | None,
    assistant: bool,
) -> LegacyPhoneAgent | None:
    if not prompt.strip():
        return None
    agent_name = _detect_agent_name(prompt, fallback_name)
    short_name = agent_name.split()[0][:80]
    persona, knowledge, required, remainder = _prompt_parts(prompt)
    operating_profile = {
        "import_source": _SOURCE_TAG,
        "funcion_y_mision": (
            "Representar a la persona y administrar llamadas con criterio, continuidad y recados."
            if assistant
            else "Conversar como asesora comercial y lograr el siguiente paso acordado sin presión."
        ),
        "capabilities": remainder[:4_000]
        or (
            "Entender el objetivo, conversar, hacer preguntas, resolver dudas con el contexto "
            "autorizado, resumir acuerdos y dejar próximos pasos."
        ),
        "out_of_scope": _negative_rules(prompt),
        "allowed_actions": (
            "Conversar, tomar recados, aclarar información autorizada, acordar próximos pasos y "
            "escalar a la persona cuando haga falta."
        ),
        "prohibited_actions": _negative_rules(prompt),
        "escalation_rules": (
            "Si falta contexto, identidad del interlocutor, autoridad o un dato verificable, "
            "preguntar o tomar un recado. Nunca improvisar."
        ),
        "success_criteria": (
            "La llamada termina con el objetivo resuelto, un recado claro o un próximo paso "
            "confirmado y resumido."
        ),
    }
    return LegacyPhoneAgent(
        name=short_name,
        agent_name=agent_name,
        persona_prompt=persona,
        default_goal=(
            "Atender o realizar la llamada, comprender el motivo y resolverla o dejar un recado."
            if assistant
            else "Presentar la propuesta asignada y acordar el siguiente paso con la persona."
        ),
        opening_message=redact_secrets(opening).strip()[:700],
        knowledge_context=knowledge,
        required_information=required,
        voice_id=voice_id[:200] if voice_id else None,
        operating_profile=operating_profile,
        handles_inbound=assistant,
        handles_outbound=True,
        is_default=assistant,
        is_inbound_default=assistant,
    )


def _section(text: str, heading: str, next_headings: tuple[str, ...]) -> str:
    match = re.search(rf"(?im)^\s*#*\s*{re.escape(heading)}\s*$", text)
    if not match:
        return ""
    end = len(text)
    for next_heading in next_headings:
        next_match = re.search(
            rf"(?im)^\s*#*\s*{re.escape(next_heading)}\s*$",
            text[match.end() :],
        )
        if next_match:
            end = min(end, match.end() + next_match.start())
    return text[match.end() : end].strip()


def _social_profile(prompt: str, platform: str) -> dict[str, Any]:
    clean = redact_secrets(prompt).strip()
    if not clean:
        return {}
    if platform == "linkedin":
        pillars_line = next(
            (line for line in clean.splitlines() if line.casefold().startswith("escribe sobre ")),
            "",
        )
        formats = ["Post educativo", "Post reactivo", "Post evergreen"]
        visual = _section(clean, "TITULAR DE LA IMAGEN", ("ANTES DE ENTREGAR",))
    else:
        territory = next(
            (line for line in clean.splitlines() if line.casefold().startswith("territorio:")),
            "",
        )
        pillars_line = territory.partition(":")[2]
        formats = ["Postura", "Mini-lección", "Dato", "Predicción", "Verdad simple"]
        visual = ""
    pillars = [
        item.strip(" .")
        for item in re.split(r",|\by\b", pillars_line, flags=re.I)
        if item.strip(" .")
    ][:20]
    return {
        "purpose": clean[:4_000],
        "audience": _section(clean, "PRUEBA DEL SCROLL", ("NOMBRES Y JERGA",))[:4_000],
        "voice": clean[:4_000],
        "content_pillars": pillars,
        "preferred_formats": formats,
        "visual_identity": visual[:4_000],
        "image_rules": visual[:4_000],
        "calls_to_action": "",
        "avoid": clean[4_000:8_000],
        "notes": clean[8_000:12_000],
    }


def _schedule_rrule(item: dict[str, Any]) -> str | None:
    interval = item.get("cada_min")
    if interval is not None:
        try:
            minutes = max(1, min(int(interval), 43_200))
        except (TypeError, ValueError):
            return None
        return f"FREQ=MINUTELY;INTERVAL={minutes}"
    clock = str(item.get("hora") or "").strip()
    match = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", clock)
    if not match:
        return None
    rrule = f"FREQ=DAILY;BYHOUR={int(match.group(1))};BYMINUTE={int(match.group(2))}"
    raw_days = item.get("dow")
    if isinstance(raw_days, list) and raw_days:
        day_names = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
        selected = [
            day_names[int(value)]
            for value in raw_days
            if isinstance(value, int) and 0 <= value < len(day_names)
        ]
        if selected:
            rrule += ";BYDAY=" + ",".join(selected)
    return rrule


def _scan_automations(path: Path) -> list[LegacyAutomation]:
    raw = _read_json(path, max_bytes=1_000_000)
    if not isinstance(raw, list):
        return []
    output: list[LegacyAutomation] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        source_id = str(item.get("id") or "").strip()
        instruction = redact_secrets(str(item.get("orden") or "")).strip()
        rrule = _schedule_rrule(item)
        if not source_id or not instruction or not rrule:
            continue
        output.append(
            LegacyAutomation(
                source_id=source_id,
                name=source_id.replace("_", " ").strip().title()[:160],
                description=(
                    "Importada desde el asistente anterior. Está deshabilitada hasta que la "
                    "persona revise conectores, horario y comportamiento."
                ),
                rrule=rrule,
                instruction=instruction[:8_000],
            )
        )
    return output


def _load_conversation_file(path: Path) -> tuple[LegacyMessage, ...]:
    if not path.is_file() or path.stat().st_size > 20_000_000:
        return ()
    output: list[LegacyMessage] = []
    try:
        lines = path.open(encoding="utf-8", errors="replace")
    except OSError:
        return ()
    with lines:
        for line in lines:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").strip()
            if role not in {"user", "assistant"}:
                continue
            content = item.get("content")
            if not isinstance(content, str):
                continue
            text = redact_secrets(content).strip()[:_MAX_MESSAGE_CHARS]
            if text:
                output.append(
                    LegacyMessage(
                        role=role,
                        text=text,
                        created_at=_parse_time(item.get("t")),
                    )
                )
    return tuple(output)


def _scan_conversations(
    *,
    index_path: Path,
    directory: Path,
    history_path: Path,
    include_tests: bool,
    include_global_history: bool,
) -> tuple[list[LegacyConversation], int]:
    index_raw = _read_json(index_path, max_bytes=5_000_000)
    index: dict[str, dict[str, Any]] = {}
    if isinstance(index_raw, list):
        index = {
            str(item.get("id")): item
            for item in index_raw
            if isinstance(item, dict) and item.get("id")
        }
    conversations: list[LegacyConversation] = []
    skipped = 0
    seen_messages: set[str] = set()
    for path in sorted(directory.glob("*.jsonl")) if directory.is_dir() else []:
        source_id = path.stem
        title = str(index.get(source_id, {}).get("title") or source_id).strip()
        if not include_tests and (
            _TEST_CONVERSATION.match(source_id) or _TEST_CONVERSATION.match(title)
        ):
            skipped += 1
            continue
        messages = _load_conversation_file(path)
        if not messages or not any(item.role == "user" for item in messages):
            skipped += 1
            continue
        for item in messages:
            seen_messages.add(
                hashlib.sha256(f"{item.role}\0{_normalize(item.text)}".encode()).hexdigest()
            )
        fallback = _parse_time(index.get(source_id, {}).get("updated"))
        created_at = min((item.created_at for item in messages), default=fallback)
        updated_at = max((item.created_at for item in messages), default=fallback)
        conversations.append(
            LegacyConversation(
                source_id=source_id,
                title=redact_secrets(title)[:240] or "Conversación importada",
                messages=messages,
                created_at=created_at,
                updated_at=max(updated_at, fallback),
            )
        )

    if include_global_history:
        history = _load_conversation_file(history_path)
        unique_history: list[LegacyMessage] = []
        for item in history:
            digest = hashlib.sha256(f"{item.role}\0{_normalize(item.text)}".encode()).hexdigest()
            if digest in seen_messages:
                continue
            seen_messages.add(digest)
            unique_history.append(item)
        if unique_history:
            conversations.append(
                LegacyConversation(
                    source_id="global-history",
                    title="Historial legado del asistente",
                    messages=tuple(unique_history),
                    created_at=min(item.created_at for item in unique_history),
                    updated_at=max(item.created_at for item in unique_history),
                )
            )
    return conversations, skipped


def _scan_semantic_memory(path: Path) -> list[str]:
    """Recupera el corpus semántico ya curado por el asistente anterior.

    Los vectores heredados se descartan: no conocemos su modelo ni dimensión
    y Edecán debe volver a crear embeddings con su proveedor actual. El
    contenido se depura, redacta y deduplica antes de entrar como memoria
    activa.
    """

    if not path.is_file() or path.stat().st_size > 20_000_000:
        return []
    values: list[str] = []
    try:
        lines = path.open(encoding="utf-8", errors="replace")
    except OSError:
        return []
    with lines:
        for line in lines:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            # Solo declaraciones de la persona entran como hechos activos.
            # Respuestas del asistente pueden contener inferencias o errores y
            # se conservan únicamente en el corpus privado de referencia.
            if not isinstance(item, dict) or item.get("role") != "user":
                continue
            content = item.get("content")
            if not isinstance(content, str):
                continue
            clean = re.sub(r"\s+", " ", redact_secrets(content)).strip()
            if len(clean) < 12 or _TEST_CONVERSATION.match(clean):
                continue
            values.append(clean[:_MAX_FACT_CHARS])
    return _unique(values, max_items=_MAX_FACTS)


def _semantic_corpus_text(path: Path) -> str:
    """Copia utilizable del corpus semántico, sin vectores heredados."""

    if not path.is_file() or path.stat().st_size > 20_000_000:
        return ""
    output: list[str] = []
    try:
        lines = path.open(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    with lines:
        for line in lines:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(item, dict) or item.get("role") not in {"user", "assistant"}:
                continue
            content = item.get("content")
            if not isinstance(content, str) or not content.strip():
                continue
            output.append(
                json.dumps(
                    {
                        "t": str(item.get("t") or ""),
                        "role": item["role"],
                        "content": redact_secrets(content)[:_MAX_MESSAGE_CHARS],
                    },
                    ensure_ascii=False,
                )
            )
    return "\n".join(output)


def _private_persona_style(legacy_persona: str, writing_style: str) -> str:
    """Une identidad privada y estilo sin reemplazar el Core de Edecán."""

    sections: list[str] = []
    if legacy_persona.strip():
        sections.append(
            "Contexto privado heredado del asistente anterior. Úsalo como preferencias y "
            "continuidad de esta persona; sigues siendo Edecán.\n\n" + legacy_persona.strip()
        )
    if writing_style.strip():
        sections.append(
            "Estilo privado de comunicación y escritura de esta persona.\n\n"
            + writing_style.strip()
        )
    return "\n\n".join(sections)[:30_000]


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def _read_env_values(path: Path) -> dict[str, str]:
    """Parser deliberadamente pequeño: no ejecuta shell ni expande variables."""

    if not path.is_file() or path.stat().st_size > 5_000_000:
        raise ValueError("El archivo privado de credenciales no existe o es demasiado grande.")
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,127}", key):
            continue
        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def _resolve_credential_template(value: Any, env: dict[str, str]) -> Any:
    if isinstance(value, dict):
        if set(value).issubset({"env", "default"}) and isinstance(value.get("env"), str):
            return env.get(value["env"], value.get("default", ""))
        return {str(key): _resolve_credential_template(item, env) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_credential_template(item, env) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise ValueError("El mapa privado contiene un tipo no permitido.")


def _validate_secret_templates(value: Any, *, parent_key: str = "") -> None:
    """Evita que el mapa de metadatos termine conteniendo secretos literales."""

    sensitive = re.search(
        r"(?i)(api_?key|secret|password|access_?token|refresh_?token|auth_?token)",
        parent_key,
    )
    if sensitive and isinstance(value, str) and value:
        raise ValueError("Los secretos del mapa deben referenciar una variable de entorno.")
    if isinstance(value, dict):
        if set(value).issubset({"env", "default"}) and isinstance(value.get("env"), str):
            if sensitive and str(value.get("default") or "").strip():
                raise ValueError(
                    "Los secretos del mapa no pueden incluir un valor literal por defecto."
                )
            return
        for key, item in value.items():
            _validate_secret_templates(item, parent_key=str(key))
    elif isinstance(value, list):
        for item in value:
            _validate_secret_templates(item, parent_key=parent_key)


def _optional_expiry(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        stamp = float(text)
        if stamp > 10_000_000_000:
            stamp /= 1_000
        try:
            return datetime.fromtimestamp(stamp, UTC)
        except (OSError, OverflowError, ValueError):
            return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def load_private_credentials(env_path: Path, map_path: Path) -> list[LegacyCredential]:
    """Construye bundles desde una lista blanca privada y declarativa.

    El mapa solo contiene nombres de variables y metadatos; los valores viven
    en el archivo privado. Ninguno de los dos se copia al repositorio ni a los
    documentos de continuidad.
    """

    env = _read_env_values(env_path.expanduser().resolve())
    mapping = _read_json(map_path.expanduser().resolve(), max_bytes=1_000_000)
    if not isinstance(mapping, dict) or mapping.get("schema_version") != 1:
        raise ValueError("El mapa privado de credenciales no tiene schema_version=1.")
    records = mapping.get("credentials")
    if not isinstance(records, list) or len(records) > 50:
        raise ValueError("El mapa privado debe contener como máximo 50 credenciales.")
    output: list[LegacyCredential] = []
    seen: set[tuple[str, str]] = set()
    for raw in records:
        if not isinstance(raw, dict):
            continue
        _validate_secret_templates(raw)
        required = raw.get("required_env") or []
        if not isinstance(required, list) or any(not isinstance(key, str) for key in required):
            raise ValueError("required_env debe ser una lista de nombres.")
        if any(not env.get(key, "").strip() for key in required):
            continue
        resolved = _resolve_credential_template(raw, env)
        connector_key = str(resolved.get("connector_key") or "").strip().lower()
        external = str(resolved.get("external_account_id") or "").strip()
        display_name = str(resolved.get("display_name") or connector_key).strip()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_.:-]{0,79}", connector_key):
            raise ValueError("El mapa privado contiene un connector_key inválido.")
        if not external or len(external) > 500:
            continue
        access_value = resolved.get("access_token")
        if isinstance(access_value, (dict, list)):
            access_token = json.dumps(access_value, ensure_ascii=False, separators=(",", ":"))
        else:
            access_token = str(access_value or "")
        if not access_token:
            continue
        refresh_value = resolved.get("refresh_token")
        refresh_token = str(refresh_value).strip() if refresh_value else None
        raw_scopes = resolved.get("scopes") or []
        scopes = tuple(
            str(scope).strip()[:200]
            for scope in raw_scopes
            if isinstance(scope, (str, int, float)) and str(scope).strip()
        )
        key = (connector_key, external)
        if key in seen:
            continue
        seen.add(key)
        output.append(
            LegacyCredential(
                connector_key=connector_key,
                external_account_id=external,
                display_name=display_name[:200],
                access_token=access_token,
                refresh_token=refresh_token,
                scopes=scopes,
                token_type=str(resolved.get("token_type") or "bearer")[:80],
                expires_at=_optional_expiry(resolved.get("expires_at")),
            )
        )
    return output


def scan_legacy_assistant(
    source: Path,
    *,
    source_map: Path | None = None,
    assistant_agent_name: str = "Asistente",
    sales_agent_name: str = "Agente comercial",
    include_tests: bool = False,
    include_global_history: bool = True,
) -> ImportPlan:
    source = source.expanduser().resolve()
    if not source.is_dir():
        raise ValueError("La carpeta fuente no existe.")
    layout = _load_source_layout(source, source_map)
    paths = layout.paths
    about = _read_text(paths["identity_context"], max_bytes=2_000_000)
    style = _read_text(paths["writing_style"], max_bytes=1_000_000)
    legacy_persona = _read_text(paths["assistant_persona"], max_bytes=1_000_000)
    voice_corpus = _read_text(paths["writing_corpus"], max_bytes=20_000_000)
    assistant_prompt = _read_text(paths["assistant_call_prompt"], max_bytes=1_000_000)
    sales_prompt = _read_text(paths["sales_call_prompt"], max_bytes=1_000_000)
    linkedin = _read_text(paths["linkedin_editorial"], max_bytes=1_000_000)
    x_prompt = _read_text(paths["x_editorial"], max_bytes=1_000_000)
    semantic_corpus = _semantic_corpus_text(paths["semantic_memory"])

    profile_raw = _read_json(paths["structured_profile"], max_bytes=5_000_000)
    raw_facts = (
        profile_raw.get("facts", profile_raw.get("hechos", []))
        if isinstance(profile_raw, dict)
        else []
    )
    profile_facts = _unique(
        (
            str(
                item
                if isinstance(item, str)
                else item.get("text", item.get("texto", ""))
            )
            for item in raw_facts
            if isinstance(item, str) or isinstance(item, dict)
        ),
        max_items=_MAX_FACTS,
    )
    facts = _unique(
        [
            *profile_facts,
            *_scan_semantic_memory(paths["semantic_memory"]),
        ],
        max_items=_MAX_FACTS,
    )
    identity = _parse_identity(about)
    voice_config = _load_voice_config(
        paths["voice_config"],
        python_constants=layout.python_constants,
    )
    opening = str(voice_config.get("assistant_opening_message") or "")
    assistant_voice = str(voice_config.get("assistant_voice_id") or "").strip() or None
    sales_voice = str(voice_config.get("sales_voice_id") or "").strip() or None
    voice_map = voice_config.get("sales_voice_map")
    if sales_voice is None and isinstance(voice_map, dict):
        sales_voice = next(
            (str(value) for value in voice_map.values() if isinstance(value, str) and value),
            None,
        )
    assistant_name = _detect_agent_name(assistant_prompt, assistant_agent_name)
    phone_agents = [
        agent
        for agent in (
            _phone_agent(
                prompt=assistant_prompt,
                fallback_name=assistant_name,
                opening=opening,
                voice_id=assistant_voice,
                assistant=True,
            ),
            _phone_agent(
                prompt=sales_prompt,
                fallback_name=sales_agent_name,
                opening=f"Hola, habla {sales_agent_name}.",
                voice_id=sales_voice,
                assistant=False,
            ),
        )
        if agent is not None
    ]
    conversations, skipped = _scan_conversations(
        index_path=paths["conversation_index"],
        directory=paths["conversation_directory"],
        history_path=paths["global_history"],
        include_tests=include_tests,
        include_global_history=include_global_history,
    )
    private_documents = {
        name: content
        for name, content in {
            "identity-and-context.txt": about,
            "writing-style.md": style,
            "legacy-persona.md": legacy_persona,
            "writing-corpus.txt": voice_corpus,
            "semantic-memory-corpus.jsonl": semantic_corpus,
            "phone-assistant-prompt.md": assistant_prompt,
            "phone-sales-prompt.md": sales_prompt,
            "linkedin-editorial.md": linkedin,
            "x-editorial.md": x_prompt,
        }.items()
        if content
    }
    social_profiles = {
        platform: profile
        for platform, profile in (
            ("linkedin", _social_profile(linkedin, "linkedin")),
            ("x", _social_profile(x_prompt, "x")),
        )
        if profile
    }
    return ImportPlan(
        facts=facts,
        identity=identity,
        profile_summary=_profile_summary(identity, facts),
        profile_lists=_classify_profile(facts),
        persona_style=_private_persona_style(legacy_persona, style),
        social_profiles=social_profiles,
        phone_agents=phone_agents,
        automations=_scan_automations(paths["schedules"]),
        conversations=conversations,
        private_documents=private_documents,
        skipped_conversations=skipped,
    )


def _memory_kind(content: str) -> str:
    if _PREFERENCE_WORDS.search(content):
        return "preference"
    if _COMPANY_WORDS.search(content) or _RELATION_WORDS.search(content):
        return "entity"
    if re.search(r"(?i)\b(ocurri[oó]|pas[oó]|fecha|naci[oó]|aprobado|termin[oó])\b", content):
        return "event"
    return "fact"


def _importance(content: str) -> float:
    if re.search(r"(?i)\b(nombre|naci|hija|hijo|empresa|direcci[oó]n|salud|alerg)\b", content):
        return 0.9
    if _PREFERENCE_WORDS.search(content):
        return 0.8
    return 0.7


def _vector_literal(values: list[float]) -> str:
    """Serializa un embedding para asyncpg sin registrar un codec de pgvector."""

    return "[" + ",".join(repr(float(value)) for value in values) + "]"


async def _merge_profile(
    connection: asyncpg.Connection,
    *,
    plan: ImportPlan,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
) -> None:
    row = await connection.fetchrow(
        "SELECT resumen, datos, version FROM user_profiles WHERE tenant_id=$1 AND user_id=$2",
        tenant_id,
        user_id,
    )
    current_data = _json_object(row["datos"]) if row else {}
    current_identity = (
        dict(current_data.get("identidad") or {})
        if isinstance(current_data.get("identidad"), dict)
        else {}
    )
    for key, value in plan.identity.items():
        if value and not current_identity.get(key):
            current_identity[key] = value
    merged: dict[str, Any] = {"identidad": current_identity}
    for key in ("gustos", "proyectos", "metas", "relaciones", "empresas", "habitos"):
        existing = current_data.get(key)
        existing_values = existing if isinstance(existing, list) else []
        merged[key] = _unique([*existing_values, *plan.profile_lists.get(key, [])])
    summary = str(row["resumen"] or "").strip() if row else ""
    if not summary:
        summary = plan.profile_summary
    version = int(row["version"] or 0) + 1 if row else 1
    await connection.execute(
        """
        INSERT INTO user_profiles
            (id, tenant_id, user_id, resumen, datos, version, created_at, updated_at)
        VALUES ($1,$2,$3,$4,$5::jsonb,$6,now(),now())
        ON CONFLICT (tenant_id,user_id) DO UPDATE
        SET resumen=EXCLUDED.resumen, datos=EXCLUDED.datos,
            version=EXCLUDED.version, updated_at=now()
        """,
        _stable_id("profile", f"{tenant_id}:{user_id}"),
        tenant_id,
        user_id,
        summary[:500],
        json.dumps(merged, ensure_ascii=False),
        version,
    )


async def _merge_persona(
    connection: asyncpg.Connection,
    *,
    style: str,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
) -> None:
    if not style:
        return
    row = await connection.fetchrow(
        "SELECT id, instrucciones FROM personas WHERE tenant_id=$1 AND user_id=$2 "
        "ORDER BY updated_at DESC LIMIT 1",
        tenant_id,
        user_id,
    )
    block = f"\n\n[{_PRIVATE_IMPORT_MARKER}]\n{style.strip()}\n[/{_PRIVATE_IMPORT_MARKER}]"
    if row:
        instructions = str(row["instrucciones"] or "")
        pattern = re.compile(
            rf"\n*\[{_PRIVATE_IMPORT_MARKER}\].*?\[/{_PRIVATE_IMPORT_MARKER}\]",
            re.S,
        )
        instructions = pattern.sub("", instructions).rstrip() + block
        await connection.execute(
            "UPDATE personas SET instrucciones=$1, updated_at=now() WHERE id=$2 AND tenant_id=$3",
            instructions,
            row["id"],
            tenant_id,
        )
        return
    await connection.execute(
        """
        INSERT INTO personas (
            id,tenant_id,user_id,nombre_asistente,idioma,tono,formalidad,emojis,
            instrucciones,rasgos,memoria_activada,voice_id,estilo_relacion,
            adulto_confirmado,consentimiento_romantico,created_at,updated_at
        ) VALUES (
            $1,$2,$3,'Edecán','es','cálido y profesional',1,false,$4,'[]'::jsonb,
            true,NULL,'profesional',false,false,now(),now()
        )
        """,
        _stable_id("persona", f"{tenant_id}:{user_id}"),
        tenant_id,
        user_id,
        block.strip(),
    )


def _local_master_key(data_dir: Path) -> str:
    secrets_path = data_dir / "secrets.json"
    try:
        payload = json.loads(secrets_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("No se pudo leer el vault privado de esta instalación.") from exc
    key = payload.get("LOCAL_MASTER_KEY") if isinstance(payload, dict) else None
    if not isinstance(key, str) or not key:
        raise ValueError("La instalación local no tiene una clave maestra válida.")
    try:
        Fernet(key.encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise ValueError("La clave maestra de la instalación local no es válida.") from exc
    return key


async def _tenant_data_key(
    connection: asyncpg.Connection,
    *,
    tenant_id: uuid.UUID,
    master_key: str,
) -> tuple[bytes, int]:
    row = await connection.fetchrow(
        "SELECT encrypted_data_key,kms_key_id,version FROM tenant_keys WHERE tenant_id=$1",
        tenant_id,
    )
    fernet = Fernet(master_key.encode("utf-8"))
    if row is None:
        data_key = AESGCM.generate_key(bit_length=256)
        wrapped = fernet.encrypt(data_key)
        await connection.execute(
            """
            INSERT INTO tenant_keys (
                id,tenant_id,encrypted_data_key,kms_key_id,version,created_at,updated_at
            ) VALUES ($1,$2,$3,NULL,1,now(),now())
            ON CONFLICT (tenant_id) DO NOTHING
            """,
            _stable_id("tenant-key", str(tenant_id)),
            tenant_id,
            wrapped,
        )
        row = await connection.fetchrow(
            "SELECT encrypted_data_key,kms_key_id,version FROM tenant_keys WHERE tenant_id=$1",
            tenant_id,
        )
    if row is None:
        raise ValueError("No se pudo preparar el vault privado.")
    if row["kms_key_id"]:
        raise ValueError("Este tenant usa AWS KMS; el importador local no puede desenvolverlo.")
    try:
        return fernet.decrypt(bytes(row["encrypted_data_key"])), int(row["version"])
    except InvalidToken as exc:
        raise ValueError("La clave maestra local no coincide con el vault existente.") from exc


async def _import_credentials(
    connection: asyncpg.Connection,
    *,
    credentials: list[LegacyCredential],
    tenant_id: uuid.UUID,
    data_dir: Path,
    replace_existing: bool,
) -> tuple[int, int]:
    if not credentials:
        return 0, 0
    master_key = _local_master_key(data_dir)
    data_key, key_version = await _tenant_data_key(
        connection,
        tenant_id=tenant_id,
        master_key=master_key,
    )
    singleton_keys = {"llm", "voice_stt", "voice_tts", "images", "search", "studio"}
    imported = 0
    skipped = 0
    for credential in credentials:
        account = await connection.fetchrow(
            """
            SELECT id,external_account_id
            FROM connector_accounts
            WHERE tenant_id=$1 AND connector_key=$2
              AND ($3 OR external_account_id=$4)
            ORDER BY created_at
            LIMIT 1
            """,
            tenant_id,
            credential.connector_key,
            credential.connector_key in singleton_keys
            or credential.connector_key.endswith("__app_config"),
            credential.external_account_id,
        )
        account_id = (
            account["id"]
            if account is not None
            else _stable_id(
                "connector-account",
                f"{tenant_id}:{credential.connector_key}:{credential.external_account_id}",
            )
        )
        token_exists = (
            await connection.fetchval(
                "SELECT EXISTS(SELECT 1 FROM oauth_tokens "
                "WHERE tenant_id=$1 AND connector_account_id=$2)",
                tenant_id,
                account_id,
            )
            if account is not None
            else False
        )
        if token_exists and not replace_existing:
            skipped += 1
            continue
        if account is None:
            await connection.execute(
                """
                INSERT INTO connector_accounts (
                    id,tenant_id,connector_key,external_account_id,display_name,
                    status,scopes,created_at,updated_at
                ) VALUES ($1,$2,$3,$4,$5,'active',$6::jsonb,now(),now())
                ON CONFLICT (tenant_id,connector_key,external_account_id) DO NOTHING
                """,
                account_id,
                tenant_id,
                credential.connector_key,
                credential.external_account_id,
                credential.display_name,
                json.dumps(list(credential.scopes)),
            )
        expires_at = credential.expires_at
        plaintext = json.dumps(
            {
                "access_token": credential.access_token,
                "refresh_token": credential.refresh_token,
                "expires_at": expires_at.isoformat() if expires_at else None,
                "scopes": list(credential.scopes),
                "token_type": credential.token_type,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        nonce = os.urandom(12)
        ciphertext = AESGCM(data_key).encrypt(nonce, plaintext, None)
        await connection.execute(
            """
            INSERT INTO oauth_tokens (
                id,tenant_id,connector_account_id,ciphertext,nonce,key_version,
                expires_at,created_at,updated_at
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,now(),now())
            ON CONFLICT (connector_account_id) DO UPDATE
            SET ciphertext=EXCLUDED.ciphertext,nonce=EXCLUDED.nonce,
                key_version=EXCLUDED.key_version,expires_at=EXCLUDED.expires_at,
                updated_at=now()
            """,
            _stable_id("oauth-token", str(account_id)),
            tenant_id,
            account_id,
            ciphertext,
            nonce,
            key_version,
            expires_at,
        )
        imported += 1
    return imported, skipped


async def _import_plan(
    connection: asyncpg.Connection,
    *,
    plan: ImportPlan,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    owner_email: str,
    data_dir: Path,
    replace_existing_credentials: bool,
) -> dict[str, int]:
    owner = await connection.fetchrow(
        """
        SELECT u.email
        FROM users u
        JOIN memberships m ON m.user_id=u.id AND m.tenant_id=$1
        WHERE u.id=$2 AND m.role IN ('owner','admin')
        LIMIT 1
        """,
        tenant_id,
        user_id,
    )
    if owner is None or str(owner["email"]).casefold() != owner_email.casefold():
        raise ValueError("El tenant, usuario y correo confirmado no corresponden al dueño local.")

    stats = {
        "memory_inserted": 0,
        "memory_existing": 0,
        "phone_agents_upserted": 0,
        "phone_agents_removed": 0,
        "social_profiles_upserted": 0,
        "automations_upserted_disabled": 0,
        "conversations_upserted": 0,
        "messages_inserted": 0,
        "credentials_imported": 0,
        "credentials_existing": 0,
    }
    existing_rows = await connection.fetch(
        "SELECT content FROM memory_items WHERE tenant_id=$1 AND user_id=$2 "
        "AND superseded_at IS NULL",
        tenant_id,
        user_id,
    )
    existing_facts = {_normalize(str(row["content"])) for row in existing_rows}
    pending_facts = [fact for fact in plan.facts if _normalize(fact) not in existing_facts]
    embedder = HashEmbedder(dim=DEFAULT_EMBEDDINGS_DIM)
    embeddings = await embedder.embed(pending_facts)
    for fact, embedding in zip(pending_facts, embeddings, strict=True):
        normalized = _normalize(fact)
        await connection.execute(
            """
            INSERT INTO memory_items (
                id,tenant_id,user_id,kind,content,embedding,importance,source,
                superseded_at,superseded_by,created_at,updated_at
            ) VALUES ($1,$2,$3,$4,$5,$6::vector,$7,$8,NULL,NULL,now(),now())
            ON CONFLICT (id) DO UPDATE
            SET kind=EXCLUDED.kind,content=EXCLUDED.content,embedding=EXCLUDED.embedding,
                importance=EXCLUDED.importance,
                source=EXCLUDED.source,updated_at=now()
            """,
            _stable_id("memory", f"{tenant_id}:{user_id}:{normalized}"),
            tenant_id,
            user_id,
            _memory_kind(fact),
            fact,
            _vector_literal(embedding),
            _importance(fact),
            _SOURCE_TAG,
        )
        existing_facts.add(normalized)
        stats["memory_inserted"] += 1
    stats["memory_existing"] = len(plan.facts) - len(pending_facts)

    await _merge_profile(
        connection,
        plan=plan,
        tenant_id=tenant_id,
        user_id=user_id,
    )
    await _merge_persona(
        connection,
        style=plan.persona_style,
        tenant_id=tenant_id,
        user_id=user_id,
    )

    for platform, config in plan.social_profiles.items():
        current_version = await connection.fetchval(
            "SELECT version FROM social_editorial_profiles "
            "WHERE tenant_id=$1 AND user_id=$2 AND platform=$3",
            tenant_id,
            user_id,
            platform,
        )
        await connection.execute(
            """
            INSERT INTO social_editorial_profiles
                (id,tenant_id,user_id,platform,config,version,created_at,updated_at)
            VALUES ($1,$2,$3,$4,$5::jsonb,$6,now(),now())
            ON CONFLICT (tenant_id,user_id,platform) DO UPDATE
            SET config=EXCLUDED.config,version=EXCLUDED.version,updated_at=now()
            """,
            _stable_id("social-profile", f"{tenant_id}:{user_id}:{platform}"),
            tenant_id,
            user_id,
            platform,
            json.dumps(config, ensure_ascii=False),
            int(current_version or 0) + 1,
        )
        stats["social_profiles_upserted"] += 1

    planned_phone_agent_ids = {
        _stable_id("phone-agent", f"{tenant_id}:{user_id}:{agent.name.casefold()}")
        for agent in plan.phone_agents
    }
    # Compatibilidad con la primera versión del importador: si el prompt no
    # declaraba nombre, usaba el alias genérico "Asistente". Su UUID es
    # determinista y solo puede haber sido creado por este importador. Cuando
    # el dueño proporciona el nombre real, retiramos ese alias para no dejar
    # dos identidades ni bloquear el agente predeterminado correcto.
    previous_fallback_id = _stable_id(
        "phone-agent",
        f"{tenant_id}:{user_id}:asistente",
    )
    if previous_fallback_id not in planned_phone_agent_ids:
        status = await connection.execute(
            "DELETE FROM phone_agent_templates WHERE id=$1 AND tenant_id=$2 AND user_id=$3",
            previous_fallback_id,
            tenant_id,
            user_id,
        )
        if status.endswith("1"):
            stats["phone_agents_removed"] += 1

    existing_default = await connection.fetchval(
        "SELECT id FROM phone_agent_templates WHERE tenant_id=$1 AND user_id=$2 "
        "AND is_default LIMIT 1",
        tenant_id,
        user_id,
    )
    existing_inbound = await connection.fetchval(
        "SELECT id FROM phone_agent_templates WHERE tenant_id=$1 AND user_id=$2 "
        "AND is_inbound_default LIMIT 1",
        tenant_id,
        user_id,
    )
    for agent in plan.phone_agents:
        agent_id = _stable_id(
            "phone-agent",
            f"{tenant_id}:{user_id}:{agent.name.casefold()}",
        )
        is_default = agent.is_default and existing_default in {None, agent_id}
        is_inbound_default = agent.is_inbound_default and existing_inbound in {None, agent_id}
        await connection.execute(
            """
            INSERT INTO phone_agent_templates (
                id,tenant_id,user_id,name,agent_name,persona_prompt,default_goal,
                opening_message,knowledge_context,required_information,voice_id,
                operating_profile,handles_inbound,handles_outbound,is_default,
                is_inbound_default,created_at,updated_at
            ) VALUES (
                $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12::jsonb,$13,$14,$15,$16,now(),now()
            )
            ON CONFLICT (tenant_id,user_id,name) DO UPDATE
            SET agent_name=EXCLUDED.agent_name,persona_prompt=EXCLUDED.persona_prompt,
                default_goal=EXCLUDED.default_goal,opening_message=EXCLUDED.opening_message,
                knowledge_context=EXCLUDED.knowledge_context,
                required_information=EXCLUDED.required_information,voice_id=EXCLUDED.voice_id,
                operating_profile=EXCLUDED.operating_profile,
                handles_inbound=EXCLUDED.handles_inbound,
                handles_outbound=EXCLUDED.handles_outbound,
                is_default=EXCLUDED.is_default,
                is_inbound_default=EXCLUDED.is_inbound_default,updated_at=now()
            """,
            agent_id,
            tenant_id,
            user_id,
            agent.name,
            agent.agent_name,
            agent.persona_prompt,
            agent.default_goal,
            agent.opening_message,
            agent.knowledge_context,
            agent.required_information,
            agent.voice_id,
            json.dumps(agent.operating_profile, ensure_ascii=False),
            agent.handles_inbound,
            agent.handles_outbound,
            is_default,
            is_inbound_default,
        )
        stats["phone_agents_upserted"] += 1

    for automation in plan.automations:
        await connection.execute(
            """
            INSERT INTO automations (
                id,tenant_id,user_id,nombre,descripcion,trigger,accion,enabled,
                next_run_at,last_run_at,created_at,updated_at
            ) VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7::jsonb,false,NULL,NULL,now(),now())
            ON CONFLICT (id) DO UPDATE
            SET nombre=EXCLUDED.nombre,descripcion=EXCLUDED.descripcion,
                trigger=EXCLUDED.trigger,accion=EXCLUDED.accion,
                enabled=false,next_run_at=NULL,updated_at=now()
            """,
            _stable_id(
                "automation",
                f"{tenant_id}:{user_id}:{automation.source_id}",
            ),
            tenant_id,
            user_id,
            automation.name,
            automation.description,
            json.dumps({"kind": "schedule", "rrule": automation.rrule}),
            json.dumps(
                {
                    "kind": "agent_instruction",
                    "instruccion": automation.instruction,
                    "agente": None,
                    "import_source": _SOURCE_TAG,
                },
                ensure_ascii=False,
            ),
        )
        stats["automations_upserted_disabled"] += 1

    for conversation in plan.conversations:
        conversation_id = _stable_id(
            "conversation",
            f"{tenant_id}:{user_id}:{conversation.source_id}",
        )
        await connection.execute(
            """
            INSERT INTO conversations (
                id,tenant_id,user_id,title,title_source,channel,created_at,updated_at
            ) VALUES ($1,$2,$3,$4,'legacy','web',$5,$6)
            ON CONFLICT (id) DO UPDATE
            SET title=EXCLUDED.title,title_source='legacy',updated_at=EXCLUDED.updated_at
            """,
            conversation_id,
            tenant_id,
            user_id,
            conversation.title,
            conversation.created_at,
            conversation.updated_at,
        )
        stats["conversations_upserted"] += 1
        for index, message in enumerate(conversation.messages):
            message_id = _stable_id(
                "message",
                f"{conversation_id}:{index}:{message.role}:{hashlib.sha256(message.text.encode()).hexdigest()}",
            )
            status = await connection.execute(
                """
                INSERT INTO messages (
                    id,tenant_id,conversation_id,role,content,tool_calls,
                    tokens_in,tokens_out,created_at,updated_at
                ) VALUES ($1,$2,$3,$4,$5::jsonb,NULL,0,0,$6,$6)
                ON CONFLICT (id) DO NOTHING
                """,
                message_id,
                tenant_id,
                conversation_id,
                message.role,
                json.dumps({"text": message.text}, ensure_ascii=False),
                message.created_at,
            )
            if status.endswith("1"):
                stats["messages_inserted"] += 1
    imported_credentials, existing_credentials = await _import_credentials(
        connection,
        credentials=plan.credentials,
        tenant_id=tenant_id,
        data_dir=data_dir,
        replace_existing=replace_existing_credentials,
    )
    stats["credentials_imported"] = imported_credentials
    stats["credentials_existing"] = existing_credentials
    return stats


def _chmod_best_effort(path: Path, mode: int) -> None:
    """`os.chmod` de "mejor esfuerzo": nunca revienta el import privado.

    En NTFS `chmod` solo puede tocar el bit de solo-lectura (nunca replica
    0600/0700 de verdad -- el perfil de permisos de Windows es la protección
    real ahí, mismo criterio que `edecan_local.runtime._ensure_local_secrets`
    y `edecan_local.edge_continuity._read_private_text`) y además puede
    lanzar `OSError` si un antivirus/indexador tiene el archivo recién escrito
    abierto un instante. Cualquiera de los dos casos es un no-op aceptable:
    lo que importa es que el corpus privado se haya escrito, no que el bit
    de permisos POSIX haya podido aplicarse."""
    try:
        os.chmod(path, mode)
    except OSError:
        logger.warning("No se pudo aplicar permisos %o a %s.", mode, path, exc_info=True)


def _write_private_documents(
    data_dir: Path,
    documents: dict[str, str],
    *,
    phone_agent_names: list[str] | None = None,
) -> int:
    root = data_dir.expanduser().resolve() / "private-imports" / "legacy-assistant"
    root.mkdir(parents=True, exist_ok=True)
    _chmod_best_effort(root.parent, 0o700)
    _chmod_best_effort(root, 0o700)
    written = 0
    for name, content in documents.items():
        path = root / Path(name).name
        path.write_text(redact_secrets(content), encoding="utf-8")
        _chmod_best_effort(path, 0o600)
        written += 1
    manifest = {
        "schema_version": 1,
        "kind": "edecan.private-assistant-import",
        "documents": sorted(documents),
        "phone_agent_names": phone_agent_names or [],
        "updated_at": datetime.now(UTC).isoformat(),
    }
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    _chmod_best_effort(manifest_path, 0o600)
    return written


def _previous_phone_agent_names(data_dir: Path) -> tuple[str | None, str | None]:
    """Recupera overrides ya aprobados sin leer contenido privado del corpus.

    Así, repetir el comando sin volver a escribir los nombres no introduce
    perfiles genéricos adicionales. Manifiestos anteriores siguen siendo
    compatibles y devuelven ``(None, None)``.
    """

    manifest_path = (
        data_dir.expanduser().resolve()
        / "private-imports"
        / "legacy-assistant"
        / "manifest.json"
    )
    payload = _read_json(manifest_path, max_bytes=64_000)
    if not isinstance(payload, dict):
        return None, None
    names = payload.get("phone_agent_names")
    if not isinstance(names, list):
        return None, None
    clean = [
        str(name).strip()
        for name in names[:2]
        if isinstance(name, str) and str(name).strip()
    ]
    return (
        clean[0] if clean else None,
        clean[1] if len(clean) > 1 else None,
    )


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


async def apply_import(
    *,
    plan: ImportPlan,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    owner_email: str,
    data_dir: Path,
    database_url: str | None,
    pg_socket: Path | None,
    replace_existing_credentials: bool = False,
) -> dict[str, int]:
    repo_root = Path(__file__).resolve().parents[3]
    resolved_data_dir = data_dir.expanduser().resolve()
    if _inside(resolved_data_dir, repo_root):
        raise ValueError("La carpeta privada de datos debe quedar fuera del repositorio.")
    if database_url:
        connection = await asyncpg.connect(database_url)
    elif pg_socket:
        connection = await asyncpg.connect(
            user="postgres",
            database="postgres",
            host=str(pg_socket.expanduser().resolve()),
        )
    else:
        raise ValueError("Falta --database-url o --pg-socket.")
    try:
        async with connection.transaction():
            stats = await _import_plan(
                connection,
                plan=plan,
                tenant_id=tenant_id,
                user_id=user_id,
                owner_email=owner_email,
                data_dir=resolved_data_dir,
                replace_existing_credentials=replace_existing_credentials,
            )
    finally:
        await connection.close()
    stats["private_documents_written"] = _write_private_documents(
        resolved_data_dir,
        plan.private_documents,
        phone_agent_names=[agent.name for agent in plan.phone_agents],
    )
    return stats


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Importa datos privados de un asistente anterior al Edecán local."
    )
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument(
        "--source-map",
        type=Path,
        help=(
            "Mapa JSON privado, fuera del repositorio, que traduce claves canónicas "
            "a rutas relativas dentro de --source."
        ),
    )
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--tenant-id", required=True, type=uuid.UUID)
    parser.add_argument("--user-id", required=True, type=uuid.UUID)
    parser.add_argument("--confirm-owner-email", required=True)
    parser.add_argument("--assistant-agent-name")
    parser.add_argument("--sales-agent-name")
    parser.add_argument(
        "--import-credentials",
        action="store_true",
        help="Importa solo credenciales declaradas por --credential-map desde --credentials-env.",
    )
    parser.add_argument("--credentials-env", type=Path)
    parser.add_argument("--credential-map", type=Path)
    parser.add_argument(
        "--replace-existing-credentials",
        action="store_true",
        help="Reemplaza credenciales ya cifradas. Por defecto se conservan.",
    )
    parser.add_argument("--database-url", default=os.getenv("EDECAN_DATABASE_URL"))
    parser.add_argument("--pg-socket", type=Path)
    parser.add_argument("--include-test-conversations", action="store_true")
    parser.add_argument("--no-global-history", action="store_true")
    parser.add_argument("--apply", action="store_true")
    return parser


async def _async_main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    previous_assistant_name, previous_sales_name = _previous_phone_agent_names(
        args.data_dir
    )
    plan = scan_legacy_assistant(
        args.source,
        source_map=args.source_map,
        assistant_agent_name=(
            args.assistant_agent_name or previous_assistant_name or "Asistente"
        ),
        sales_agent_name=(
            args.sales_agent_name or previous_sales_name or "Agente comercial"
        ),
        include_tests=args.include_test_conversations,
        include_global_history=not args.no_global_history,
    )
    if args.import_credentials:
        if args.credentials_env is None or args.credential_map is None:
            raise ValueError("--import-credentials exige --credentials-env y --credential-map.")
        plan.credentials = load_private_credentials(
            args.credentials_env,
            args.credential_map,
        )
    result: dict[str, Any] = {
        "mode": "apply" if args.apply else "dry-run",
        "planned": plan.public_counts(),
    }
    if args.apply:
        result["applied"] = await apply_import(
            plan=plan,
            tenant_id=args.tenant_id,
            user_id=args.user_id,
            owner_email=args.confirm_owner_email,
            data_dir=args.data_dir,
            database_url=args.database_url,
            pg_socket=args.pg_socket,
            replace_existing_credentials=args.replace_existing_credentials,
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


def main() -> None:
    try:
        raise SystemExit(asyncio.run(_async_main()))
    except (ValueError, OSError, asyncpg.PostgresError) as exc:
        print(
            json.dumps(
                {"ok": False, "error": redact_secrets(str(exc))[:500]},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
