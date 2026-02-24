from __future__ import annotations

from dataclasses import dataclass
from typing import Any

NOT_FOUND_ERROR_MAP: dict[str, tuple[str, str]] = {
    "会话": ("E-2001", "会话不存在"),
    "素材": ("E-2002", "素材不存在"),
    "风格": ("E-2003", "风格不存在"),
    "任务": ("E-2004", "任务不存在"),
    "导出任务": ("E-2005", "导出任务不存在"),
    "提供商": ("E-2006", "提供商不存在"),
}


@dataclass
class DomainError(Exception):
    code: str
    message: str
    status_code: int = 400
    details: dict[str, Any] | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "details": self.details or {},
        }


def not_found(entity: str, entity_id: str) -> DomainError:
    error_code, error_message = NOT_FOUND_ERROR_MAP.get(entity, ("E-1099", f"未找到{entity}"))
    return DomainError(
        code=error_code,
        message=error_message,
        status_code=404,
        details={"entity": entity, "id": entity_id},
    )


def internal_error(details: dict[str, Any] | None = None) -> DomainError:
    return DomainError(code="E-1099", message="系统内部错误", status_code=500, details=details)
