from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .knowledge import KnowledgeIndex
from .models import KnowledgeHit
from .session import InterviewSession, SessionManager


@dataclass(frozen=True)
class AnswerContext:
    """Model-neutral context assembled for one answer request."""

    text: str
    knowledge_hits: tuple[KnowledgeHit, ...]
    knowledge_packs: tuple[str, ...]


class AnswerContextProvider:
    """One seam through which every answer model receives interview context.

    Model adapters do not need to know where resumes, local FTS knowledge,
    imported sources, or follow-up history are stored.  They receive one
    bounded text context built by this module.
    """

    def __init__(self, workspace_root: Path, sessions: SessionManager) -> None:
        self.knowledge = KnowledgeIndex(workspace_root)
        self.sessions = sessions

    def available_packs(self) -> list[str]:
        return self.knowledge.list_packs()

    def build(
        self,
        session: InterviewSession,
        *,
        question: str = "",
        topic_questions: Iterable[str] = (),
        candidate_answers: Iterable[str] = (),
    ) -> AnswerContext:
        jd = (session.path / "jd.md").read_text(encoding="utf-8").strip()
        facts = (session.path / "candidate-facts.md").read_text(encoding="utf-8").strip()
        # An empty selection intentionally disables local retrieval. The workbench
        # owns the selection; every answer-model adapter receives the result.
        packs = session.knowledge_packs
        query = question.strip() or " ".join(
            part for part in (session.position, jd[:1800], facts[:1200]) if part.strip()
        )
        hits = tuple(self.knowledge.search(query, packs, limit=6)) if query and packs else ()
        external = self.sessions.external_knowledge(session.id, limit=10_000)

        sections = [
            f"公司：{session.company}\n岗位：{session.position}",
            f"岗位 JD：\n{jd[:10_000]}",
            f"候选人事实（含简历和项目描述）：\n{facts[:16_000]}",
        ]
        if external:
            sections.append(
                "外部题库与项目参考（可用于合理补全技术方案，但不得据此编造精确硬事实）：\n"
                f"{external}"
            )
        if hits:
            local = []
            for hit in hits:
                local.append(f"### {hit.title}\n来源：{hit.source}\n{hit.content}")
            sections.append(
                "本地知识库按题检索结果（可用于合理补全技术方案，但不得据此编造精确硬事实）：\n"
                + "\n\n".join(local)[:10_000]
            )

        questions = [item.strip() for item in topic_questions if item.strip()]
        answers = [item.strip() for item in candidate_answers if item.strip()]
        if questions or answers:
            lines = ["面试官当前追问链："]
            lines.extend(f"- {item}" for item in questions)
            if answers:
                lines.append("候选人最近回答：")
                lines.extend(f"- {item}" for item in answers)
            sections.append("当前面试上下文：\n" + "\n".join(lines))

        return AnswerContext(
            text="\n\n".join(sections),
            knowledge_hits=hits,
            knowledge_packs=tuple(packs),
        )
