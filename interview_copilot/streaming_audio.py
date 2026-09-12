from __future__ import annotations

from dataclasses import dataclass
from threading import Event, Thread
import time
from typing import Callable

import numpy as np

from .audio import LoopbackAudioProcessor, capture_loopback
from .config import AppConfig
from .models import Speaker
from .qwen_realtime import AliyunRealtimeAnswerStream


@dataclass
class _Channel:
    speaker: Speaker
    thread: Thread


class StreamingAudioCoordinator:
    def __init__(
        self,
        config: AppConfig,
        on_text: Callable[[Speaker, str, bool], None] | None = None,
        on_audio_level: Callable[[Speaker, float], None] | None = None,
        on_audio_status: Callable[[Speaker, str, str], None] | None = None,
        on_fast_event: Callable[[dict], None] | None = None,
    ) -> None:
        self.config = config
        self.on_text = on_text
        self.on_audio_level = on_audio_level
        self.on_audio_status = on_audio_status
        self.on_fast_event = on_fast_event
        self.stop_event = Event()
        self.channels: list[_Channel] = []
        self.fast_stream: AliyunRealtimeAnswerStream | None = None
        self._last_level_at: dict[Speaker, float] = {}
        self._silent_since: dict[Speaker, float] = {}
        self._silence_reported: set[Speaker] = set()

    def start(
        self,
        microphone_enabled: bool | None = None,
        microphone_device_id: str = "",
        loopback_device_name: str = "",
        fast_instructions: str = "",
    ) -> None:
        if self.channels:
            return
        self.stop_event.clear()
        self._silent_since.clear()
        self._silence_reported.clear()
        selected_loopback = loopback_device_name or self.config.device_name
        if not self.on_fast_event:
            raise RuntimeError("Qwen 事件处理器未配置")
        self.fast_stream = AliyunRealtimeAnswerStream(
            self.on_fast_event,
            model=self.config.qwen_realtime_model,
            workspace_id=self.config.qwen_realtime_workspace_id,
            turn_detection=self.config.qwen_realtime_turn_detection,
            instructions=fast_instructions,
        )
        self.fast_stream.start()
        self._start_channel("interviewer", capture_loopback, selected_loopback)

    def stop(self) -> None:
        self.stop_event.set()
        if self.fast_stream:
            self.fast_stream.stop()
            self.fast_stream = None
        for channel in self.channels:
            channel.thread.join(timeout=3)
        self.channels.clear()
        self._silent_since.clear()
        self._silence_reported.clear()

    def _start_channel(self, speaker: Speaker, capture, device_name: str) -> None:
        processor = LoopbackAudioProcessor() if speaker == "interviewer" else None

        def send_block(block: np.ndarray, sample_rate: int) -> None:
            now = time.monotonic()
            prepared = processor.process(block) if processor else block
            rms = float(np.sqrt(np.mean(np.square(prepared)))) if prepared.size else 0.0
            silence_floor = processor.silence_rms if processor else 0.0008
            if rms < silence_floor:
                self._silent_since.setdefault(speaker, now)
            else:
                self._silent_since.pop(speaker, None)
            if speaker == "interviewer":
                if rms < silence_floor:
                    if (
                        now - self._silent_since[speaker] >= 3.0
                        and speaker not in self._silence_reported
                    ):
                        self._silence_reported.add(speaker)
                        if self.on_audio_status:
                            self.on_audio_status(speaker, "silent", "连续 3 秒未采集到系统声音")
                else:
                    if speaker in self._silence_reported:
                        self._silence_reported.discard(speaker)
                        if self.on_audio_status:
                            self.on_audio_status(speaker, "connected", "系统声音已恢复")
            if self.on_audio_level and now - self._last_level_at.get(speaker, 0.0) >= 0.25:
                db = 20.0 * np.log10(max(rms, 1e-6))
                level = max(0.0, min(1.0, (db + 80.0) / 80.0))
                self.on_audio_level(speaker, level)
                self._last_level_at[speaker] = now
            if speaker == "interviewer" and self.fast_stream:
                self.fast_stream.send(prepared, sample_rate)

        def report_capture_status(status: str, message: str) -> None:
            if self.on_audio_status:
                self.on_audio_status(speaker, status, message)

        def run_capture() -> None:
            if speaker == "interviewer":
                capture(
                    self.stop_event,
                    device_name,
                    self.config.sample_rate,
                    min(self.config.block_seconds, 0.04),
                    send_block,
                    on_status=report_capture_status,
                )

        thread = Thread(
            target=run_capture,
            daemon=True,
            name=f"audio-{speaker}",
        )
        thread.start()
        self.channels.append(_Channel(speaker, thread))
