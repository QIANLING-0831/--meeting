from __future__ import annotations

import threading
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
        self._last_transcript_item = ""
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
            }

    def instructions(self) -> str:
        if not self.session:
            return ""
        jd = (self.session.path / "jd.md").read_text(encoding="utf-8").strip()
        facts = (self.session.path / "candidate-facts.md").read_text(encoding="utf-8").strip()
        return f"""你是候选人的实时面试回答助手。请根据面试官的问题，用中文输出候选人可以马上口述的回答。

规则：
1. 只回答面试官刚刚提出的完整问题，不要复述题目，不要寒暄。
2. 默认控制在 30～45 秒口述长度，先给结论，再给依据或例子。
3. 只能把“候选人事实”中的内容说成亲身经历；资料没有写明的经历、数字和成果不得编造。
4. 技术题可以结合通用知识回答，但要清楚区分通用方案与候选人的真实经历。
5. 听到停顿、语气词或不完整句时继续等待；不要抢答。

岗位信息：
公司：{self.session.company}
岗位：{self.session.position}

岗位 JD：
{jd[:12000]}

候选人事实（含简历和项目描述）：
{facts[:18000]}
""".strip()

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
                self._answer = ""
                self._answer_running = True
        elif event_type == "fast_answer_delta":
            with self._lock:
                if not self._response_id or event.get("responseId") == self._response_id:
                    self._answer += str(event.get("delta", ""))
        elif event_type == "fast_answer_text_done":
            text = str(event.get("text", "")).strip()
            with self._lock:
                if text and (not self._response_id or event.get("responseId") == self._response_id):
                    self._answer = text
        elif event_type == "fast_answer_completed":
            with self._lock:
                self._answer_running = False
                question = self._question
                answer = self._answer.strip()
            if self.session and answer:
                self.sessions.append_text(
                    self.session.id,
                    "answers.md",
                    f"\n## {question or '实时回答'}\n\n{answer}\n",
                )
        elif event_type == "fast_answer_error":
            with self._lock:
                self._answer_running = False
        self.bus.publish(event)
