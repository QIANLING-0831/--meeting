from __future__ import annotations

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
        self._answer = ""
        self._response_id = ""
        self._answer_running = False
        self._answer_touched = False
        self._last_transcript_item = ""
        # Keep the active follow-up chain instead of mechanically retaining two
        # questions.  A clear topic-switch phrase starts a new chain; the cap is
        # only a safety bound for prompt size during a long interview.
        self._topic_questions: deque[str] = deque(maxlen=10)
        self._candidate_answers: deque[str] = deque(maxlen=8)
        self.context_provider = AnswerContextProvider(root / "workspace", self.sessions)
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
                "recentQuestions": list(self._topic_questions),
                "candidateAnswers": list(self._candidate_answers),
            }

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
3. 只能把“候选人事实”中的内容说成亲身经历；资料没有写明的经历、数字和成果不得编造。
4. 技术题可以结合通用知识回答，但要清楚区分通用方案与候选人的真实经历。
5. 听到停顿、语气词或不完整句时继续等待；不要抢答。
6. 当前话轮若只是“嗯、啊、好的”等语气词、残句或无关对话，输出空字符串，不覆盖上一道问题。
7. 当前话轮若是追问、指代或被打断后的补充，结合“当前追问链”和候选人刚才的回答还原真实意图；只有面试官明确切换话题时才切换主题。
8. 候选人回答仅用于判断面试官后续追问所指内容、避免前后矛盾，不得把候选人的话当成面试官的新问题，也不要擅自虚构补充。

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
            self.bus.publish({"type": "qwen_transcript_delta", "speaker": speaker, "text": clean})
            return
        if speaker == "candidate":
            # Laptop speakers can leak the interviewer's voice back into the
            # microphone. Do not mistake a near-duplicate question for the
            # candidate's answer context.
            with self._lock:
                current_question = self._question
            comparable = lambda value: "".join(value.lower().split()).strip("，。！？?")
            if current_question and SequenceMatcher(
                None, comparable(clean), comparable(current_question)
            ).ratio() >= 0.82:
                self.bus.publish({"type": "candidate_echo_ignored", "text": clean})
                return
            entry = TranscriptEntry(speaker="candidate", text=clean, final=True)
            with self._lock:
                if not self._candidate_answers or self._candidate_answers[-1] != clean:
                    self._candidate_answers.append(clean)
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
