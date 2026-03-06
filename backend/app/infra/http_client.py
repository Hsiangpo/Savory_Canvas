from __future__ import annotations

import json
from typing import Any
from urllib import error as url_error
from urllib import request


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)


class HttpClientHttpError(Exception):
    def __init__(self, status_code: int, body_text: str):
        super().__init__(body_text or f"HTTP {status_code}")
        self.status_code = status_code
        self.body_text = body_text


class HttpClientNetworkError(Exception):
    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


class HttpClientInvalidJsonError(Exception):
    def __init__(self, body_text: str):
        super().__init__(body_text)
        self.body_text = body_text


class HttpClientInvalidPayloadError(Exception):
    def __init__(self, payload: Any):
        super().__init__(type(payload).__name__)
        self.payload = payload


def build_json_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": DEFAULT_USER_AGENT,
    }


def post_json(url: str, payload: dict[str, Any], api_key: str, *, timeout: int = 45) -> dict[str, Any]:
    headers = build_json_headers(api_key)
    request_data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    upstream_request = request.Request(url=url, method="POST", headers=headers, data=request_data)
    try:
        with request.urlopen(upstream_request, timeout=timeout) as response:
            raw_text = response.read().decode("utf-8")
    except url_error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="ignore")
        raise HttpClientHttpError(int(getattr(exc, "code", 0) or 0), body_text) from exc
    except (TimeoutError, url_error.URLError, OSError) as exc:
        raise HttpClientNetworkError(str(exc)) from exc

    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise HttpClientInvalidJsonError(raw_text) from exc
    if not isinstance(parsed, dict):
        raise HttpClientInvalidPayloadError(parsed)
    return parsed


def download_binary(url: str, *, timeout: int = 45) -> bytes:
    try:
        with request.urlopen(url, timeout=timeout) as response:
            return response.read()
    except url_error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="ignore")
        raise HttpClientHttpError(int(getattr(exc, "code", 0) or 0), body_text) from exc
    except (TimeoutError, url_error.URLError, OSError) as exc:
        raise HttpClientNetworkError(str(exc)) from exc
