from pathlib import Path
import threading
import time

from interview_copilot.codex_app_server import CodexAppServerClient


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
