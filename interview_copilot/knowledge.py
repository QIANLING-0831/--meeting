from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from .models import KnowledgeHit


SUPPORTED_SUFFIXES = {".md", ".txt", ".pdf"}
GENERIC_QUERY_TERMS = {
    "什么", "怎么", "如何", "为何", "哪些", "多少", "是否", "可以", "能否",
    "不能", "介绍", "一下", "说说", "谈谈", "问题", "产生", "遇到", "出现",
    "解决", "处理", "回答",
}
QUESTION_PHRASES = (
    "怎么处理", "如何处理", "怎么解决", "如何解决", "怎么办", "为什么",
    "是什么", "有哪几种", "有哪些", "能不能", "是否可以", "请介绍",
    "介绍一下", "请问", "说说", "谈谈",
)
GENERIC_DOMAIN_TERMS = {"ai", "agent", "llm", "gpt", "模型", "系统", "项目", "大模型"}


class KnowledgeIndex:
    def __init__(self, workspace_root: Path) -> None:
        self.knowledge_root = workspace_root / "knowledge"
        self.packs_root = self.knowledge_root / "packs"
        self.sources_root = self.knowledge_root / "sources"
        self.db_path = self.knowledge_root / "indexes" / "knowledge.db"
        self.packs_root.mkdir(parents=True, exist_ok=True)
        self.sources_root.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def list_packs(self) -> list[str]:
        packs = {path.name for path in self.packs_root.iterdir() if path.is_dir()}
        if (self.sources_root / "zero2Agent" / "learn-agent-interview").exists():
            packs.add("agent")
        return sorted(packs)

    def rebuild(self) -> dict[str, int]:
        documents: list[tuple[str, Path]] = []
        for pack_dir in self.packs_root.iterdir():
            if pack_dir.is_dir():
                documents.extend((pack_dir.name, path) for path in self._files(pack_dir))
        agent_root = self.sources_root / "zero2Agent" / "learn-agent-interview"
        if agent_root.exists():
            documents.extend(("agent", path) for path in self._files(agent_root))

        indexed_files = indexed_chunks = 0
        with sqlite3.connect(self.db_path) as connection:
            connection.execute("DELETE FROM knowledge")
            for pack, path in documents:
                text = self._read(path)
                if not text.strip():
                    continue
                indexed_files += 1
                relative = str(path.relative_to(self.knowledge_root)).replace("\\", "/")
                for title, content in self._chunks(text, path.stem):
                    connection.execute(
                        "INSERT INTO knowledge(pack, source, title, content) VALUES (?, ?, ?, ?)",
                        (pack, relative, title, content),
                    )
                    indexed_chunks += 1
            connection.commit()
        return {"files": indexed_files, "chunks": indexed_chunks}

    def search(self, query: str, packs: list[str], limit: int = 6) -> list[KnowledgeHit]:
        terms = self._query_terms(query)
        if not terms or not packs:
            return []
        placeholders = ",".join("?" for _ in packs)
        match_query = " OR ".join(f'"{term}"' for term in terms[:12])
        fetch_limit = max(limit * 4, limit)
        sql = (
            "SELECT source, pack, title, content, bm25(knowledge) AS rank "
            f"FROM knowledge WHERE knowledge MATCH ? AND pack IN ({placeholders}) "
            "ORDER BY rank LIMIT ?"
        )
        try:
            with sqlite3.connect(self.db_path) as connection:
                rows = connection.execute(sql, (match_query, *packs, fetch_limit)).fetchall()
        except sqlite3.OperationalError:
            rows = []
        hits = [
            KnowledgeHit(row[0], row[1], row[2], row[3], -float(row[4]), self.answer_preview(row[3]))
            for row in rows
        ]
        hits.extend(self._short_topic_search(query, packs, fetch_limit))
        unique_hits = {
            (hit.source, hit.title, hit.content): hit
            for hit in hits
        }
        lexical_terms = self._lexical_terms(query)
        hits = list(unique_hits.values())
        hits.sort(
            key=lambda hit: (
                self._lexical_score(hit, lexical_terms),
                self._answer_priority(hit.content),
                len(hit.answer_preview) >= 80,
                hit.score,
            ),
            reverse=True,
        )
        return hits[:limit]

    def _short_topic_search(self, query: str, packs: list[str], limit: int) -> list[KnowledgeHit]:
        """Recall short Chinese topics that FTS trigram cannot index, such as “幻觉”."""
        terms = self._lexical_terms(query)
        if not terms:
            return []
        topic_terms = [term for term in terms if term.casefold() not in GENERIC_DOMAIN_TERMS]
        search_terms = topic_terms or terms
        placeholders = ",".join("?" for _ in packs)
        clauses = " OR ".join("(title LIKE ? OR content LIKE ?)" for _ in search_terms)
        patterns = [value for term in search_terms for value in (f"%{term}%", f"%{term}%")]
        with sqlite3.connect(self.db_path) as connection:
            rows = connection.execute(
                f"SELECT source, pack, title, content FROM knowledge "
                f"WHERE pack IN ({placeholders}) AND ({clauses}) LIMIT ?",
                (*packs, *patterns, max(limit * 8, 80)),
            ).fetchall()
        return [
            KnowledgeHit(
                row[0], row[1], row[2], row[3],
                float(self._lexical_score_text(row[2], row[3], terms)),
                self.answer_preview(row[3]),
            )
            for row in rows
        ]

    @staticmethod
    def _lexical_score(hit: KnowledgeHit, terms: list[str]) -> int:
        return KnowledgeIndex._lexical_score_text(hit.title, hit.content, terms)

    @staticmethod
    def _lexical_score_text(title: str, content: str, terms: list[str]) -> int:
        title_folded = title.casefold()
        combined = f"{title}\n{content}".casefold()
        score = 0
        for term in terms:
            folded = term.casefold()
            if folded not in combined:
                continue
            if folded in GENERIC_DOMAIN_TERMS:
                score += 1
            else:
                score += len(term) * (4 if folded in title_folded else 1)
        return score

    def _fallback_search(self, query: str, packs: list[str], limit: int) -> list[KnowledgeHit]:
        needle = query[:40]
        placeholders = ",".join("?" for _ in packs)
        with sqlite3.connect(self.db_path) as connection:
            rows = connection.execute(
                f"SELECT source, pack, title, content FROM knowledge WHERE pack IN ({placeholders}) "
                "AND content LIKE ? LIMIT ?",
                (*packs, f"%{needle}%", limit),
            ).fetchall()
        return [
            KnowledgeHit(row[0], row[1], row[2], row[3], 0.0, self.answer_preview(row[3]))
            for row in rows
        ]

    @staticmethod
    def answer_preview(content: str, limit: int = 700) -> str:
        """Extract the useful answer body from an interview-question Markdown chunk."""
        lines = content.splitlines()
        expert_marker = next(
            (index for index, line in enumerate(lines) if "高手答" in line),
            None,
        )
        if expert_marker is not None:
            lines = lines[expert_marker + 1 :]

        useful: list[str] = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                if useful and useful[-1] != "":
                    useful.append("")
                continue
            if stripped.startswith(("#", ">")):
                continue
            if "新手答" in stripped or "高手答" in stripped:
                continue
            stripped = re.sub(r"!\[[^]]*]\([^)]*\)", "", stripped)
            stripped = re.sub(r"\[([^]]+)]\([^)]*\)", r"\1", stripped)
            stripped = stripped.replace("**", "").replace("`", "")
            useful.append(stripped)
            if len("\n".join(useful)) >= limit:
                break
        preview = "\n".join(useful).strip()
        return preview[:limit].rstrip()

    @staticmethod
    def _answer_priority(content: str) -> int:
        if "高手答" in content:
            return 2
        if re.search(r"^#{2,4}\s*Q[：:]", content, re.MULTILINE):
            return 1
        return 0

    def _ensure_schema(self) -> None:
        with sqlite3.connect(self.db_path) as connection:
            try:
                connection.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS knowledge USING fts5("
                    "pack UNINDEXED, source UNINDEXED, title, content, tokenize='trigram')"
                )
            except sqlite3.OperationalError:
                connection.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS knowledge USING fts5("
                    "pack UNINDEXED, source UNINDEXED, title, content, tokenize='unicode61')"
                )

    @staticmethod
    def _files(root: Path):
        return (path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES)

    @staticmethod
    def _read(path: Path) -> str:
        if path.suffix.lower() == ".pdf":
            from pypdf import PdfReader

            return "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
        return path.read_text(encoding="utf-8", errors="ignore")

    @staticmethod
    def _chunks(text: str, fallback_title: str):
        title = fallback_title
        buffer: list[str] = []
        size = 0
        for line in text.splitlines():
            if line.startswith("#") and buffer:
                yield title, "\n".join(buffer).strip()
                buffer, size = [], 0
            if line.startswith("#"):
                title = line.lstrip("# ").strip() or fallback_title
            buffer.append(line)
            size += len(line)
            if size >= 1400:
                yield title, "\n".join(buffer).strip()
                buffer, size = [], 0
        if buffer:
            yield title, "\n".join(buffer).strip()

    @staticmethod
    def _query_terms(query: str) -> list[str]:
        latin = re.findall(r"[A-Za-z][A-Za-z0-9_.+-]{1,}", query)
        chinese = [part for part in re.split(r"[，。？！、；：\s]+", query) if len(part) >= 2]
        return list(dict.fromkeys(latin + chinese))

    @staticmethod
    def _lexical_terms(query: str) -> list[str]:
        terms = re.findall(r"[A-Za-z][A-Za-z0-9_.+-]{1,}", query)
        for run in re.findall(r"[\u4e00-\u9fff]+", query):
            cleaned = run
            for phrase in QUESTION_PHRASES:
                cleaned = cleaned.replace(phrase, "")
            cleaned = cleaned.strip("的了呢吗啊吧呀嘛")
            if len(cleaned) >= 2 and cleaned not in GENERIC_QUERY_TERMS:
                terms.append(cleaned)
            terms.extend(
                cleaned[index : index + 2]
                for index in range(max(0, len(cleaned) - 1))
                if cleaned[index : index + 2] not in GENERIC_QUERY_TERMS
            )
        return list(dict.fromkeys(terms))[:12]
