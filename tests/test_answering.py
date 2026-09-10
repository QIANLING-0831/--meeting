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
    assert "第一句先用15～30个字直接给出结论" in prompt
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


def test_answer_reconnects_once_only_when_codex_process_start_times_out(tmp_path, monkeypatch):
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
    assert notices == ["Codex 启动超时，正在执行最后兜底重建（1/1）"]


def test_turn_start_timeout_keeps_existing_codex_session(tmp_path, monkeypatch):
    engine = build_engine(tmp_path, False)
    events = engine.bus.subscribe()
    closes = []
    monkeypatch.setattr(engine.codex, "ensure_thread", lambda **_kwargs: "thread-1")
    monkeypatch.setattr(
        engine.codex,
        "start_turn",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            CodexAppServerError("Codex 请求超时：turn/start")
        ),
    )
    monkeypatch.setattr(engine.codex, "close", lambda: closes.append("close"))

    engine.answer("什么是 LLM？")
    engine._cancel_answer_watchdog()

    assert closes == []
    notices = []
    while not events.empty():
        event = events.get_nowait()
        if event["type"] == "notice":
            notices.append(event["message"])
    assert notices == ["Codex 请求确认较慢，保留原会话并继续等待"]


def test_first_token_timeout_retries_turn_in_same_codex_session(tmp_path, monkeypatch):
    engine = build_engine(tmp_path, False)
    events = engine.bus.subscribe()
    engine._answer_generation = 7
    engine._answer_buffer = ""
    engine.codex.turn_id = "turn-old-old"
    closes = []
    actions = []
    monkeypatch.setattr(engine.codex, "close", lambda: closes.append("close"))
    monkeypatch.setattr(engine.codex, "interrupt", lambda: actions.append("interrupt"))
    monkeypatch.setattr(
        engine,
        "_start_codex_turn",
        lambda prompt, **_kwargs: actions.append(("start", prompt)),
    )
    monkeypatch.setattr(engine, "_arm_answer_watchdog", lambda generation: actions.append(("watchdog", generation)))
    engine._answer_prompt = "回答这个问题"

    engine._handle_first_token_timeout(7)

    assert closes == []
    assert actions == ["interrupt", ("start", "回答这个问题"), ("watchdog", 7)]
    assert engine._first_token_retry_used is True
    retry_events = []
    while not events.empty():
        event = events.get_nowait()
        if event["type"] == "answer_retrying":
            retry_events.append(event["message"])
    assert retry_events == ["Codex 首字超时，正在原会话内重试（不重连）"]


def test_unknown_turn_after_start_timeout_is_not_sent_twice(tmp_path, monkeypatch):
    engine = build_engine(tmp_path, False)
    engine._answer_generation = 2
    engine._answer_buffer = ""
    engine._answer_prompt = "回答"
    engine.codex.turn_id = None
    starts = []
    monkeypatch.setattr(engine, "_start_codex_turn", lambda *_args, **_kwargs: starts.append("start"))
    monkeypatch.setattr(engine, "_arm_answer_watchdog", lambda _generation: None)

    engine._handle_first_token_timeout(2)

    assert starts == []


def test_failed_interrupt_rotates_thread_without_restarting_codex_process(tmp_path, monkeypatch):
    engine = build_engine(tmp_path, False)
    engine._answer_generation = 4
    engine._answer_buffer = ""
    engine._answer_prompt = "回答"
    engine.codex.turn_id = "turn-stuck"
    actions = []
    monkeypatch.setattr(
        engine.codex,
        "interrupt",
        lambda: (_ for _ in ()).throw(CodexAppServerError("Codex 中断确认超时")),
    )
    monkeypatch.setattr(engine.codex, "reset_thread", lambda: actions.append("reset-thread"))
    monkeypatch.setattr(engine.codex, "close", lambda: actions.append("close-process"))
    monkeypatch.setattr(
        engine,
        "_start_codex_turn",
        lambda prompt, **_kwargs: actions.append(("start", prompt)),
    )
    monkeypatch.setattr(engine, "_arm_answer_watchdog", lambda generation: actions.append(("watchdog", generation)))

    engine._handle_first_token_timeout(4)

    assert actions == ["reset-thread", ("start", "回答"), ("watchdog", 4)]


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
    assert errors == ["Codex 原会话仍未返回，可按 F8 在同一会话中重试"]


def test_late_events_from_interrupted_turn_are_ignored(tmp_path):
    engine = build_engine(tmp_path, False)
    events = engine.bus.subscribe()
    engine.codex.turn_id = "turn-new"
    engine._obsolete_turn_ids.add("turn-old")

    engine._on_codex_event(
        {"method": "item/agentMessage/delta", "params": {"turnId": "turn-old", "delta": "旧回答"}}
    )
    engine._on_codex_event(
        {"method": "item/agentMessage/delta", "params": {"turnId": "turn-new", "delta": "新回答"}}
    )

    assert engine._answer_buffer == "新回答"
    deltas = []
    while not events.empty():
        event = events.get_nowait()
        if event["type"] == "answer_delta":
            deltas.append(event["delta"])
    assert deltas == ["新回答"]
