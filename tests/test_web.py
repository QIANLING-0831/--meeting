import time
from types import SimpleNamespace

from fastapi.testclient import TestClient

from interview_copilot.config import AppConfig
from interview_copilot.web import create_app
from interview_copilot.external_sources import ImportedSource


def test_home_and_status_are_qwen_only(tmp_path, monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    with TestClient(create_app(tmp_path)) as client:
        home = client.get("/")
        page = home.text
        status = client.get("/api/status").json()

    assert 'id="loopbackDevice"' in page
    assert 'id="microphoneEnabled"' in page
    assert 'id="overlayEnabled"' in page
    assert "Qwen Audio Realtime Plus" in page
    assert "Codex" not in page
    assert "Paraformer" not in page
    assert status["aliyunConfigured"] is False
    assert "loopbackDevices" in status
    assert "microphoneDevices" in status
    assert status["answerSnapshot"]["text"] == ""
    assert status["overlay"]["settings"]["historyCount"] == 2
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
    monkeypatch.setattr(
        "interview_copilot.web.list_input_devices",
        lambda: [SimpleNamespace(name="麦克风", id="7")],
    )
    app = create_app(tmp_path)
    app.state.engine.session, _ = app.state.engine.sessions.create(
        company="测试公司", position="Agent", jd_text="JD", resume_path=None, knowledge_packs=[]
    )
    calls = []
    monkeypatch.setattr(app.state.audio, "start", lambda **options: calls.append(options))

    with TestClient(app) as client:
        response = client.post("/api/interview/start", json={"loopback_device_name": "扬声器", "microphone_enabled": True, "microphone_device_id": "7"})

    assert response.status_code == 200
    assert calls[0]["loopback_device_name"] == "扬声器"
    assert calls[0]["microphone_enabled"] is True
    assert calls[0]["microphone_device_id"] == "7"
    assert "测试公司" in calls[0]["fast_instructions"]
    assert AppConfig.load(tmp_path).device_name == "扬声器"
    assert AppConfig.load(tmp_path).microphone_enabled is True


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


def test_external_github_source_is_added_to_current_session(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "interview_copilot.web.import_github_source",
        lambda url, label: ImportedSource(label, url, "什么是 Agent Loop？"),
    )
    app = create_app(tmp_path)
    app.state.engine.session, _ = app.state.engine.sessions.create(
        company="测试", position="Agent", jd_text="", resume_path=None, knowledge_packs=[]
    )

    with TestClient(app) as client:
        response = client.post(
            f"/api/session/{app.state.engine.session.id}/external-sources",
            json={"label": "Agent 题库", "url": "https://github.com/example/questions"},
        )
        status = client.get("/api/status").json()

    assert response.status_code == 200
    assert status["session"]["externalSources"] == [
        {"label": "Agent 题库", "url": "https://github.com/example/questions"}
    ]
    assert "Agent Loop" in app.state.engine.instructions()


def test_workbench_checkbox_persists_local_knowledge_for_all_answer_models(tmp_path):
    pack = tmp_path / "workspace" / "knowledge" / "packs" / "agent"
    pack.mkdir(parents=True)
    (pack / "workflow.md").write_text(
        "# Agent 工作流\n先规划，再执行工具。", encoding="utf-8"
    )
    app = create_app(tmp_path)
    app.state.engine.context_provider.knowledge.rebuild()

    with TestClient(app) as client:
        prepared = client.post(
            "/api/session/prepare",
            data={
                "company": "测试",
                "position": "Agent",
                "jd_text": "Agent 工作流",
                "knowledge_packs": '["agent"]',
            },
        )
        session_id = prepared.json()["session"]["id"]
        unchecked = client.post(
            f"/api/session/{session_id}/knowledge-packs", json={"packs": []}
        )

    assert prepared.status_code == 200
    assert prepared.json()["session"]["knowledge_packs"] == ["agent"]
    assert unchecked.status_code == 200
    assert app.state.engine.session.knowledge_packs == []


def test_overlay_settings_are_validated_and_persisted(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        saved = client.post(
            "/api/settings/overlay",
            json={
                "opacity": 0.73,
                "background_opacity": 0.73,
                "text_opacity": 0.82,
                "font_size": 28,
                "history_font_size": 13,
                "text_color": "#FFFFFF",
                "history_color": "#AABBCC",
                "show_question": True,
                "show_history": True,
                "history_count": 3,
                "highlight_progress": True,
                "auto_microphone": True,
                "size_preset": "wide",
            },
        )
        invalid = client.post(
            "/api/settings/overlay",
            json={"background_opacity": 0.01, "font_size": 28, "history_font_size": 13},
        )
        status = client.get("/api/status").json()

    assert saved.status_code == 200
    assert invalid.status_code == 400
    assert status["overlay"]["settings"]["historyCount"] == 3
    persisted = AppConfig.load(tmp_path)
    assert persisted.overlay_background_opacity == 0.73
    assert persisted.overlay_text_opacity == 0.82


def test_overlay_geometry_accepts_small_custom_size(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        response = client.post(
            "/api/overlay/geometry",
            json={"x": 24, "y": 30, "width": 360, "height": 180},
        )

    persisted = AppConfig.load(tmp_path)
    assert response.status_code == 200
    assert persisted.overlay_width == 360
    assert persisted.overlay_height == 180
    assert persisted.overlay_size_preset == "custom"


def test_overlay_accepts_readable_large_font_sizes(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        response = client.post(
            "/api/settings/overlay",
            json={
                "font_size": 64,
                "history_font_size": 32,
                "size_preset": "standard",
            },
        )

    assert response.status_code == 200
    assert response.json()["fontSize"] == 64
    assert response.json()["historyFontSize"] == 32


def test_overlay_accepts_compact_font_sizes(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        response = client.post(
            "/api/settings/overlay",
            json={"font_size": 12, "history_font_size": 6, "size_preset": "standard"},
        )

    assert response.status_code == 200
    assert response.json()["fontSize"] == 12
    assert response.json()["historyFontSize"] == 6


def test_overlay_can_be_force_unlocked_from_workbench(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "interview_copilot.overlay.OverlayManager.running",
        property(lambda _self: True),
    )
    app = create_app(tmp_path)

    with TestClient(app) as client:
        locked = client.post("/api/overlay/lock")
        unlocked = client.post("/api/overlay/unlock")

    assert locked.status_code == 200
    assert locked.json()["locked"] is True
    assert unlocked.status_code == 200
    assert unlocked.json()["locked"] is False


def test_enabled_overlay_automatically_enables_candidate_microphone(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test-12345678")
    AppConfig(overlay_enabled=True, overlay_auto_microphone=True).save(tmp_path)
    monkeypatch.setattr("interview_copilot.web.OverlayManager.start", lambda _self: True)
    monkeypatch.setattr("interview_copilot.web.OverlayManager.stop", lambda _self: None)
    monkeypatch.setattr(
        "interview_copilot.web.list_loopback_devices",
        lambda: [SimpleNamespace(name="扬声器", id="device-1")],
    )
    monkeypatch.setattr(
        "interview_copilot.web.probe_loopback_levels",
        lambda **_options: [{"name": "扬声器", "id": "device-1", "rms": 0.02}],
    )
    monkeypatch.setattr(
        "interview_copilot.web.list_input_devices",
        lambda: [SimpleNamespace(name="麦克风", id="7")],
    )
    app = create_app(tmp_path)
    app.state.engine.session, _ = app.state.engine.sessions.create(
        company="测试", position="Agent", jd_text="", resume_path=None, knowledge_packs=[]
    )
    calls = []
    monkeypatch.setattr(app.state.audio, "start", lambda **options: calls.append(options))

    with TestClient(app) as client:
        response = client.post(
            "/api/interview/start",
            json={
                "loopback_device_name": "扬声器",
                "microphone_enabled": False,
                "microphone_device_id": "7",
            },
        )

    assert response.status_code == 200
    assert response.json()["microphoneEnabled"] is True
    assert calls[0]["microphone_enabled"] is True
