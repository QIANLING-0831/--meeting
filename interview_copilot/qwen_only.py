from __future__ import annotations

import threading
from collections import deque
from pathlib import Path

from .event_bus import EventBus
from .models import TranscriptEntry
from .qwen_dual import QwenTextAnswerer, extract_hotwords
from .session import InterviewSession, SessionManager


class QwenOnlyEngine:
    """Small session/state layer for the single Qwen Realtime pipeline."""

    def __init__(
        self,
        root: Path,
        bus: EventBus,
        answer_model: str = "qwen-plus",
        workspace_id: str = "",
    ) -> None:
        self.sessions = SessionManager(root / "workspace" / "sessions")
        self.bus = bus
        self.session: InterviewSession | None = None
        self.active = False
        self._lock = threading.Lock()
        self._question = ""
        self._answer = ""
        self._response_id = ""
        self._answer_running = False
        self._answer_touched = False
        self._last_transcript_item = ""
        self._recent_questions: deque[str] = deque(maxlen=2)
        self.text_answerer = QwenTextAnswerer(
            self.on_qwen_event, answer_model, workspace_id=workspace_id
        )

    def set_session(self, session: InterviewSession) -> None:
        self.session = session

    def answer_snapshot(self) -> dict:
        with self._lock:
            return {
                "question": self._question,
                "text": self._answer,
                "responseId": self._response_id,
                "running": self._answer_running,
                "recentQuestions": list(self._recent_questions),
            }

    def instructions(self) -> str:
        if not self.session:
            return ""
        jd = (self.session.path / "jd.md").read_text(encoding="utf-8").strip()
        facts = (self.session.path / "candidate-facts.md").read_text(encoding="utf-8").strip()
        external = self.sessions.external_knowledge(self.session.id)
        return f"""你是候选人的实时面试回答助手。请根据面试官的问题，用中文输出候选人可以马上口述的回答。

规则：
1. 只回答面试官刚刚提出的完整问题，不要复述题目，不要寒暄。
2. 默认控制在 30～45 秒口述长度，先给结论，再给依据或例子。
3. 只能把“候选人事实”中的内容说成亲身经历；资料没有写明的经历、数字和成果不得编造。
4. 技术题可以结合通用知识回答，但要清楚区分通用方案与候选人的真实经历。
5. 听到停顿、语气词或不完整句时继续等待；不要抢答。
6. 只保留最近两道完整技术问题作为上下文。当前话轮若只是“嗯、啊、好的”等语气词、残句或无关对话，输出空字符串，不覆盖上一道问题。
7. 当前话轮若是追问、指代或被打断后的补充，结合最近两道技术问题还原真实意图后继续回答；只有明确的新问题才切换主题。

岗位信息：
公司：{self.session.company}
岗位：{self.session.position}

岗位 JD：
{jd[:12000]}

候选人事实（含简历和项目描述）：
{facts[:18000]}

外部题库与项目参考（只用于预测问题和补充通用技术知识，不得当作候选人亲历）：
{external}
""".strip()

    @staticmethod
    def _looks_like_question(text: str) -> bool:
        if len(text.strip()) < 4 or text.strip("，。！？? ") in {"嗯", "啊", "好的", "行吧", "知道了"}:
            return False
        markers = ("?", "？", "吗", "呢", "怎么", "如何", "为什么", "什么", "介绍", "说说", "讲讲", "谈谈", "区别", "流程", "原理", "设计", "实现", "项目", "经验", "负责", "优缺点", "场景")
        return any(marker in text for marker in markers)

    def _remember_question(self, text: str) -> None:
        if self._looks_like_question(text) and (not self._recent_questions or self._recent_questions[-1] != text):
            self._recent_questions.append(text)
            self.bus.publish({"type": "question_memory", "questions": list(self._recent_questions)})

    def asr_context(self) -> str:
        if not self.session:
            return ""
        jd = (self.session.path / "jd.md").read_text(encoding="utf-8")
        facts = (self.session.path / "candidate-facts.md").read_text(encoding="utf-8")
        return (f"岗位：{self.session.position}。技术领域和专业词汇：{jd}\n{facts}")[:400]

    def asr_vocabulary(self) -> dict[str, int]:
        if not self.session:
            return {}
        source = "\n".join(
            [
                self.session.position,
                (self.session.path / "jd.md").read_text(encoding="utf-8"),
                (self.session.path / "candidate-facts.md").read_text(encoding="utf-8"),
            ]
        )
        return extract_hotwords(source)

    def on_asr_text(self, _speaker: str, text: str, final: bool) -> None:
        clean = text.strip()
        if not clean:
            return
        if not final:
            self.bus.publish({"type": "qwen_transcript_delta", "text": clean})
            return
        entry = TranscriptEntry(speaker="interviewer", text=clean, final=True)
        with self._lock:
            self._question = clean
            self._remember_question(clean)
        if self.session:
            self.sessions.append_text(
                self.session.id,
                "interviewer-transcript.md",
                f"- [{entry.created_at}] {clean}",
            )
        self.bus.publish({"type": "transcript", "entry": entry.to_dict()})
        self.bus.publish({"type": "fast_question_transcript", "text": clean})
        self.text_answerer.answer(clean, self.instructions())

    def on_qwen_event(self, event: dict) -> None:
        event_type = event.get("type")
        if event_type == "fast_question_transcript":
            text = str(event.get("text", "")).strip()
            item_id = str(event.get("itemId", "")).strip()
            transcript_key = item_id or text
            if text and transcript_key != self._last_transcript_item:
                self._last_transcript_item = transcript_key
                entry = TranscriptEntry(speaker="interviewer", text=text, final=True)
                with self._lock:
                    self._question = text
                    self._remember_question(text)
                if self.session:
                    self.sessions.append_text(
                        self.session.id,
                        "interviewer-transcript.md",
                        f"- [{entry.created_at}] {text}",
                    )
                self.bus.publish({"type": "transcript", "entry": entry.to_dict()})
        elif event_type == "fast_answer_started":
            with self._lock:
                self._response_id = str(event.get("responseId", ""))
                self._answer_touched = False
                self._answer_running = True
        elif event_type == "fast_answer_delta":
            with self._lock:
                if not self._response_id or event.get("responseId") == self._response_id:
                    if not self._answer_touched:
                        self._answer = ""
                        self._answer_touched = True
                    self._answer += str(event.get("delta", ""))
        elif event_type == "fast_answer_text_done":
            text = str(event.get("text", "")).strip()
            with self._lock:
                if text and (not self._response_id or event.get("responseId") == self._response_id):
                    self._answer = text
                    self._answer_touched = True
        elif event_type == "fast_answer_completed":
            with self._lock:
                self._answer_running = False
                question = self._question
                answer = self._answer.strip()
                answer_touched = self._answer_touched
            if self.session and answer and answer_touched:
                self.sessions.append_text(
                    self.session.id,
                    "answers.md",
                    f"\n## {question or '实时回答'}\n\n{answer}\n",
                )
        elif event_type == "fast_answer_error":
            with self._lock:
                self._answer_running = False
        self.bus.publish(event)
