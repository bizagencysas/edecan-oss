"""`LiveProfile` — perfil estructurado del usuario (ROADMAP_V2.md §7.4, §21,
dueño WP-V2-01; consumido por WP-V2-13).

Espeja la tabla `user_profiles` (`resumen text`, `datos jsonb`, `version
int`) pero, igual que `PersonaConfig` frente a la tabla `personas`, es el
objeto de VALOR (sin `id`/`tenant_id`/timestamps) — WP-V2-13 lo construye por
consolidación (mismo job que `memory_consolidate`, o uno análogo) y lo inyecta
en el system prompt vía memoria (ARCHITECTURE.md §10.7:
`ToolContext.extras`), además de exponerlo en `GET/PUT /v1/perfil`.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ProfileData(BaseModel):
    """`datos` de `user_profiles` — listas libres de texto, cada una
    acumulada/depurada por la consolidación de memoria. Ninguna lista tiene
    un tamaño máximo pinned aquí; WP-V2-13 decide cómo podar."""

    gustos: list[str] = Field(default_factory=list)
    proyectos: list[str] = Field(default_factory=list)
    metas: list[str] = Field(default_factory=list)
    relaciones: list[str] = Field(default_factory=list)
    empresas: list[str] = Field(default_factory=list)
    habitos: list[str] = Field(default_factory=list)


class LiveProfile(BaseModel):
    """`resumen`: 1-2 párrafos en prosa. `datos`: el mismo contenido,
    estructurado, para que las tools de WP-V2-11 (`registrar_salud`,
    `tutor_leccion`, ...) o de negocio puedan leer campos concretos sin
    tener que parsear prosa."""

    resumen: str = ""
    datos: ProfileData = Field(default_factory=ProfileData)
    version: int = 1
