from interview_copilot.knowledge import KnowledgeIndex


def test_rebuild_and_search_chinese_markdown(tmp_path):
    pack = tmp_path / "knowledge" / "packs" / "agent"
    pack.mkdir(parents=True)
    (pack / "memory.md").write_text(
        "# Agent 记忆\n短期记忆保存当前对话上下文，长期记忆通过检索召回。",
        encoding="utf-8",
    )
    index = KnowledgeIndex(tmp_path)
    result = index.rebuild()
    hits = index.search("Agent 的长期记忆如何检索？", ["agent"])
    assert result == {"files": 1, "chunks": 1}
    assert hits
    assert "长期记忆" in hits[0].content


def test_answer_preview_keeps_expert_answer_and_hides_metadata(tmp_path):
    index = KnowledgeIndex(tmp_path)
    content = """### Q：你用 ReAct 还是 Plan-and-Execute？为什么？

> 来源：腾讯 Agent 岗终面 【其他同题】

**新手答**：“看情况。”

**高手答**：

ReAct 根据环境反馈动态决定下一步，适合不确定任务。
Plan-and-Execute 先规划后执行，适合步骤清晰的复杂任务。
"""
    preview = index.answer_preview(content)
    assert "ReAct 根据环境反馈" in preview
    assert "Plan-and-Execute 先规划" in preview
    assert "来源" not in preview
    assert "Q：" not in preview
    assert "新手答" not in preview


def test_search_promotes_substantive_interview_answer(tmp_path):
    pack = tmp_path / "knowledge" / "packs" / "agent"
    pack.mkdir(parents=True)
    (pack / "intro.md").write_text("ReAct 与 Plan-and-Execute 是两类 Agent 架构。", encoding="utf-8")
    (pack / "answer.md").write_text(
        """### Q：ReAct 和 Plan-and-Execute 怎么选？
> 来源：面试题
**高手答**：
ReAct 适合需要根据环境反馈动态决策的任务；Plan-and-Execute 适合步骤明确、需要审计的任务。
具体工程中也可以外层规划、内层 ReAct。
""",
        encoding="utf-8",
    )
    index = KnowledgeIndex(tmp_path)
    index.rebuild()
    hits = index.search("ReAct 和 Plan-and-Execute 怎么选", ["agent"], limit=2)
    assert hits[0].source.endswith("answer.md")
    assert "来源" not in hits[0].answer_preview


def test_search_recalls_two_character_topic_from_natural_question(tmp_path):
    pack = tmp_path / "knowledge" / "packs" / "agent"
    pack.mkdir(parents=True)
    (pack / "architecture-distractors.md").write_text(
        "\n".join(
            f"### Q：AI Agent 架构设计问题 {index}\n使用 AI 完成通用任务。"
            for index in range(100)
        ),
        encoding="utf-8",
    )
    (pack / "hallucination.md").write_text(
        """### Q：Agent 如何减少幻觉？
**高手答**：
用事实约束、结构化输出、工具结果校验和人工兜底组成多层防线。
""",
        encoding="utf-8",
    )
    index = KnowledgeIndex(tmp_path)
    index.rebuild()

    hits = index.search("AI 产生幻觉了怎么办？", ["agent"])

    assert hits
    assert hits[0].source.endswith("hallucination.md")
    assert "多层防线" in hits[0].answer_preview


def test_framework_selection_question_does_not_rank_tool_routing_first(tmp_path):
    pack = tmp_path / "knowledge" / "packs" / "agent"
    pack.mkdir(parents=True)
    (pack / "tools.md").write_text(
        """### Q：你们工具库有上百个工具，怎么让模型快速选对？
**高手答**：先做工具检索，再让模型选择候选工具。
""",
        encoding="utf-8",
    )
    (pack / "frameworks.md").write_text(
        """### Q：LangChain 和 LangGraph 有什么区别？分别适合什么场景？
**高手答**：LangChain 适合线性流程；LangGraph 适合有状态图、循环和断点恢复。框架选型要结合控制流和状态管理。
""",
        encoding="utf-8",
    )
    index = KnowledgeIndex(tmp_path)
    index.rebuild()

    hits = index.search(
        "你们这个 Agent 项目是用什么框架？为什么最后选择这个，当时怎么做选型的，你都了解哪些框架？",
        ["agent"],
    )

    assert hits
    assert hits[0].source.endswith("frameworks.md")


def test_search_returns_no_hit_instead_of_unrelated_tool_question(tmp_path):
    pack = tmp_path / "knowledge" / "packs" / "agent"
    pack.mkdir(parents=True)
    (pack / "tools.md").write_text(
        """### Q：你们工具库有上百个工具，怎么让模型快速选对？
**高手答**：使用分层路由和语义检索缩小候选工具范围。
""",
        encoding="utf-8",
    )
    index = KnowledgeIndex(tmp_path)
    index.rebuild()

    hits = index.search("你们现场使用什么框架，如何做框架选型？", ["agent"])

    assert hits == []
