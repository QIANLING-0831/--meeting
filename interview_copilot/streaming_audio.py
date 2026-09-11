from __future__ import annotations

from dataclasses import dataclass
from threading import Event, Thread
import time
from typing import Callable

import numpy as np

from .audio import LoopbackAudioProcessor, capture_loopback, capture_microphone
from .config import AppConfig
from .models import Speaker
from .transcriber import AliyunParaformerStream


@dataclass
class _Channel:
    speaker: Speaker
    stream: AliyunParaformerStream
    thread: Thread


class StreamingAudioCoordinator:
    def __init__(
        self,
        config: AppConfig,
        on_text: Callable[[Speaker, str, bool], None],
        on_audio_level: Callable[[Speaker, float], None] | None = None,
        on_audio_status: Callable[[Speaker, str, str], None] | None = None,
    ) -> None:
        self.config = config
        self.on_text = on_text
        self.on_audio_level = on_audio_level
        self.on_audio_status = on_audio_status
        self.stop_event = Event()
        self.channels: list[_Channel] = []
        self._last_level_at: dict[Speaker, float] = {}
        self._silent_since: dict[Speaker, float] = {}
        self._silence_reported: set[Speaker] = set()

    def start(
        self,
        microphone_enabled: bool | None = None,
        microphone_device_id: str = "",
        loopback_device_name: str = "",
    ) -> None:
        if self.channels:
            return
        self.stop_event.clear()
        self._silent_since.clear()
        self._silence_reported.clear()
        microphone_enabled = self.config.microphone_enabled if microphone_enabled is None else microphone_enabled
        selected_loopback = loopback_device_name or self.config.device_name
        self._start_channel("interviewer", capture_loopback, selected_loopback)
        if microphone_enabled:
            selected_device = microphone_device_id or self.config.microphone_name
            self._start_channel("candidate", capture_microphone, selected_device)

    def stop(self) -> None:
        self.stop_event.set()
        for channel in self.channels:
            channel.thread.join(timeout=3)
            try:
                channel.stream.stop()
            except Exception:
                # One broken channel must not prevent the other channel or the
                # application lifespan from shutting down cleanly.
                pass
        self.channels.clear()
        self._silent_since.clear()
        self._silence_reported.clear()

    def _start_channel(self, speaker: Speaker, capture, device_name: str) -> None:
        stream = AliyunParaformerStream(
            lambda text, final: self.on_text(speaker, text, final),
            model=self.config.paraformer_model,
            vocabulary_id=self.config.paraformer_vocabulary_id,
            max_sentence_silence_ms=self.config.max_sentence_silence_ms,
        )
        stream.start()
        processor = LoopbackAudioProcessor() if speaker == "interviewer" else None

        def send_block(block: np.ndarray, sample_rate: int) -> None:
            now = time.monotonic()
            prepared = processor.process(block) if processor else block
            rms = float(np.sqrt(np.mean(np.square(prepared)))) if prepared.size else 0.0
            if speaker == "interviewer":
                if rms < 0.0003:
                    self._silent_since.setdefault(speaker, now)
                    if (
                        now - self._silent_since[speaker] >= 3.0
                        and speaker not in self._silence_reported
                    ):
                        self._silence_reported.add(speaker)
                        if self.on_audio_status:
                            self.on_audio_status(speaker, "silent", "连续 3 秒未采集到系统声音")
                else:
                    self._silent_since.pop(speaker, None)
                    if speaker in self._silence_reported:
                        self._silence_reported.discard(speaker)
                        if self.on_audio_status:
                            self.on_audio_status(speaker, "connected", "系统声音已恢复")
            if self.on_audio_level and now - self._last_level_at.get(speaker, 0.0) >= 0.25:
                db = 20.0 * np.log10(max(rms, 1e-6))
                level = max(0.0, min(1.0, (db + 60.0) / 60.0))
                self.on_audio_level(speaker, level)
                self._last_level_at[speaker] = now
            stream.send(prepared, sample_rate)

        def report_capture_status(status: str, message: str) -> None:
            if self.on_audio_status:
                self.on_audio_status(speaker, status, message)

        def run_capture() -> None:
            if speaker == "interviewer":
                capture(
                    self.stop_event,
                    device_name,
                    self.config.sample_rate,
                    self.config.block_seconds,
                    send_block,
                    on_status=report_capture_status,
                )
            else:
                capture(
                    self.stop_event,
                    device_name,
                    self.config.sample_rate,
                    self.config.block_seconds,
                    send_block,
                )

        thread = Thread(
            target=run_capture,
            daemon=True,
            name=f"audio-{speaker}",
        )
        thread.start()
        self.channels.append(_Channel(speaker, stream, thread))
