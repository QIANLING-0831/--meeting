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


def test_paraformer_key_can_be_confirmed_saved_and_masked(tmp_path, monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    app = create_app(tmp_path)

    with TestClient(app) as client:
        mismatch = client.post(
            "/api/settings/paraformer-key",
            json={"api_key": "sk-test-12345678", "confirm_api_key": "sk-other"},
        )
        saved = client.post(
            "/api/settings/paraformer-key",
            json={"api_key": "sk-test-12345678", "confirm_api_key": "sk-test-12345678"},
        )
        status = client.get("/api/status").json()

    assert mismatch.status_code == 400
    assert saved.status_code == 200
    assert saved.json() == {"configured": True, "masked": "sk-****5678"}
    assert status["paraformerConfigured"] is True
    assert status["paraformerKeyMasked"] == "sk-****5678"
    assert "sk-test-12345678" not in str(status)
    assert app.state.engine.config.paraformer_api_key == "sk-test-12345678"


def test_paraformer_key_cannot_change_while_recognition_is_running(tmp_path):
    app = create_app(tmp_path)
    app.state.engine.active = True

    with TestClient(app) as client:
        response = client.post(
            "/api/settings/paraformer-key",
            json={"api_key": "sk-test-12345678", "confirm_api_key": "sk-test-12345678"},
        )

    assert response.status_code == 409
