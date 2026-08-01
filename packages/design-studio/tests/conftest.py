from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
from edecan_design_studio.models import DesignVersion, RenderBundle
from edecan_design_studio.storage import DesignNotFoundError


@dataclass
class MemoryStore:
    versions: dict[tuple[UUID, UUID], list[DesignVersion]] = field(default_factory=dict)

    async def save(self, tenant_id: UUID, version: DesignVersion) -> None:
        self.versions.setdefault((tenant_id, version.artifact_id), []).append(version)

    async def get(
        self, tenant_id: UUID, artifact_id: UUID, version_id: UUID | None = None
    ) -> DesignVersion:
        history = self.versions.get((tenant_id, artifact_id), [])
        if version_id is None and history:
            return history[-1]
        for item in history:
            if item.version_id == version_id:
                return item
        raise DesignNotFoundError("No encontré ese diseño.")

    async def history(self, tenant_id: UUID, artifact_id: UUID) -> list[DesignVersion]:
        return list(self.versions.get((tenant_id, artifact_id), []))


@dataclass
class FakeUploader:
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def __call__(self, ctx: Any, *, data: bytes, filename: str, mime: str):
        self.calls.append({"ctx": ctx, "data": data, "filename": filename, "mime": mime})
        return uuid4(), filename


class FakeRenderer:
    async def render(
        self,
        html: str,
        *,
        width: int,
        height: int,
        include_png: bool,
        include_pdf: bool,
    ) -> RenderBundle:
        return RenderBundle(
            png=b"\x89PNG\r\n\x1a\npreview" if include_png else None,
            pdf=b"%PDF-1.7\n%%EOF" if include_pdf else None,
            engine="fake-browser",
        )


@dataclass
class FakeLLM:
    text: str = "contenido de prueba"
    llamadas: list[tuple[str, dict[str, Any], Any]] = field(default_factory=list)

    async def complete(self, alias: str, flags: dict[str, Any], request: Any):
        self.llamadas.append((alias, flags, request))
        return SimpleNamespace(text=self.text)


@pytest.fixture
def studio_deps():
    store = MemoryStore()
    uploader = FakeUploader()
    renderer = FakeRenderer()
    return store, uploader, renderer


@pytest.fixture
def make_ctx():
    def factory(*, tenant_id: UUID | None = None):
        return SimpleNamespace(
            tenant_id=tenant_id or uuid4(),
            user_id=uuid4(),
            settings=SimpleNamespace(),
            session=SimpleNamespace(),
            llm=FakeLLM(),
            vault=None,
            extras={},
        )

    return factory
