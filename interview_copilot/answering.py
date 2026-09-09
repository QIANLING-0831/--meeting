from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

from .codex_app_server import CodexAppServerClient
from .config import AppConfig
from .event_bus import EventBus
from .knowledge import KnowledgeIndex
from .models import Speaker, TranscriptEntry
from .question_detector import QuestionDetector
from .session import InterviewSession, SessionManager


ANSWER_INSTRUCTIONS = """你是中文技术面试回答助手。只提供简洁、真实、可口述的建议。
严禁把知识库里的案例说成候选人亲历；个人经历只能来自 candidate-facts.md。
默认不联网，不修改任何文件。严格遵循每次问题中指定的输出格式。
若个人事实不足，明确写“需要结合你的真实经历补充”，不得编造公司、指标或职责。"""


class InterviewEngine:
    def __init__(self, root: Path, config: AppConfig, bus: EventBus) -> None:
        self.root = root
        self.config = config
        self.bus = bus
        self.sessions = SessionManager(root / "workspace" / "sessions")
        self.knowledge = KnowledgeIndex(root / "workspace")
        self.detector = QuestionDetector()
        self.codex = CodexAppServerClient(root, self._on_codex_event)
        self.session: InterviewSession | None = None
        self.auto_answer = True
        self.active = False
        self._question_timer: threading.Timer | None = None
        self._answer_buffer = ""
        self._current_question = ""

    def set_session(self, session: InterviewSession) -> None:
        self.session = session

    def ingest_transcript(self, speaker: Speaker, text: str, final: bool = True) -> None:
        entry = TranscriptEntry(speaker=speaker, text=text.strip(), final=final)
        self.bus.publish({"type": "transcript", "entry": entry.to_dict()})
        if not final or not entry.text or not self.session:
            return
        filename = "interviewer-transcript.md" if speaker == "interviewer" else "candidate-transcript.md"
        label = "面试官" if speaker == "interviewer" else "我"
        self.sessions.append_text(self.session.id, filename, f"- [{entry.created_at}] **{label}：** {entry.text}")
        if speaker == "interviewer":
            candidate = self.detector.detect(entry.text)
            if candidate:
                self.bus.publish({"type": "question_candidate", "question": candidate.to_dict()})
                if self.active and self.auto_answer:
                    self.schedule_answer(candidate.text)

    def schedule_answer(self, question: str, delay: float = 1.5) -> None:
        if self._question_timer:
            self._question_timer.cancel()
        self._question_timer = threading.Timer(delay, self.answer, args=(question,))
        self._question_timer.daemon = True
        self._question_timer.start()

    def answer(self, question: str) -> None:
        if not self.session:
            self.bus.publish({"type": "error", "message": "请先准备本次面试会话"})
            return
        if self.codex.turn_id:
            try:
                self.codex.interrupt()
            except Exception:
                pass
        self._current_question = question.strip()
        hits = self.knowledge.search(self._current_question, self.session.knowledge_packs, limit=6)
        self.bus.publish({"type": "knowledge_hits", "question": self._current_question, "hits": [hit.to_dict() for hit in hits]})
        prompt = self._build_prompt(self._current_question, hits)
        self._answer_buffer = ""
        self.bus.publish({"type": "answer_started", "question": self._current_question})
        try:
            self.codex.ensure_thread(model=self.config.codex_model, instructions=ANSWER_INSTRUCTIONS)
            self.codex.start_turn(
                prompt,
                model=self.config.codex_model,
                effort=self.config.codex_reasoning_effort,
            )
        except Exception as exc:
            self.bus.publish({"type": "error", "message": f"Codex 调用失败：{exc}"})

    def _build_prompt(self, question, hits) -> str:
        assert self.session
        session_path = self.session.path
        facts = (session_path / "candidate-facts.md").read_text(encoding="utf-8")
        jd = (session_path / "jd.md").read_text(encoding="utf-8")
        evidence = "\n\n".join(
            f"[K{index}] 来源={hit.source} 标题={hit.title}\n{hit.content}"
            for index, hit in enumerate(hits, start=1)
        ) or "未命中本地知识库，请依靠通用知识并标注为模型补充。"
        question_type = self.detector.classify(question)
        if self.config.include_core_points:
            output_format = """### 核心要点
- 3到4条，每条只写一句，并在末尾标注 [项目]、[简历]、[K1] 或 [模型补充]
### 30～45秒口语版
一段自然中文，控制在140～220个汉字。只有候选人事实允许使用“我做过/我负责”。"""
        else:
            output_format = """只输出一段30～45秒可直接口述的自然中文，控制在140～220个汉字。
不要标题、不要核心要点列表、不要可能追问、不要展示来源标签。
只有候选人事实允许使用“我做过/我负责”。开头直接回答问题。"""
        return f"""请回答本次面试问题。

问题类型：{question_type}
面试官问题：{question}

本次JD：
{jd[:6000]}

已确认的候选人事实：
{facts[:6000]}

本地检索依据：
{evidence[:12000]}

输出格式：
{output_format}"""

    def _on_codex_event(self, event: dict) -> None:
        method = event.get("method")
        params = event.get("params") or {}
        if method == "item/agentMessage/delta":
            delta = params.get("delta", "")
            self._answer_buffer += delta
            self.bus.publish({"type": "answer_delta", "delta": delta})
        elif method == "turn/completed":
            status = (params.get("turn") or {}).get("status", "completed")
            if self.session and self._answer_buffer.strip():
                stamp = datetime.now().isoformat(timespec="seconds")
                self.sessions.append_text(
                    self.session.id,
                    "answers.md",
                    f"\n## {stamp} {self._current_question}\n\n{self._answer_buffer.strip()}\n",
                )
            self.bus.publish({"type": "answer_completed", "status": status})
            self.codex.turn_id = None
        elif method == "server/error":
            self.bus.publish({"type": "error", "message": params.get("message", "Codex 服务异常")})
        elif method in ("account/updated", "account/login/completed"):
            self.bus.publish({"type": "codex_account", "data": params})
