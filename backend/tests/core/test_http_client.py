from __future__ import annotations

import io
import json
from urllib import error as url_error


def test_post_json_returns_parsed_dict(monkeypatch):
    from backend.app.infra.http_client import post_json

    class FakeResponse:
        def __init__(self, payload: dict):
            self._payload = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        def read(self):
            return self._payload

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen(upstream_request, timeout=45):
        assert timeout == 12
        assert upstream_request.full_url == "https://example.com/demo"
        return FakeResponse({"ok": True})

    monkeypatch.setattr("backend.app.infra.http_client.request.urlopen", fake_urlopen)
    payload = post_json("https://example.com/demo", {"hello": "world"}, "secret-key", timeout=12)
    assert payload == {"ok": True}


def test_post_json_raises_http_error_with_status_and_body(monkeypatch):
    from backend.app.infra.http_client import HttpClientHttpError, post_json

    def fake_urlopen(upstream_request, timeout=45):
        raise url_error.HTTPError(
            url=upstream_request.full_url,
            code=502,
            msg="Bad Gateway",
            hdrs=None,
            fp=io.BytesIO(b'{"error":"gateway"}'),
        )

    monkeypatch.setattr("backend.app.infra.http_client.request.urlopen", fake_urlopen)
    try:
        post_json("https://example.com/demo", {"hello": "world"}, "secret-key")
    except HttpClientHttpError as exc:
        assert exc.status_code == 502
        assert "gateway" in exc.body_text
    else:
        raise AssertionError("expected HttpClientHttpError")


def test_post_json_raises_invalid_json_error(monkeypatch):
    from backend.app.infra.http_client import HttpClientInvalidJsonError, post_json

    class FakeResponse:
        def read(self):
            return b"<html>not-json</html>"

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("backend.app.infra.http_client.request.urlopen", lambda *_args, **_kwargs: FakeResponse())
    try:
        post_json("https://example.com/demo", {"hello": "world"}, "secret-key")
    except HttpClientInvalidJsonError as exc:
        assert "not-json" in exc.body_text
    else:
        raise AssertionError("expected HttpClientInvalidJsonError")
