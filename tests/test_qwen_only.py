from interview_copilot.event_bus import EventBus
from interview_copilot.qwen_only import QwenOnlyEngine
from interview_copilot.external_sources import ImportedSource


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
    assert "补全一套合理、可落地" in prompt
    assert "绝对不要输出" in prompt
    assert "用户没有补充" in prompt
    assert "不要捏造精确指标" in prompt


def test_prompt_contains_external_source_and_keeps_active_follow_up_chain(tmp_path):
    bus = EventBus()
    events = bus.subscribe()
    engine = QwenOnlyEngine(tmp_path, bus)
    engine.session, _ = engine.sessions.create(
        company="示例", position="Agent", jd_text="", resume_path=None, knowledge_packs=[]
    )
    engine.sessions.add_external_source(
        engine.session.id,
        ImportedSource("Agent 题库", "https://github.com/example/questions", "什么是 Agent Loop？"),
    )
    for index in range(3):
        engine.on_qwen_event(
            {"type": "fast_question_transcript", "itemId": f"q{index}", "text": f"请介绍第{index}个项目的实现流程"}
        )
    engine.on_qwen_event({"type": "fast_question_transcript", "itemId": "filler", "text": "嗯。"})

    assert "Agent Loop" in engine.instructions()
    assert engine.answer_snapshot()["recentQuestions"] == [
        "请介绍第0个项目的实现流程",
        "请介绍第1个项目的实现流程",
        "请介绍第2个项目的实现流程",
    ]
    assert any(event["type"] == "question_memory" for event in list(events.queue))


def test_candidate_answer_updates_context_without_triggering_an_answer(tmp_path, monkeypatch):
    engine = QwenOnlyEngine(tmp_path, EventBus())
    engine.session, _ = engine.sessions.create(
        company="示例", position="Agent", jd_text="", resume_path=None, knowledge_packs=[]
    )
    calls = []
    class AnswerCall:
        def __call__(self, *args):
            calls.append(args)
    monkeypatch.setattr(engine.text_answerer, "answer", AnswerCall())

    engine.on_asr_text("candidate", "我采用了 ReAct 循环，并为工具调用设置超时。", True)

    assert calls == []
    assert "ReAct 循环" in engine.instructions()
    assert engine.answer_snapshot()["candidateAnswers"] == ["我采用了 ReAct 循环，并为工具调用设置超时。"]
    assert "ReAct 循环" in (engine.session.path / "candidate-transcript.md").read_text(encoding="utf-8")


def test_explicit_new_topic_resets_follow_up_chain(tmp_path):
    engine = QwenOnlyEngine(tmp_path, EventBus())
    engine.session, _ = engine.sessions.create(
        company="示例", position="Agent", jd_text="", resume_path=None, knowledge_packs=[]
    )
    engine.on_qwen_event({"type": "fast_question_transcript", "itemId": "q1", "text": "你的 Agent 工作流程是什么？"})
    engine.on_asr_text("candidate", "先规划再调用工具。", True)
    engine.on_qwen_event({"type": "fast_question_transcript", "itemId": "q2", "text": "下一个问题，如何处理缓存击穿？"})

    assert engine.answer_snapshot()["recentQuestions"] == ["下一个问题，如何处理缓存击穿？"]
    assert engine.answer_snapshot()["candidateAnswers"] == []


def test_interviewer_echo_from_microphone_is_not_candidate_context(tmp_path):
    engine = QwenOnlyEngine(tmp_path, EventBus())
    engine.session, _ = engine.sessions.create(
        company="示例", position="Agent", jd_text="", resume_path=None, knowledge_packs=[]
    )
    engine.on_qwen_event({"type": "fast_question_transcript", "itemId": "q1", "text": "请介绍一下 Agent 的工作流程？"})

    engine.on_asr_text("candidate", "请介绍一下 Agent 的工作流程", True)

    assert engine.answer_snapshot()["candidateAnswers"] == []


def test_new_question_does_not_replace_visible_pair_until_first_answer_text(tmp_path):
    engine = QwenOnlyEngine(tmp_path, EventBus())
    engine.session, _ = engine.sessions.create(
        company="示例", position="Agent", jd_text="", resume_path=None, knowledge_packs=[]
    )
    engine.on_qwen_event({"type": "fast_question_transcript", "itemId": "q1", "text": "什么是 Agent Loop？"})
    engine.on_qwen_event({"type": "fast_answer_started", "responseId": "r1"})
    engine.on_qwen_event({"type": "fast_answer_delta", "responseId": "r1", "delta": "Agent Loop 是规划、执行和观察的循环。"})
    engine.on_qwen_event({"type": "fast_answer_completed", "responseId": "r1", "status": "completed"})

    engine.on_qwen_event({"type": "fast_question_transcript", "itemId": "q2", "text": "工具调用失败怎么处理？"})
    waiting = engine.answer_snapshot()
    assert waiting["question"] == "什么是 Agent Loop？"
    assert waiting["pendingQuestion"] == "工具调用失败怎么处理？"
    assert "规划、执行" in waiting["text"]

    engine.on_qwen_event({"type": "fast_answer_started", "responseId": "r2"})
    engine.on_qwen_event({"type": "fast_answer_delta", "responseId": "r2", "delta": "我会设置超时与重试。"})
    promoted = engine.answer_snapshot()
    assert promoted["question"] == "工具调用失败怎么处理？"
    assert promoted["text"] == "我会设置超时与重试。"
    assert promoted["history"][0]["question"] == "什么是 Agent Loop？"
    assert "规划、执行" in promoted["history"][0]["answer"]


def test_empty_new_response_keeps_previous_question_and_answer(tmp_path):
    engine = QwenOnlyEngine(tmp_path, EventBus())
    engine.session, _ = engine.sessions.create(
        company="示例", position="Agent", jd_text="", resume_path=None, knowledge_packs=[]
    )
    engine.on_qwen_event({"type": "fast_question_transcript", "itemId": "q1", "text": "请介绍一下项目。"})
    engine.on_qwen_event({"type": "fast_answer_started", "responseId": "r1"})
    engine.on_qwen_event({"type": "fast_answer_text_done", "responseId": "r1", "text": "这是一个知识库项目。"})
    engine.on_qwen_event({"type": "fast_answer_completed", "responseId": "r1", "status": "completed"})
    before = engine.answer_snapshot()

    engine.on_qwen_event({"type": "fast_question_transcript", "itemId": "noise", "text": "嗯。"})
    engine.on_qwen_event({"type": "fast_answer_started", "responseId": "r2"})
    engine.on_qwen_event({"type": "fast_answer_text_done", "responseId": "r2", "text": ""})
    engine.on_qwen_event({"type": "fast_answer_completed", "responseId": "r2", "status": "completed"})

    after = engine.answer_snapshot()
    assert after["question"] == before["question"]
    assert after["text"] == before["text"]
    assert after["history"] == []


def test_candidate_semantics_marks_interrupted_answer_and_reading_progress(tmp_path):
    engine = QwenOnlyEngine(tmp_path, EventBus())
    engine.session, _ = engine.sessions.create(
        company="示例", position="Agent", jd_text="", resume_path=None, knowledge_packs=[]
    )
    engine.on_qwen_event({"type": "fast_question_transcript", "itemId": "q1", "text": "怎么设计重试？"})
    engine.on_qwen_event({"type": "fast_answer_started", "responseId": "r1"})
    engine.on_qwen_event({"type": "fast_answer_text_done", "responseId": "r1", "text": "我会先设置超时和重试。然后增加熔断保护。"})
    engine.on_asr_text("candidate", "我会先设置超时和重试。然后", True)

    speaking = engine.answer_snapshot()
    assert speaking["candidateState"] == "answering"
    assert speaking["coveredSentenceCount"] == 1

    engine.on_qwen_event({"type": "fast_question_transcript", "itemId": "q2", "text": "那熔断怎么恢复？"})
    assert engine.answer_snapshot()["candidateState"] == "interrupted"
