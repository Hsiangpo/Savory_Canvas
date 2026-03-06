from __future__ import annotations


def test_response_includes_generated_request_id(client):
    response = client.get("/api/v1/sessions")

    assert response.status_code == 200
    assert isinstance(response.headers.get("x-request-id"), str)
    assert response.headers["x-request-id"].strip()


def test_response_preserves_incoming_request_id(client):
    response = client.get(
        "/api/v1/sessions",
        headers={"X-Request-Id": "test-request-id-123"},
    )

    assert response.status_code == 200
    assert response.headers.get("x-request-id") == "test-request-id-123"


def test_request_id_filter_uses_contextvar_value():
    import logging

    from backend.app.core import logging_config

    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )
    token = logging_config.REQUEST_ID_CONTEXT.set("ctx-request-id")
    try:
        assert logging_config.RequestIdFilter().filter(record) is True
    finally:
        logging_config.REQUEST_ID_CONTEXT.reset(token)

    assert record.request_id == "ctx-request-id"
