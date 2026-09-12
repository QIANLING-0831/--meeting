from interview_copilot.event_bus import EventBus
from interview_copilot.qwen_only import QwenOnlyEngine


def test_qwen_transcript_and_answer_are_saved_and_published(tmp_path):
    bus = EventBus()
    events = bus.subscribe()
    engine = QwenOnlyEngine(tmp_path, bus)
    engine.session, _ = engine.sessions.create(
        company="测试公司",
        position="后端工程师",
        jd_text="熟悉缓存",
        resume_path=None,
        knowledge_packs=[],
    )

    engine.on_qwen_event({"type": "fast_question_transcript", "itemId": "q1", "text": "如何处理缓存击穿？"})
    engine.on_qwen_event({"type": "fast_answer_started", "responseId": "r1"})
    engine.on_qwen_event({"type": "fast_answer_delta", "responseId": "r1", "delta": "可以使用互斥锁。"})
    engine.on_qwen_event({"type": "fast_answer_completed", "responseId": "r1", "status": "completed"})

    published = []
    while not events.empty():
        published.append(events.get_nowait())
    transcript = (engine.session.path / "interviewer-transcript.md").read_text(encoding="utf-8")
    answers = (engine.session.path / "answers.md").read_text(encoding="utf-8")

    assert any(event["type"] == "transcript" for event in published)
    assert "如何处理缓存击穿" in transcript
    assert "可以使用互斥锁" in answers
    assert engine.answer_snapshot()["running"] is False


def test_prompt_contains_jd_and_confirmed_candidate_facts(tmp_path):
    engine = QwenOnlyEngine(tmp_path, EventBus())
    engine.session, _ = engine.sessions.create(
        company="示例公司", position="Agent", jd_text="需要 RAG 经验", resume_path=None, knowledge_packs=[]
    )
    engine.sessions.update_candidate_facts(engine.session.id, "做过企业知识库项目")

    prompt = engine.instructions()

    assert "需要 RAG 经验" in prompt
    assert "做过企业知识库项目" in prompt
    assert "不得编造" in prompt
