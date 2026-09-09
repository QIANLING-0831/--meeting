from fastapi.testclient import TestClient

from interview_copilot.web import create_app


def test_home_and_status_routes(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        assert client.get("/").status_code == 200
        status = client.get("/api/status")
        assert status.status_code == 200
        assert "loopbackDevices" in status.json()
        assert status.json()["answerSnapshot"] == {"question": "", "text": "", "running": False}


def test_prepare_session_without_resume(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        response = client.post(
            "/api/session/prepare",
            data={
                "company": "测试公司",
                "position": "Agent 工程师",
                "jd_text": "负责 RAG 与 Agent",
                "knowledge_packs": '["agent"]',
            },
        )
        assert response.status_code == 200
        assert response.json()["session"]["knowledge_packs"] == ["agent"]
        assert "候选人事实" in response.json()["candidateFacts"]


def test_codex_warmup_prepares_thread_before_first_question(tmp_path, monkeypatch):
    app = create_app(tmp_path)
    prepared = []
    monkeypatch.setattr(
        app.state.engine.codex,
        "ensure_thread",
        lambda **options: prepared.append(options) or "thread-ready",
    )

    with TestClient(app) as client:
        response = client.post("/api/codex/warmup")

    assert response.status_code == 200
    assert response.json() == {"ready": True}
    assert prepared[0]["model"] == app.state.engine.config.codex_model
