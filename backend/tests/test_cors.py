from __future__ import annotations


def test_cors_preflight_for_sessions(client):
    response = client.options(
        "/api/v1/sessions",
        headers={
            "Origin": "http://localhost:7778",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code in {200, 204}
    assert response.headers.get("access-control-allow-origin") == "http://localhost:7778"


def test_cors_get_sessions_with_origin(client):
    response = client.get(
        "/api/v1/sessions",
        headers={"Origin": "http://localhost:7778"},
    )
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "http://localhost:7778"


def test_cors_get_sessions_with_other_localhost_port(client):
    response = client.get(
        "/api/v1/sessions",
        headers={"Origin": "http://localhost:5173"},
    )
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") is None
