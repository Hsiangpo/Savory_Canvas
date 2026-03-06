from __future__ import annotations

from contextvars import ContextVar
import logging
from typing import Callable
from uuid import uuid4

from fastapi import Request, Response

REQUEST_ID_CONTEXT: ContextVar[str] = ContextVar("request_id", default="-")


def configure_logging() -> None:
    root_logger = logging.getLogger()
    if root_logger.handlers:
        return
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] [request_id=%(request_id)s] %(message)s",
    )


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = REQUEST_ID_CONTEXT.get("-")
        return True


def install_request_id_filter() -> None:
    request_filter = RequestIdFilter()
    for handler in logging.getLogger().handlers:
        if any(isinstance(existing, RequestIdFilter) for existing in handler.filters):
            continue
        handler.addFilter(request_filter)


async def request_id_middleware(request: Request, call_next: Callable[[Request], Response]) -> Response:
    request_id = request.headers.get("X-Request-Id") or str(uuid4())
    request.state.request_id = request_id
    token = REQUEST_ID_CONTEXT.set(request_id)
    try:
        response = await call_next(request)
    finally:
        REQUEST_ID_CONTEXT.reset(token)
    response.headers["X-Request-Id"] = request_id
    return response
