from __future__ import annotations

import json
import re
import threading
from collections import deque
from difflib import SequenceMatcher
from pathlib import Path

from .answer_context import AnswerContextProvider
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
        self._pending_question = ""
        self._answer = ""
        self._response_id = ""
        self._answer_running = False
        self._answer_error = ""
        self._answer_touched = False
        self._last_transcript_item = ""
        # Keep the active follow-up chain instead of mechanically retaining two
        # questions.  A clear topic-switch phrase starts a new chain; the cap is
        # only a safety bound for prompt size during a long interview.
        self._topic_questions: deque[str] = deque(maxlen=10)
        self._candidate_answers: deque[str] = deque(maxlen=8)
        self._answer_history: deque[dict[str, str]] = deque(maxlen=20)
        self._candidate_spoken = ""
        self._candidate_state = "idle"
        self.context_provider = AnswerContextProvider(root / "workspace", self.sessions)
        self.text_answerer = QwenTextAnswerer(
            self.on_qwen_event, answer_model, workspace_id=workspace_id
        )

    def set_session(self, session: InterviewSession) -> None:
        self.session = session
        state_path = session.path / "overlay-state.json"
        with self._lock:
            self._question = ""
            self._pending_question = ""
            self._answer = ""
            self._answer_error = ""
            self._response_id = ""
            self._answer_running = False
            self._answer_touched = False
            self._last_transcript_item = ""
            self._topic_questions.clear()
            self._candidate_answers.clear()
            self._answer_history.clear()
            self._candidate_spoken = ""
            self._candidate_state = "idle"
            if state_path.exists():
                try:
                    state = json.loads(state_path.read_text(encoding="utf-8"))
                    self._question = str(state.get("question", ""))
                    self._answer = str(state.get("answer", ""))
                    self._answer_history.extend(state.get("history", [])[:20])
                    self._candidate_spoken = str(state.get("candidateSpoken", ""))
                    self._candidate_state = str(state.get("candidateState", "idle"))
                except (OSError, ValueError, TypeError):
                    pass

    @staticmethod
    def _covered_sentence_count(answer: str, spoken: str) -> int:
        """Estimate reading progress only when the spoken text strongly matches."""
        normalized_spoken = "".join(spoken.lower().split())
        if len(normalized_spoken) < 8:
            return 0
        sentences = [part.strip() for part in re.split(r"(?<=[。！？!?；;])", answer) if part.strip()]
        covered = 0
        for sentence in sentences:
            normalized = "".join(sentence.lower().split()).strip("，。！？!?；;：:")
            if len(normalized) < 5:
                continue
            direct = normalized in normalized_spoken
            match = SequenceMatcher(None, normalized, normalized_spoken).find_longest_match(
                0, len(normalized), 0, len(normalized_spoken)
            )
            if direct or match.size >= max(7, int(len(normalized) * 0.58)):
                covered += 1
                continue
            break
        return covered

    def answer_snapshot(self) -> dict:
        with self._lock:
            return {
                "question": self._question,
                "pendingQuestion": self._pending_question,
                "text": self._answer,
                "responseId": self._response_id,
                "running": self._answer_running,
                "error": self._answer_error,
                "recentQuestions": list(self._topic_questions),
                "candidateAnswers": list(self._candidate_answers),
                "history": list(self._answer_history),
                "candidateSpoken": self._candidate_spoken,
                "candidateState": self._candidate_state,
                "coveredSentenceCount": self._covered_sentence_count(
                    self._answer, self._candidate_spoken
                ),
            }

    def _save_display_state(self) -> None:
        if not self.session:
            return
        state = {
            "question": self._question,
            "answer": self._answer,
            "history": list(self._answer_history),
            "candidateSpoken": self._candidate_spoken,
            "candidateState": self._candidate_state,
        }
        try:
            (self.session.path / "overlay-state.json").write_text(
                json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            pass

    def _stage_question(self, text: str) -> None:
        if text != self._question:
            self._pending_question = text
        if self._candidate_state == "answering":
            self._candidate_state = "interrupted"
        elif self._candidate_state == "likely_complete":
            self._candidate_state = "answered"

    def _promote_pending_answer(self) -> None:
        if self._answer and self._question:
            self._answer_history.appendleft(
                {
                    "question": self._question,
                    "answer": self._answer,
                    "candidateStatus": self._candidate_state,
                }
            )
        if self._pending_question:
            self._question = self._pending_question
        self._pending_question = ""
        self._answer = ""
        self._candidate_spoken = ""
        self._candidate_state = "waiting"

    def _publish_answer_snapshot(self) -> None:
        self.bus.publish({"type": "answer_snapshot", "snapshot": self.answer_snapshot()})

    def instructions(self, question: str = "") -> str:
        if not self.session:
            return ""
        with self._lock:
            resolved_question = question.strip() or self._question
            topic_questions = list(self._topic_questions)
            candidate_answers = list(self._candidate_answers)
        context = self.context_provider.build(
            self.session,
            question=resolved_question,
            topic_questions=topic_questions,
            candidate_answers=candidate_answers,
        )
        if context.knowledge_hits:
            self.bus.publish(
                {
                    "type": "knowledge_hits",
                    "question": resolved_question,
                    "hits": [hit.to_dict() for hit in context.knowledge_hits],
                    "packs": list(context.knowledge_packs),
                }
            )
        return f"""你是候选人的实时面试回答助手。请根据面试官的问题，用中文输出候选人可以马上口述的回答。

规则：
1. 只回答面试官刚刚提出的完整问题，不要复述题目，不要寒暄。
2. 默认控制在 30～45 秒口述长度，先给结论，再给依据或例子。
3. 以“候选人事实”为真实锚点；缺少具体技术细节时，根据项目类型、已有技术栈和知识库，补全一套合理、可落地且前后一致的实现方案，直接用候选人第一人称回答。
4. 绝对不要输出“资料没有提供”“用户没有补充”“需要结合真实经历补充”“无法回答”等元话术，也不要要求候选人现场补资料。
5. 可以合理补全架构、技术选型、实现步骤、异常处理和优化思路；没有依据时不要捏造精确指标、团队人数、客户名称、奖项或线上事故等容易核验的硬事实。
6. 听到停顿、语气词或不完整句时继续等待；不要抢答。
7. 当前话轮若只是“嗯、啊、好的”等语气词、残句或无关对话，输出空字符串，不覆盖上一道问题。
8. 当前话轮若是追问、指代或被打断后的补充，结合“当前追问链”和候选人刚才的回答还原真实意图；只有面试官明确切换话题时才切换主题。
9. 候选人回答仅用于判断面试官后续追问所指内容、避免前后矛盾，不得把候选人的话当成面试官的新问题。

统一回答上下文（由模型无关的上下文模块提供）：
{context.text}
""".strip()

    @staticmethod
    def _looks_like_question(text: str) -> bool:
        if len(text.strip()) < 4 or text.strip("，。！？? ") in {"嗯", "啊", "好的", "行吧", "知道了"}:
            return False
        markers = ("?", "？", "吗", "呢", "怎么", "如何", "为什么", "什么", "介绍", "说说", "讲讲", "谈谈", "区别", "流程", "原理", "设计", "实现", "项目", "经验", "负责", "优缺点", "场景")
        return any(marker in text for marker in markers)

    def _remember_question(self, text: str) -> None:
        if not self._looks_like_question(text):
            return
        topic_switches = ("下一个问题", "换个话题", "换一个话题", "另外一个问题", "接下来问")
        if any(marker in text for marker in topic_switches):
            self._topic_questions.clear()
            self._candidate_answers.clear()
        if not self._topic_questions or self._topic_questions[-1] != text:
            self._topic_questions.append(text)
            self.bus.publish({"type": "question_memory", "questions": list(self._topic_questions)})

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

    def on_asr_text(self, speaker: str, text: str, final: bool) -> None:
        clean = text.strip()
        if not clean:
            return
        if not final:
            if speaker == "candidate":
                with self._lock:
                    self._candidate_state = "answering"
            self.bus.publish({"type": "qwen_transcript_delta", "speaker": speaker, "text": clean})
            if speaker == "candidate":
                self._publish_answer_snapshot()
            return
        if speaker == "candidate":
            # Laptop speakers can leak the interviewer's voice back into the
            # microphone. Do not mistake a near-duplicate question for the
            # candidate's answer context.
            with self._lock:
                possible_echoes = (self._question, self._pending_question)
            comparable = lambda value: "".join(value.lower().split()).strip("，。！？?")
            if any(
                question
                and SequenceMatcher(None, comparable(clean), comparable(question)).ratio() >= 0.82
                for question in possible_echoes
            ):
                self.bus.publish({"type": "candidate_echo_ignored", "text": clean})
                return
            entry = TranscriptEntry(speaker="candidate", text=clean, final=True)
            with self._lock:
                if not self._candidate_answers or self._candidate_answers[-1] != clean:
                    self._candidate_answers.append(clean)
                self._candidate_spoken = f"{self._candidate_spoken} {clean}".strip()
                trailing = ("然后", "因为", "但是", "比如", "首先", "其次", "还有", "主要是", "一方面")
                looks_incomplete = clean.rstrip("，, ").endswith(trailing)
                enough_content = len("".join(clean.split())) >= 12
                self._candidate_state = (
                    "answering" if looks_incomplete else "likely_complete" if enough_content else "answering"
                )
                self._save_display_state()
            if self.session:
                self.sessions.append_text(
                    self.session.id,
                    "candidate-transcript.md",
                    f"- [{entry.created_at}] {clean}",
                )
            self.bus.publish({"type": "transcript", "entry": entry.to_dict()})
            self.bus.publish(
                {
                    "type": "candidate_context_updated",
                    "answers": list(self._candidate_answers),
                }
            )
            self._publish_answer_snapshot()
            return
        entry = TranscriptEntry(speaker="interviewer", text=clean, final=True)
        with self._lock:
            self._stage_question(clean)
            self._remember_question(clean)
        if self.session:
            self.sessions.append_text(
                self.session.id,
                "interviewer-transcript.md",
                f"- [{entry.created_at}] {clean}",
            )
        self.bus.publish({"type": "transcript", "entry": entry.to_dict()})
        self.bus.publish({"type": "fast_question_transcript", "text": clean})
        self._publish_answer_snapshot()
        self.text_answerer.answer(clean, self.instructions(clean))

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
                    self._stage_question(text)
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
                self._answer_error = ""
        elif event_type == "fast_answer_delta":
            delta = str(event.get("delta", ""))
            with self._lock:
                if not self._response_id or event.get("responseId") == self._response_id:
                    if not self._answer_touched and delta.strip():
                        self._promote_pending_answer()
                        self._answer_touched = True
                    if self._answer_touched:
                        self._answer += delta
        elif event_type == "fast_answer_text_done":
            text = str(event.get("text", "")).strip()
            with self._lock:
                if text and (not self._response_id or event.get("responseId") == self._response_id):
                    if not self._answer_touched:
                        self._promote_pending_answer()
                    self._answer = text
                    self._answer_touched = True
        elif event_type == "fast_answer_completed":
            with self._lock:
                self._answer_running = False
                if not self._answer_touched:
                    self._pending_question = ""
                question = self._question
                answer = self._answer.strip()
                answer_touched = self._answer_touched
            if self.session and answer and answer_touched:
                self.sessions.append_text(
                    self.session.id,
                    "answers.md",
                    f"\n## {question or '实时回答'}\n\n{answer}\n",
                )
            with self._lock:
                self._save_display_state()
        elif event_type == "fast_answer_error":
            with self._lock:
                self._answer_running = False
                self._pending_question = ""
                self._answer_error = str(event.get("message", "生成失败，已保留上次回答"))
        self.bus.publish(event)
        self._publish_answer_snapshot()
