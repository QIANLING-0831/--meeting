from fastapi.testclient import TestClient

from interview_copilot.web import create_app


def test_home_and_status_routes(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        assert client.get("/").status_code == 200
        status = client.get("/api/status")
        assert status.status_code == 200
        assert "loopbackDevices" in status.json()


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
