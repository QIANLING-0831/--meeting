from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

from .codex_app_server import CodexAppServerClient, CodexAppServerError
from .config import AppConfig
from .event_bus import EventBus
from .knowledge import KnowledgeIndex
from .models import Speaker, TranscriptEntry
from .question_detector import QuestionDetector
from .question_assembler import QuestionAssembler
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
        self.question_assembler = QuestionAssembler()
        self.codex = CodexAppServerClient(root, self._on_codex_event)
        self.session: InterviewSession | None = None
        self.auto_answer = True
        self.active = False
        self._question_timer: threading.Timer | None = None
        self._answer_buffer = ""
        self._current_question = ""
        self._answer_prompt = ""
        self._answer_generation = 0
        self._answer_watchdog: threading.Timer | None = None
        self._first_token_retry_used = False
        self._obsolete_turn_ids: set[str] = set()

    def set_session(self, session: InterviewSession) -> None:
        self.session = session
        self.question_assembler.reset()

    def answer_snapshot(self) -> dict:
        return {
            "question": self._current_question,
            "text": self._answer_buffer,
            "running": bool(self.codex.turn_id),
        }

    def ingest_transcript(self, speaker: Speaker, text: str, final: bool = True) -> None:
        entry = TranscriptEntry(speaker=speaker, text=text.strip(), final=final)
        self.bus.publish({"type": "transcript", "entry": entry.to_dict()})
        if not final or not entry.text or not self.session:
            return
        filename = "interviewer-transcript.md" if speaker == "interviewer" else "candidate-transcript.md"
        label = "面试官" if speaker == "interviewer" else "我"
        self.sessions.append_text(self.session.id, filename, f"- [{entry.created_at}] **{label}：** {entry.text}")
        if speaker == "interviewer":
            direct_candidate = self.detector.detect(entry.text)
            answerable_text = self.question_assembler.add_interviewer(
                entry.text,
                is_question=direct_candidate is not None,
            )
            candidate = self.detector.detect(answerable_text)
            if candidate:
                self.bus.publish({"type": "question_candidate", "question": candidate.to_dict()})
                if self.active and self.auto_answer:
                    self.schedule_answer(candidate.text)
        else:
            self.question_assembler.reset()

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
        self._answer_generation += 1
        generation = self._answer_generation
        self._cancel_answer_watchdog()
        hits = self.knowledge.search(self._current_question, self.session.knowledge_packs, limit=6)
        self.bus.publish({"type": "knowledge_hits", "question": self._current_question, "hits": [hit.to_dict() for hit in hits]})
        prompt = self._build_prompt(self._current_question, hits)
        self._answer_prompt = prompt
        self._answer_buffer = ""
        self._first_token_retry_used = False
        self.bus.publish({"type": "answer_started", "question": self._current_question})
        try:
            self._start_codex_turn(prompt, reconnect_on_timeout=True)
            self._arm_answer_watchdog(generation)
        except Exception as exc:
            self._publish_error(f"Codex 调用失败：{exc}")

    def _start_codex_turn(self, prompt: str, *, reconnect_on_timeout: bool) -> None:
        try:
            self.codex.ensure_thread(model=self.config.codex_model, instructions=ANSWER_INSTRUCTIONS)
        except CodexAppServerError as exc:
            if not reconnect_on_timeout or "超时" not in str(exc):
                raise
            # No usable thread exists at this point, so rebuilding the local
            # process is the last-resort bootstrap path rather than the normal
            # response-timeout behavior.
            message = "Codex 启动超时，正在执行最后兜底重建（1/1）"
            self.bus.publish({"type": "answer_retrying", "message": message})
            self.bus.publish({"type": "notice", "message": message})
            self.codex.close()
            self.codex.ensure_thread(model=self.config.codex_model, instructions=ANSWER_INSTRUCTIONS)
        try:
            self.codex.start_turn(
                prompt,
                model=self.config.codex_model,
                effort=self.config.codex_reasoning_effort,
            )
        except CodexAppServerError as exc:
            if "超时" not in str(exc):
                raise
            # turn/start may already have reached Codex even if its RPC reply is
            # late. Keep the process and thread alive to avoid duplicate turns
            # and avoid destroying interview context.
            message = "Codex 请求确认较慢，保留原会话并继续等待"
            self.bus.publish({"type": "answer_retrying", "message": message})
            self.bus.publish({"type": "notice", "message": message})

    def _arm_answer_watchdog(self, generation: int) -> None:
        if self._answer_buffer or generation != self._answer_generation:
            return
        self._cancel_answer_watchdog()
        self._answer_watchdog = threading.Timer(
            self.config.answer_timeout_seconds,
            self._handle_first_token_timeout,
            args=(generation,),
        )
        self._answer_watchdog.daemon = True
        self._answer_watchdog.start()

    def _cancel_answer_watchdog(self) -> None:
        if self._answer_watchdog:
            self._answer_watchdog.cancel()
            self._answer_watchdog = None

    def _handle_first_token_timeout(self, generation: int) -> None:
        if generation != self._answer_generation or self._answer_buffer:
            return
        if self._first_token_retry_used:
            try:
                self.codex.interrupt()
            except Exception:
                pass
            self.codex.turn_id = None
            self._publish_error("Codex 原会话仍未返回，可按 F8 在同一会话中重试")
            return
        self._first_token_retry_used = True
        if not self.codex.turn_id:
            message = "Codex 请求状态尚未确认，继续等待原请求（不重复发送）"
            self.bus.publish({"type": "answer_retrying", "message": message})
            self.bus.publish({"type": "notice", "message": message})
            self._arm_answer_watchdog(generation)
            return
        message = "Codex 首字超时，正在原会话内重试（不重连）"
        self.bus.publish({"type": "answer_retrying", "message": message})
        self.bus.publish({"type": "notice", "message": message})
        old_turn_id = self.codex.turn_id
        if old_turn_id:
            self._obsolete_turn_ids.add(old_turn_id)
        try:
            self.codex.interrupt()
            self.codex.turn_id = None
            self._answer_buffer = ""
            self._start_codex_turn(self._answer_prompt, reconnect_on_timeout=False)
            self._arm_answer_watchdog(generation)
        except Exception as exc:
            self._publish_error(f"Codex 同会话重试失败：{exc}")

    def _publish_error(self, message: str) -> None:
        self.bus.publish({"type": "error", "message": message})
        if self.session:
            stamp = datetime.now().isoformat(timespec="seconds")
            self.sessions.append_text(
                self.session.id,
                "runtime-errors.md",
                f"- [{stamp}] {message}",
            )

    def _build_prompt(self, question, hits) -> str:
        assert self.session
        session_path = self.session.path
        facts = (session_path / "candidate-facts.md").read_text(encoding="utf-8")
        jd = (session_path / "jd.md").read_text(encoding="utf-8")
        evidence = "\n\n".join(
            f"[K{index}] 来源={hit.source} 标题={hit.title}\n{hit.content}"
            for index, hit in enumerate(hits[:4], start=1)
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
只有候选人事实允许使用“我做过/我负责”。
第一句先用15～30个字直接给出结论，让用户能立刻开始口述；随后再自然补充原理、步骤或取舍。"""
        return f"""请回答本次面试问题。

问题类型：{question_type}
面试官问题：{question}

本次JD：
{jd[:3000]}

已确认的候选人事实：
{facts[:6000]}

本地检索依据：
{evidence[:8000]}

输出格式：
{output_format}"""

    def _on_codex_event(self, event: dict) -> None:
        method = event.get("method")
        params = event.get("params") or {}
        event_turn_id = params.get("turnId") or (params.get("turn") or {}).get("id")
        if event_turn_id in self._obsolete_turn_ids:
            if method == "turn/completed":
                self._obsolete_turn_ids.discard(event_turn_id)
            return
        if method == "item/agentMessage/delta":
            delta = params.get("delta", "")
            if not self._answer_buffer:
                self._cancel_answer_watchdog()
            self._answer_buffer += delta
            self.bus.publish({"type": "answer_delta", "delta": delta})
        elif method == "turn/completed":
            self._cancel_answer_watchdog()
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
            self._publish_error(params.get("message", "Codex 服务异常"))
        elif method in ("account/updated", "account/login/completed"):
            self.bus.publish({"type": "codex_account", "data": params})
