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
