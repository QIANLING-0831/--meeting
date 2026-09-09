from interview_copilot.answering import InterviewEngine
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
