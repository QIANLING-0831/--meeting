from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass, asdict
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class ImportedSource:
    label: str
    url: str
    content: str

    def to_dict(self) -> dict:
        return asdict(self)


def import_github_source(url: str, label: str = "", opener=urlopen) -> ImportedSource:
    parsed = urlparse(url.strip())
    parts = [part for part in parsed.path.split("/") if part]
    if parsed.scheme != "https" or parsed.netloc.lower() != "github.com" or len(parts) < 2:
        raise ValueError("目前仅支持 https://github.com/所有者/仓库 格式")
    owner, repo = parts[0], parts[1].removesuffix(".git")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", owner) or not re.fullmatch(r"[A-Za-z0-9_.-]+", repo):
        raise ValueError("GitHub 仓库地址格式不正确")

    api_url = f"https://api.github.com/repos/{quote(owner)}/{quote(repo)}/readme"
    request = Request(api_url, headers={"Accept": "application/vnd.github+json", "User-Agent": "qwen-interview-copilot"})
    with opener(request, timeout=12) as response:
        data = json.loads(response.read().decode("utf-8"))
    content = base64.b64decode(data["content"]).decode("utf-8", errors="ignore")[:80_000]
    return ImportedSource(label=(label.strip() or f"{owner}/{repo}"), url=url.strip(), content=content)
