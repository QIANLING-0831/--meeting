import time
from types import SimpleNamespace

from fastapi.testclient import TestClient

from interview_copilot.config import AppConfig
from interview_copilot.web import create_app


def test_home_and_status_are_qwen_only(tmp_path, monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    with TestClient(create_app(tmp_path)) as client:
        home = client.get("/")
        page = home.text
        status = client.get("/api/status").json()

    assert 'id="loopbackDevice"' in page
    assert "Qwen Audio Realtime Plus" in page
    assert "Codex" not in page
    assert "Paraformer" not in page
    assert status["aliyunConfigured"] is False
    assert "loopbackDevices" in status
    assert status["answerSnapshot"]["text"] == ""
    assert home.headers["cache-control"] == "no-store"


def test_last_browser_disconnect_stops_active_interview_after_grace_period(tmp_path):
    (tmp_path / "config.json").write_text(
        '{"browser_disconnect_grace_seconds": 0.01}', encoding="utf-8"
    )
    app = create_app(tmp_path)
    calls = []
    app.state.audio.stop = lambda: calls.append("stop")
    app.state.engine.active = True

    with TestClient(app) as client:
        with client.websocket_connect("/ws"):
            pass
        time.sleep(0.08)

    assert app.state.engine.active is False
    assert calls


def test_websocket_drains_burst_without_per_event_throttle(tmp_path):
    app = create_app(tmp_path)
    started = time.monotonic()
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as websocket:
            for index in range(30):
                app.state.engine.bus.publish({"type": "burst", "index": index})
            received = [websocket.receive_json()["index"] for _ in range(30)]

    assert received == list(range(30))
    assert time.monotonic() - started < 1.5


def test_prepare_session_injects_resume_facts_into_qwen_prompt(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app) as client:
        response = client.post(
            "/api/session/prepare",
            data={"company": "测试公司", "position": "Agent 工程师", "jd_text": "熟悉 RAG"},
            files={"resume": ("resume.txt", "负责检索项目".encode("utf-8"), "text/plain")},
        )
        session_id = response.json()["session"]["id"]
        client.post(f"/api/session/{session_id}/facts", json={"text": "项目：检索助手，延迟降低 30%"})

    prompt = app.state.engine.instructions()
    assert response.status_code == 200
    assert "熟悉 RAG" in prompt
    assert "延迟降低 30%" in prompt


def test_aliyun_key_can_be_saved_and_masked(tmp_path, monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    with TestClient(create_app(tmp_path)) as client:
        mismatch = client.post("/api/settings/aliyun-key", json={"api_key": "sk-test-12345678", "confirm_api_key": "different"})
        saved = client.post("/api/settings/aliyun-key", json={"api_key": "sk-test-12345678", "confirm_api_key": "sk-test-12345678"})
        status = client.get("/api/status").json()

    assert mismatch.status_code == 400
    assert saved.status_code == 200
    assert saved.json()["masked"] == "sk-****5678"
    assert status["aliyunConfigured"] is True
    assert "sk-test-12345678" not in str(status)


def test_qwen_settings_cannot_be_disabled(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        response = client.post(
            "/api/settings/realtime",
            json={"enabled": False, "model": "qwen-audio-3.0-realtime-plus", "workspace_id": "", "turn_detection": "smart_turn"},
        )
        status = client.get("/api/status").json()

    assert response.status_code == 200
    assert "enabled" not in response.json()
    assert status["realtimeSettings"]["model"] == "qwen-audio-3.0-realtime-plus"


def test_start_interview_uses_selected_loopback_and_qwen_prompt(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test-12345678")
    monkeypatch.setattr(
        "interview_copilot.web.list_loopback_devices",
        lambda: [SimpleNamespace(name="扬声器", id="device-1")],
    )
    monkeypatch.setattr(
        "interview_copilot.web.probe_loopback_levels",
        lambda **_options: [{"name": "扬声器", "id": "device-1", "rms": 0.02}],
    )
    app = create_app(tmp_path)
    app.state.engine.session, _ = app.state.engine.sessions.create(
        company="测试公司", position="Agent", jd_text="JD", resume_path=None, knowledge_packs=[]
    )
    calls = []
    monkeypatch.setattr(app.state.audio, "start", lambda **options: calls.append(options))

    with TestClient(app) as client:
        response = client.post("/api/interview/start", json={"loopback_device_name": "扬声器"})

    assert response.status_code == 200
    assert calls[0]["loopback_device_name"] == "扬声器"
    assert "测试公司" in calls[0]["fast_instructions"]
    assert AppConfig.load(tmp_path).device_name == "扬声器"


def test_start_auto_selects_the_device_that_has_meeting_audio(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test-12345678")
    devices = [SimpleNamespace(name="旧扬声器", id="1"), SimpleNamespace(name="腾讯会议扬声器", id="2")]
    monkeypatch.setattr("interview_copilot.web.list_loopback_devices", lambda: devices)
    monkeypatch.setattr(
        "interview_copilot.web.probe_loopback_levels",
        lambda **_options: [
            {"name": "旧扬声器", "id": "1", "rms": 0.0},
            {"name": "腾讯会议扬声器", "id": "2", "rms": 0.015},
        ],
    )
    app = create_app(tmp_path)
    app.state.engine.session, _ = app.state.engine.sessions.create(
        company="测试", position="Agent", jd_text="", resume_path=None, knowledge_packs=[]
    )
    calls = []
    monkeypatch.setattr(app.state.audio, "start", lambda **options: calls.append(options))

    with TestClient(app) as client:
        response = client.post("/api/interview/start", json={"loopback_device_name": "旧扬声器"})

    assert response.status_code == 200
    assert response.json()["loopbackDeviceName"] == "腾讯会议扬声器"
    assert calls[0]["loopback_device_name"] == "腾讯会议扬声器"
