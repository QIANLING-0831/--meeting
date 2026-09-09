from interview_copilot.answering import InterviewEngine
from interview_copilot.codex_app_server import CodexAppServerError
from interview_copilot.config import AppConfig
from interview_copilot.event_bus import EventBus


def build_engine(tmp_path, include_points):
    config = AppConfig(include_core_points=include_points)
    engine = InterviewEngine(tmp_path, config, EventBus())
    session, _ = engine.sessions.create(
        company="测试",
        position="开发",
        jd_text="负责 Agent",
        resume_path=None,
        knowledge_packs=[],
    )
    engine.set_session(session)
    return engine


def published_questions(events):
    published = []
    while not events.empty():
        event = events.get_nowait()
        if event["type"] == "question_candidate":
            published.append(event["question"]["text"])
    return published


def test_spoken_only_prompt_is_default(tmp_path):
    prompt = build_engine(tmp_path, False)._build_prompt("什么是 ReAct？", [])
    assert "只输出一段30～45秒" in prompt
    assert "不要核心要点列表" in prompt
    assert "### 核心要点" not in prompt


def test_optional_points_prompt(tmp_path):
    prompt = build_engine(tmp_path, True)._build_prompt("什么是 ReAct？", [])
    assert "### 核心要点" in prompt
    assert "3到4条" in prompt


def test_manual_questions_publish_latest_candidate_while_inactive(tmp_path):
    engine = build_engine(tmp_path, False)
    events = engine.bus.subscribe()

    engine.ingest_transcript("interviewer", "第一个问题：什么是 RAG？", final=True)
    engine.ingest_transcript("interviewer", "第二个问题：什么是 ReAct？", final=True)

    published = []
    while not events.empty():
        published.append(events.get_nowait())
    questions = [
        event["question"]["text"]
        for event in published
        if event["type"] == "question_candidate"
    ]
    assert questions == ["第一个问题：什么是 RAG？", "第二个问题：什么是 ReAct？"]


def test_split_question_is_assembled_before_publishing(tmp_path):
    engine = build_engine(tmp_path, False)
    events = engine.bus.subscribe()

    engine.ingest_transcript("interviewer", "你说一下", final=True)
    engine.ingest_transcript("interviewer", "C++的特性有", final=True)
    engine.ingest_transcript("interviewer", "哪些？", final=True)

    assert published_questions(events) == ["你说一下C++的特性有哪些？"]


def test_independent_question_is_not_merged_with_previous_question(tmp_path):
    engine = build_engine(tmp_path, False)
    events = engine.bus.subscribe()

    engine.ingest_transcript("interviewer", "项目有哪些难点？", final=True)
    engine.ingest_transcript("interviewer", "为什么选择 C++？", final=True)

    assert published_questions(events) == ["项目有哪些难点？", "为什么选择 C++？"]


def test_candidate_speech_breaks_question_assembly_context(tmp_path):
    engine = build_engine(tmp_path, False)
    events = engine.bus.subscribe()

    engine.ingest_transcript("interviewer", "C++的特性有", final=True)
    engine.ingest_transcript("candidate", "我先回答一下", final=True)
    engine.ingest_transcript("interviewer", "哪些？", final=True)

    assert published_questions(events) == ["哪些？"]


def test_auto_answer_schedules_only_the_assembled_question(tmp_path, monkeypatch):
    engine = build_engine(tmp_path, False)
    scheduled = []
    monkeypatch.setattr(engine, "schedule_answer", scheduled.append)
    engine.active = True

    engine.ingest_transcript("interviewer", "你说一下", final=True)
    engine.ingest_transcript("interviewer", "C++的特性有", final=True)
    engine.ingest_transcript("interviewer", "哪些？", final=True)

    assert scheduled == ["你说一下C++的特性有哪些？"]


def test_answer_reconnects_once_when_codex_start_times_out(tmp_path, monkeypatch):
    engine = build_engine(tmp_path, False)
    events = engine.bus.subscribe()
    attempts = []
    closes = []

    def ensure_thread(**_kwargs):
        attempts.append("ensure")
        if len(attempts) == 1:
            raise CodexAppServerError("Codex 请求超时：initialize")
        return "thread-2"

    monkeypatch.setattr(engine.codex, "ensure_thread", ensure_thread)
    monkeypatch.setattr(engine.codex, "close", lambda: closes.append("close"))
    monkeypatch.setattr(engine.codex, "start_turn", lambda *_args, **_kwargs: {"turn": {"id": "turn-2"}})

    engine.answer("什么是 LLM？")
    engine._cancel_answer_watchdog()

    assert attempts == ["ensure", "ensure"]
    assert closes == ["close"]
    notices = []
    while not events.empty():
        event = events.get_nowait()
        if event["type"] == "notice":
            notices.append(event["message"])
    assert notices == ["Codex 连接超时，正在自动重连（1/1）"]


def test_first_token_timeout_restarts_turn_once(tmp_path, monkeypatch):
    engine = build_engine(tmp_path, False)
    events = engine.bus.subscribe()
    engine._answer_generation = 7
    engine._answer_prompt = "回答这个问题"
    engine._answer_buffer = ""
    closes = []
    starts = []
    monkeypatch.setattr(engine.codex, "close", lambda: closes.append("close"))
    monkeypatch.setattr(
        engine,
        "_start_codex_turn",
        lambda prompt, **_kwargs: starts.append(prompt),
    )
    monkeypatch.setattr(engine, "_arm_answer_watchdog", lambda generation: starts.append(generation))

    engine._handle_first_token_timeout(7)

    assert closes == ["close"]
    assert starts == ["回答这个问题", 7]
    assert engine._first_token_retry_used is True
    retry_events = []
    while not events.empty():
        event = events.get_nowait()
        if event["type"] == "answer_retrying":
            retry_events.append(event["message"])
    assert retry_events == ["Codex 首字响应超时，正在自动重连（1/1）"]


def test_second_first_token_timeout_stops_retrying(tmp_path):
    engine = build_engine(tmp_path, False)
    events = engine.bus.subscribe()
    engine._answer_generation = 3
    engine._first_token_retry_used = True

    engine._handle_first_token_timeout(3)

    errors = []
    while not events.empty():
        event = events.get_nowait()
        if event["type"] == "error":
            errors.append(event["message"])
    assert errors == ["Codex 重连后仍未在限定时间内返回，可按 F8 再试一次"]
