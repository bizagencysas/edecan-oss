"""Entity resolution: alias y resolución de entidades (§57).

Permite que "Acme", "Data Cred", "la empresa", "mi fintech" se
resuelvan a la misma entidad según el contexto.

No persiste nada por sí mismo — es un resolvedor en memoria que usa
la memoria semántica y el contexto de la conversación.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class Entity:
    id: str
    canonical_name: str
    aliases: list[str] = field(default_factory=list)
    entity_type: str = "generic"
    metadata: dict = field(default_factory=dict)


class EntityResolver:
    """Resuelve referencias a entidades usando aliases y contexto."""

    def __init__(self) -> None:
        self._entities: dict[str, Entity] = {}

    def register(self, entity: Entity) -> None:
        self._entities[entity.id] = entity

    def resolve(self, text: str, context: str = "") -> list[Entity]:
        """Encuentra entidades mencionadas en el texto."""
        text_lower = text.lower()
        context_lower = context.lower()
        found: list[Entity] = []
        for entity in self._entities.values():
            names = [entity.canonical_name.lower()] + [a.lower() for a in entity.aliases]
            for name in names:
                if name in text_lower:
                    found.append(entity)
                    break
                if len(name) > 4 and name in context_lower:
                    found.append(entity)
                    break
        return found

    def resolve_pronoun(self, text: str, context: str = "") -> Entity | None:
        """Resuelve pronombres referenciales ('la empresa', 'el proyecto')."""
        pronoun_patterns = [
            (re.compile(r"\bla\s+empresa\b", re.I), "company"),
            (re.compile(r"\bel\s+proyecto\b", re.I), "project"),
            (re.compile(r"\bla\s+app\b", re.I), "app"),
            (re.compile(r"\bmi\s+fintech\b", re.I), "company"),
        ]
        for pattern, entity_type in pronoun_patterns:
            if pattern.search(text):
                for entity in self._entities.values():
                    if entity.entity_type == entity_type:
                        return entity
        return None

    def add_alias(self, entity_id: str, alias: str) -> None:
        if entity_id in self._entities:
            self._entities[entity_id].aliases.append(alias)

    def all_entities(self) -> list[Entity]:
        return list(self._entities.values())
