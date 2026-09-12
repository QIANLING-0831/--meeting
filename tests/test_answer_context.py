from interview_copilot.answer_context import AnswerContextProvider
from interview_copilot.session import SessionManager


def test_selected_local_pack_is_retrieved_into_model_neutral_context(tmp_path):
    pack = tmp_path / "workspace" / "knowledge" / "packs" / "agent"
    pack.mkdir(parents=True)
    (pack / "workflow.md").write_text(
        "# Agent 工作流\nAgent 通常先规划任务，再选择工具执行并观察结果。",
        encoding="utf-8",
    )
    sessions = SessionManager(tmp_path / "workspace" / "sessions")
    session, _ = sessions.create(
        company="测试",
        position="Agent 工程师",
        jd_text="负责 Agent 工作流",
        resume_path=None,
        knowledge_packs=["agent"],
    )
    provider = AnswerContextProvider(tmp_path / "workspace", sessions)
    provider.knowledge.rebuild()

    context = provider.build(session, question="Agent 的工作流程是什么？")

    assert context.knowledge_packs == ("agent",)
    assert context.knowledge_hits
    assert "先规划任务" in context.text


def test_unchecked_local_pack_is_not_injected(tmp_path):
    pack = tmp_path / "workspace" / "knowledge" / "packs" / "agent"
    pack.mkdir(parents=True)
    (pack / "secret.md").write_text("只存在于本地知识库的内容", encoding="utf-8")
    sessions = SessionManager(tmp_path / "workspace" / "sessions")
    session, _ = sessions.create(
        company="测试",
        position="开发",
        jd_text="",
        resume_path=None,
        knowledge_packs=[],
    )
    provider = AnswerContextProvider(tmp_path / "workspace", sessions)
    provider.knowledge.rebuild()

    context = provider.build(session, question="本地内容是什么？")

    assert context.knowledge_packs == ()
    assert context.knowledge_hits == ()
    assert "只存在于本地知识库的内容" not in context.text
