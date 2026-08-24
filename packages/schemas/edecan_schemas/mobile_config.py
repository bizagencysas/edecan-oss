"""Contratos server-driven para clientes móviles.

El objetivo es que iOS/Android puedan cambiar navegación, textos y flags de
producto desde el backend sin publicar una build nueva. No describe layouts
arbitrarios ni ejecuta código remoto: es configuración declarativa, versionada
y validada.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MobilePlatform = Literal["ios", "android", "web", "desktop"]


class MobileTabConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=32)
    system_icon: str = Field(min_length=1, max_length=64)
    enabled: bool = True
    order: int = Field(ge=0, le=100)
    badge: str | None = Field(default=None, max_length=24)


class MobileCopyConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    assistant_title: str = "Edecán"
    chat_placeholder: str = "Escríbele a Edecán..."
    ide_placeholder: str = "Dile qué construir, revisar o arreglar..."
    activity_empty: str = "No hay nada en curso ahora."
    profile_title: str = "Tú"


class MobileFeatureFlags(BaseModel):
    model_config = ConfigDict(extra="allow")

    attachments: bool = True
    camera: bool = True
    photo_picker: bool = True
    rich_cards: bool = True
    chat_streaming: bool = True
    ide_remote: bool = True
    voice: bool = True
    calls: bool = True
    activity: bool = True
    profile: bool = True
    server_driven_ui: bool = True


class MobileActionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=64)
    kind: Literal["prefill", "open_url", "open_screen"]
    value: str = Field(min_length=1, max_length=1024)
    enabled: bool = True


class MobileServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: int = 1
    config_version: int = 1
    updated_at: datetime
    min_supported_build: int = 1
    platform: MobilePlatform | Literal["all"] = "all"
    tabs: list[MobileTabConfig]
    copy_config: MobileCopyConfig = Field(
        default_factory=MobileCopyConfig,
        alias="copy",
    )
    flags: MobileFeatureFlags = Field(default_factory=MobileFeatureFlags)
    quick_actions: list[MobileActionConfig] = Field(default_factory=list)


def default_mobile_server_config(*, version: int = 1) -> MobileServerConfig:
    return MobileServerConfig(
        config_version=version,
        updated_at=datetime.now(UTC),
        tabs=[
            MobileTabConfig(
                id="assistant",
                title="Edecán",
                system_icon="bubble.left.and.bubble.right.fill",
                order=0,
            ),
            MobileTabConfig(
                id="equipo",
                title="Equipo",
                system_icon="person.3.fill",
                order=1,
            ),
            MobileTabConfig(
                id="activity",
                title="Actividad",
                system_icon="clock.arrow.circlepath",
                order=2,
            ),
            MobileTabConfig(
                id="ide",
                title="IDE",
                system_icon="chevron.left.forwardslash.chevron.right",
                order=3,
            ),
            MobileTabConfig(
                id="profile",
                title="Tú",
                system_icon="person.crop.circle.fill",
                order=4,
            ),
        ],
        quick_actions=[
            MobileActionConfig(
                id="organize_day",
                title="Organiza mi día",
                kind="prefill",
                value="Organiza mis pendientes para hoy.",
            ),
            MobileActionConfig(
                id="create_post",
                title="Crear post",
                kind="prefill",
                value="Créame un post para LinkedIn.",
            ),
            MobileActionConfig(
                id="open_ide",
                title="Abrir IDE",
                kind="open_screen",
                value="ide",
            ),
        ],
    )
