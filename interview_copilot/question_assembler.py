from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass


SHORT_CONTINUATION = re.compile(
    r"^(?:(?:那|那么|这个|它|这些|具体)?(?:都)?)?"
    r"(?:有哪些|哪些|为什么|为何|怎么做|如何做|是什么|还有呢|然后呢|分别是什么)"
    r"[？?呢吗啊吧呀]*$"
)
INCOMPLETE_ENDINGS = (
    "有", "包括", "分别", "分别是", "是", "比如", "例如", "以及", "还有",
    "和", "或", "的", "关于", "主要", "一下",
)
QUESTION_PREFIXES = ("请", "你说", "说一下", "讲一下", "介绍一下", "谈谈")
TERMINAL_PUNCTUATION = "，,。.!！?？；;：:"


@dataclass(frozen=True)
class _Fragment:
    text: str
    observed_at: float
    is_question: bool


class QuestionAssembler:
    """Build an answerable question from nearby final ASR sentences."""

    def __init__(self, window_seconds: float = 6.0, max_fragments: int = 3) -> None:
        self.window_seconds = window_seconds
        self.fragments: deque[_Fragment] = deque(maxlen=max_fragments)

    def reset(self) -> None:
        self.fragments.clear()

    def add_interviewer(
        self,
        text: str,
        *,
        is_question: bool,
        observed_at: float | None = None,
    ) -> str:
        now = time.monotonic() if observed_at is None else observed_at
        normalized = re.sub(r"\s+", " ", text).strip()
        self._discard_expired(now)

        if self._is_short_continuation(normalized) and self.fragments:
            combined = self._join([item.text for item in self.fragments], normalized)
            self.fragments.clear()
            self.fragments.append(_Fragment(combined, now, True))
            return combined

        if is_question:
            self.fragments.clear()
            self.fragments.append(_Fragment(normalized, now, True))
            return normalized

        if self._looks_incomplete(normalized):
            if self.fragments and self.fragments[-1].is_question:
                self.fragments.clear()
            self.fragments.append(_Fragment(normalized, now, False))
        return normalized

    def _discard_expired(self, now: float) -> None:
        while self.fragments and now - self.fragments[0].observed_at > self.window_seconds:
            self.fragments.popleft()

    @staticmethod
    def _is_short_continuation(text: str) -> bool:
        compact = re.sub(r"\s+", "", text)
        return len(compact) <= 10 and bool(SHORT_CONTINUATION.fullmatch(compact))

    @staticmethod
    def _looks_incomplete(text: str) -> bool:
        compact = text.rstrip(TERMINAL_PUNCTUATION).strip()
        return (
            len(compact) <= 60
            and (
                compact.startswith(QUESTION_PREFIXES)
                or compact.endswith(INCOMPLETE_ENDINGS)
            )
        )

    @staticmethod
    def _join(previous: list[str], continuation: str) -> str:
        parts = [item.rstrip(TERMINAL_PUNCTUATION).strip() for item in previous]
        current = continuation.strip()
        combined = "".join(part for part in parts if part)
        if combined.endswith("有") and current.startswith("有哪"):
            current = current[1:]
        return f"{combined}{current}"
