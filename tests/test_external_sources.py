import base64
import json

import pytest

from interview_copilot.external_sources import import_github_source


class FakeResponse:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self):
        return json.dumps(self.value).encode()


def test_imports_public_github_readme_as_external_knowledge():
    content = "# Agent 题库\n\n什么是 Agent Loop？"

    source = import_github_source(
        "https://github.com/example/agent-bank",
        "Agent 题库",
        opener=lambda _request, timeout: FakeResponse(
            {"content": base64.b64encode(content.encode()).decode()}
        ),
    )

    assert source.label == "Agent 题库"
    assert "Agent Loop" in source.content


def test_rejects_non_github_external_url():
    with pytest.raises(ValueError, match="仅支持"):
        import_github_source("http://127.0.0.1/private")
