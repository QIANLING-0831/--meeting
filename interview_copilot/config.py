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
    aliyun_api_key: str = field(default="", repr=False)
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
    qwen_realtime_enabled: bool = True
    qwen_realtime_model: str = "qwen-audio-3.0-realtime-plus"
    qwen_realtime_workspace_id: str = ""
    qwen_realtime_turn_detection: str = "server_vad"
    qwen_pipeline_mode: str = "realtime"
    qwen_asr_model: str = "qwen-audio-3.0-asr-flash-streaming"
    qwen_answer_model: str = "qwen-plus"
    browser_disconnect_grace_seconds: float = 2.0
    overlay_enabled: bool = False
    overlay_opacity: float = 0.88
    overlay_background_opacity: float = 0.78
    overlay_text_opacity: float = 1.0
    overlay_font_size: int = 24
    overlay_history_font_size: int = 14
    overlay_text_color: str = "#E8F5EE"
    overlay_history_color: str = "#A9BBB2"
    overlay_show_question: bool = True
    overlay_show_history: bool = True
    overlay_history_count: int = 2
    overlay_highlight_progress: bool = True
    overlay_auto_microphone: bool = True
    overlay_size_preset: str = "standard"
    overlay_width: int = 0
    overlay_height: int = 0
    overlay_x: int | None = None
    overlay_y: int | None = None

    @classmethod
    def load(cls, root: Path) -> "AppConfig":
        path = root / "config.json"
        if not path.exists():
            return cls()
        values = json.loads(path.read_text(encoding="utf-8"))
        if "aliyun_api_key" not in values and values.get("paraformer_api_key"):
            values["aliyun_api_key"] = values["paraformer_api_key"]
        allowed = cls.__dataclass_fields__.keys()
        return cls(**{key: value for key, value in values.items() if key in allowed})

    def save(self, root: Path) -> None:
        (root / "config.json").write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8"
        )
