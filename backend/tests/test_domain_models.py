from __future__ import annotations

from conftest import create_generation_job, create_session, create_style, setup_model_routing


def test_session_repository_returns_session_model(client):
    from backend.app.domain.models import SessionModel

    session = create_session(client, title="dataclass-session", content_mode="food")
    repo = client.app.state.services.session.session_repo

    stored = repo.get(session["id"])

    assert isinstance(stored, SessionModel)
    assert stored["id"] == session["id"]
    assert stored.get("content_mode") == "food"
    assert stored.to_dict()["title"] == "dataclass-session"


def test_job_repository_returns_generation_job_model(client):
    from backend.app.domain.models import GenerationJobModel

    setup_model_routing(client)
    session = create_session(client, title="dataclass-job", content_mode="food")
    client.post(
        "/api/v1/assets/text",
        json={"session_id": session["id"], "asset_type": "text", "content": "羊肉泡馍与城墙夜景"},
    )
    style = create_style(client, session["id"], {"painting_style": ["电影写实"]})
    job = create_generation_job(client, session["id"], style["id"], image_count=1)
    repo = client.app.state.services.generation.job_repo

    stored = repo.get(job["id"])

    assert isinstance(stored, GenerationJobModel)
    assert stored["id"] == job["id"]
    assert stored.get("status") == "queued"
    assert stored.to_dict()["image_count"] == 1
