from pathlib import Path
import threading
import time

from interview_copilot.codex_app_server import CodexAppServerClient, resolve_codex_executable


def test_turn_can_override_model_and_effort():
    client = CodexAppServerClient(Path("."))
    client.thread_id = "thread-1"
    captured = {}

    def fake_request(method, params, timeout=20):
        captured.update({"method": method, "params": params, "timeout": timeout})
        return {"turn": {"id": "turn-1"}}

    client.request = fake_request
    client.start_turn("回答问题", model="gpt-5.6-luna", effort="low")
    assert captured["method"] == "turn/start"
    assert captured["params"]["model"] == "gpt-5.6-luna"
    assert captured["params"]["effort"] == "low"
    assert captured["timeout"] == 10


def test_login_is_skipped_when_chatgpt_account_already_exists():
    client = CodexAppServerClient(Path("."))
    client.account = lambda: {"account": {"type": "chatgpt", "planType": "plus"}}
    client.request = lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("account/login/start must not be called")
    )
    result = client.begin_chatgpt_login()
    assert result["type"] == "alreadyLoggedIn"
    assert result["account"]["planType"] == "plus"


def test_interrupt_waits_until_turn_completed_before_returning():
    client = CodexAppServerClient(Path("."))
    client.thread_id = "thread-1"
    client.turn_id = "turn-1"
    client._turn_finished.clear()

    def fake_request(*_args, **_kwargs):
        threading.Timer(0.05, client._turn_finished.set).start()
        return {}

    client.request = fake_request
    started = time.perf_counter()

    client.interrupt()

    assert time.perf_counter() - started >= 0.04


def test_resolve_codex_finds_desktop_bundle_when_path_is_missing(tmp_path, monkeypatch):
    executable = tmp_path / "OpenAI" / "Codex" / "bin" / "version-hash" / "codex.exe"
    executable.parent.mkdir(parents=True)
    executable.touch()
    monkeypatch.setenv("PATH", "")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("INTERVIEW_COPILOT_CODEX", raising=False)

    assert resolve_codex_executable() == str(executable)
