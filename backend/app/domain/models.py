from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterator, Mapping


@dataclass
class MappingDataclass(Mapping[str, Any]):
    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)

    def __iter__(self) -> Iterator[str]:
        return iter(self.to_dict())

    def __len__(self) -> int:
        return len(self.to_dict())

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SessionModel(MappingDataclass):
    id: str
    title: str
    content_mode: str
    created_at: str
    updated_at: str

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SessionModel":
        return cls(
            id=str(payload["id"]),
            title=str(payload["title"]),
            content_mode=str(payload["content_mode"]),
            created_at=str(payload["created_at"]),
            updated_at=str(payload["updated_at"]),
        )


@dataclass
class GenerationJobModel(MappingDataclass):
    id: str
    session_id: str
    style_profile_id: str | None
    image_count: int
    status: str
    progress_percent: int
    current_stage: str
    stage_message: str
    error_code: str | None
    error_message: str | None
    created_at: str
    updated_at: str

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "GenerationJobModel":
        return cls(
            id=str(payload["id"]),
            session_id=str(payload["session_id"]),
            style_profile_id=str(payload["style_profile_id"]) if payload.get("style_profile_id") else None,
            image_count=int(payload["image_count"]),
            status=str(payload["status"]),
            progress_percent=int(payload["progress_percent"]),
            current_stage=str(payload["current_stage"]),
            stage_message=str(payload["stage_message"]),
            error_code=str(payload["error_code"]) if payload.get("error_code") else None,
            error_message=str(payload["error_message"]) if payload.get("error_message") else None,
            created_at=str(payload["created_at"]),
            updated_at=str(payload["updated_at"]),
        )
