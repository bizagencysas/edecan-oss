from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4


@dataclass(frozen=True, slots=True)
class DesignVersion:
    artifact_id: UUID
    version_id: UUID
    parent_version_id: UUID | None
    title: str
    html: str
    width: int
    height: int
    summary: str
    created_at: datetime
    sha256: str

    @classmethod
    def create(
        cls,
        *,
        artifact_id: UUID,
        title: str,
        html: str,
        width: int,
        height: int,
        summary: str,
        parent_version_id: UUID | None = None,
    ) -> DesignVersion:
        return cls(
            artifact_id=artifact_id,
            version_id=uuid4(),
            parent_version_id=parent_version_id,
            title=title,
            html=html,
            width=width,
            height=height,
            summary=summary,
            created_at=datetime.now(UTC),
            sha256=hashlib.sha256(html.encode("utf-8")).hexdigest(),
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["artifact_id"] = str(self.artifact_id)
        data["version_id"] = str(self.version_id)
        data["parent_version_id"] = str(self.parent_version_id) if self.parent_version_id else None
        data["created_at"] = self.created_at.astimezone(UTC).isoformat()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DesignVersion:
        return cls(
            artifact_id=UUID(str(data["artifact_id"])),
            version_id=UUID(str(data["version_id"])),
            parent_version_id=(
                UUID(str(data["parent_version_id"])) if data.get("parent_version_id") else None
            ),
            title=str(data["title"]),
            html=str(data["html"]),
            width=int(data["width"]),
            height=int(data["height"]),
            summary=str(data.get("summary") or ""),
            created_at=datetime.fromisoformat(str(data["created_at"])),
            sha256=str(data["sha256"]),
        )

    def public_metadata(self) -> dict[str, Any]:
        return {
            "version_id": str(self.version_id),
            "parent_version_id": (str(self.parent_version_id) if self.parent_version_id else None),
            "title": self.title,
            "width": self.width,
            "height": self.height,
            "summary": self.summary,
            "created_at": self.created_at.astimezone(UTC).isoformat(),
            "sha256": self.sha256,
        }


@dataclass(frozen=True, slots=True)
class RenderBundle:
    png: bytes | None
    pdf: bytes | None
    engine: str
