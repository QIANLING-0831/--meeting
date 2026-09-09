from pathlib import Path

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


def test_login_is_skipped_when_chatgpt_account_already_exists():
    client = CodexAppServerClient(Path("."))
    client.account = lambda: {"account": {"type": "chatgpt", "planType": "plus"}}
    client.request = lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("account/login/start must not be called")
    )
    result = client.begin_chatgpt_login()
    assert result["type"] == "alreadyLoggedIn"
    assert result["account"]["planType"] == "plus"
