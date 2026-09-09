from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Literal


Speaker = Literal["interviewer", "candidate"]


@dataclass(frozen=True)
class TranscriptEntry:
    speaker: Speaker
    text: str
    final: bool = True
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class QuestionCandidate:
    text: str
    confidence: float
    question_type: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class KnowledgeHit:
    source: str
    pack: str
    title: str
    content: str
    score: float
    answer_preview: str = ""

    def to_dict(self) -> dict:
        return asdict(self)
