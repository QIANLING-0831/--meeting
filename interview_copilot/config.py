from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class AppConfig:
    stt_provider: str = "paraformer"
    model_size: str = "base"
    paraformer_model: str = "paraformer-realtime-v2"
    paraformer_api_key: str = field(default="", repr=False)
    device_name: str = ""
    sample_rate: int = 48_000
    block_seconds: float = 0.1
    silence_seconds: float = 1.0
    min_speech_seconds: float = 0.8
    silence_rms: float = 0.006
    web_host: str = "127.0.0.1"
    web_port: int = 8765
    paraformer_vocabulary_id: str = ""
    max_sentence_silence_ms: int = 1_200
    microphone_name: str = ""
    microphone_enabled: bool = False
    codex_model: str = "gpt-5.6-luna"
    codex_reasoning_effort: str = "low"
    include_core_points: bool = False
    answer_timeout_seconds: float = 10.0

    @classmethod
    def load(cls, root: Path) -> "AppConfig":
        path = root / "config.json"
        if not path.exists():
            return cls()
        values = json.loads(path.read_text(encoding="utf-8"))
        allowed = cls.__dataclass_fields__.keys()
        return cls(**{key: value for key, value in values.items() if key in allowed})

    def save(self, root: Path) -> None:
        (root / "config.json").write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8"
        )
