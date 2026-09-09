from __future__ import annotations

import json
import re
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from uuid import uuid4


@dataclass
class InterviewSession:
    id: str
    company: str
    position: str
    knowledge_packs: list[str]
    created_at: str
    path: Path = field(repr=False)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["path"] = str(self.path)
        return data


class SessionManager:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def create(
        self,
        *,
        company: str,
        position: str,
        jd_text: str,
        resume_path: Path | None,
        knowledge_packs: list[str],
    ) -> tuple[InterviewSession, str]:
        now = datetime.now()
        slug = re.sub(r"[^\w\-\u4e00-\u9fff]+", "-", f"{company}-{position}").strip("-")
        session_id = f"{now:%Y%m%d-%H%M%S}-{slug or uuid4().hex[:8]}"
        session_path = self.root / session_id
        session_path.mkdir(parents=True)
        (session_path / "jd.md").write_text(jd_text.strip(), encoding="utf-8")

        resume_text = ""
        if resume_path:
            suffix = resume_path.suffix.lower()
            stored_resume = session_path / f"resume{suffix}"
            shutil.copy2(resume_path, stored_resume)
            resume_text = self._extract_text(stored_resume)
        candidate_facts = self.sanitize_resume(resume_text)
        (session_path / "candidate-facts.md").write_text(candidate_facts, encoding="utf-8")

        session = InterviewSession(
            id=session_id,
            company=company.strip(),
            position=position.strip(),
            knowledge_packs=knowledge_packs,
            created_at=now.isoformat(timespec="seconds"),
            path=session_path,
        )
        (session_path / "session.json").write_text(
            json.dumps(session.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        for name in ("interviewer-transcript.md", "candidate-transcript.md", "answers.md"):
            (session_path / name).touch()
        return session, candidate_facts

    def latest(self) -> InterviewSession | None:
        candidates = sorted(
            (path for path in self.root.iterdir() if path.is_dir() and (path / "session.json").exists()),
            key=lambda path: (path / "session.json").stat().st_mtime,
            reverse=True,
        )
        for path in candidates:
            try:
                data = json.loads((path / "session.json").read_text(encoding="utf-8"))
                return InterviewSession(
                    id=str(data["id"]),
                    company=str(data.get("company", "")),
                    position=str(data.get("position", "")),
                    knowledge_packs=[str(item) for item in data.get("knowledge_packs", [])],
                    created_at=str(data.get("created_at", "")),
                    path=path,
                )
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
        return None

    def update_candidate_facts(self, session_id: str, text: str) -> None:
        path = self._safe_session_path(session_id) / "candidate-facts.md"
        path.write_text(text.strip(), encoding="utf-8")

    def append_text(self, session_id: str, filename: str, text: str) -> None:
        allowed = {
            "interviewer-transcript.md",
            "candidate-transcript.md",
            "answers.md",
            "runtime-errors.md",
        }
        if filename not in allowed:
            raise ValueError("不允许写入该会话文件")
        path = self._safe_session_path(session_id) / filename
        with path.open("a", encoding="utf-8") as handle:
            handle.write(text.rstrip() + "\n")

    def _safe_session_path(self, session_id: str) -> Path:
        candidate = (self.root / session_id).resolve()
        if candidate.parent != self.root.resolve() or not candidate.is_dir():
            raise ValueError("会话不存在")
        return candidate

    @staticmethod
    def sanitize_resume(text: str) -> str:
        if not text.strip():
            return "# 候选人事实\n\n请在面试开始前补充真实经历、项目职责和量化结果。\n"
        cleaned = re.sub(r"\b1[3-9]\d{9}\b", "[手机号已移除]", text)
        cleaned = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[邮箱已移除]", cleaned)
        cleaned = re.sub(r"(?i)(身份证|证件号)\s*[:：]?\s*[0-9Xx-]{8,}", r"\1：[已移除]", cleaned)
        return "# 候选人事实（请核对）\n\n" + cleaned.strip() + "\n"

    @staticmethod
    def _extract_text(path: Path) -> str:
        if path.suffix.lower() == ".pdf":
            from pypdf import PdfReader

            return "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
        return path.read_text(encoding="utf-8", errors="ignore")
