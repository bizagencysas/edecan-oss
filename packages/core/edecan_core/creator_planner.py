"""Planner determinista para solicitudes de creación compuestas.

No redacta ni ejecuta: convierte lenguaje cotidiano o una lista explícita de
formatos en un :class:`CreationPlan` estable y acotado. La generación real se
mantiene en una Tool, bajo los mismos gates del agente.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence

from edecan_schemas import ArtifactKind, CreationDeliverable, CreationPlan

_ALIASES: dict[str, ArtifactKind] = {
    "app": "app",
    "aplicacion": "app",
    "aplicación": "app",
    "doc": "docx",
    "documento": "docx",
    "docx": "docx",
    "landing": "website",
    "pagina": "website",
    "página": "website",
    "pagina_web": "website",
    "pdf": "pdf",
    "post": "post",
    "posts": "post",
    "powerpoint": "pptx",
    "ppt": "pptx",
    "pptx": "pptx",
    "presentacion": "pptx",
    "presentación": "pptx",
    "scaffold": "app",
    "sitio": "website",
    "sitio_web": "website",
    "web": "website",
    "website": "website",
    "word": "docx",
}

_PATTERNS: tuple[tuple[ArtifactKind, tuple[str, ...]], ...] = (
    ("post", (r"\bposts?\b", r"\bpublicacion(?:es)?\b", r"\bcopy\b")),
    ("docx", (r"\bdocx?\b", r"\bword\b", r"\bdocumentos?\b")),
    ("pdf", (r"\bpdfs?\b",)),
    (
        "pptx",
        (r"\bpptx?\b", r"\bpowerpoint\b", r"\bpresentacion(?:es)?\b", r"\bdiapositivas?\b"),
    ),
    (
        "website",
        (
            r"\blanding(?: page)?\b",
            r"\bpaginas? web\b",
            r"\bsitios? web\b",
            r"\bweb\b",
            r"\bwebsite\b",
        ),
    ),
    (
        "app",
        (
            r"\bapps?\b",
            r"\baplicacion(?:es)?\b",
            r"\bscaffolds?\b",
        ),
    ),
)

_IMPERATIVE_PREFIX = re.compile(
    r"^(?:por favor\s+)?(?:cr[eé]a(?:me)?|haz(?:me)?|genera|redacta|construye|arma)\s+",
    re.IGNORECASE,
)


def normalize_creator_text(value: str) -> str:
    """Minúsculas ASCII y espacios estables para matching, sin mutar el original."""
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", normalized.lower()).strip()


def normalize_artifact_kind(value: str) -> ArtifactKind:
    """Normaliza un alias público o falla explícitamente si no es un formato real."""
    normalized = normalize_creator_text(value).replace(" ", "_")
    kind = _ALIASES.get(normalized)
    if kind is None:
        supported = "post, docx/Word, pdf, pptx/PowerPoint, website y app"
        raise ValueError(f"Formato no soportado: {value!r}. Disponibles: {supported}.")
    return kind


def detect_artifact_kinds(request: str) -> list[ArtifactKind]:
    """Detecta todos los formatos mencionados, conservando el orden de la frase."""
    normalized = normalize_creator_text(request)
    matches: list[tuple[int, ArtifactKind]] = []
    for kind, patterns in _PATTERNS:
        for pattern in patterns:
            match = re.search(pattern, normalized)
            if match is not None:
                matches.append((match.start(), kind))
                break

    # "documento PDF" nombra un solo formato; "documento y PDF" sí pide dos.
    if re.search(r"\bdocumentos?\s+(?:en\s+)?pdf\b", normalized) and not re.search(
        r"\b(?:word|docx?)\b", normalized
    ):
        matches = [(pos, kind) for pos, kind in matches if kind != "docx"]

    ordered: list[ArtifactKind] = []
    for _position, kind in sorted(matches, key=lambda item: item[0]):
        if kind not in ordered:
            ordered.append(kind)
    return ordered


def derive_creation_title(request: str) -> str:
    """Título corto y determinista cuando el usuario no dio uno explícito."""
    compact = re.sub(r"\s+", " ", request).strip()
    compact = _IMPERATIVE_PREFIX.sub("", compact).strip(" .,:;-")
    if not compact:
        return "Creación de Edecán"
    title = compact[:96].rstrip()
    return title[0].upper() + title[1:]


def plan_creation(
    request: str,
    *,
    requested_formats: Sequence[str] | None = None,
    title: str | None = None,
) -> CreationPlan:
    """Construye un plan acotado; nunca promete un formato desconocido."""
    clean_request = re.sub(r"\s+", " ", str(request or "")).strip()
    if not clean_request:
        raise ValueError("La solicitud de creación no puede estar vacía.")

    kinds: list[ArtifactKind] = []
    if requested_formats:
        for raw_kind in requested_formats:
            kind = normalize_artifact_kind(str(raw_kind))
            if kind not in kinds:
                kinds.append(kind)
    else:
        kinds = detect_artifact_kinds(clean_request)

    # Una petición de redacción sin formato explícito produce un post privado,
    # el artefacto de menor sorpresa y totalmente reversible.
    if not kinds:
        kinds = ["post"]

    clean_title = re.sub(r"\s+", " ", str(title or "")).strip()[:300]
    clean_title = clean_title or derive_creation_title(clean_request)
    return CreationPlan(
        request=clean_request,
        title=clean_title,
        deliverables=[CreationDeliverable(kind=kind, title=clean_title) for kind in kinds],
    )
