from __future__ import annotations

from dataclasses import dataclass
from threading import Event, Thread
import time
from typing import Callable

import numpy as np

from .audio import capture_loopback, capture_microphone, prepare_loopback_audio
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
    ) -> None:
        self.config = config
        self.on_text = on_text
        self.on_audio_level = on_audio_level
        self.stop_event = Event()
        self.channels: list[_Channel] = []
        self._last_level_at: dict[Speaker, float] = {}

    def start(self, microphone_enabled: bool | None = None, microphone_device_id: str = "") -> None:
        if self.channels:
            return
        self.stop_event.clear()
        microphone_enabled = self.config.microphone_enabled if microphone_enabled is None else microphone_enabled
        self._start_channel("interviewer", capture_loopback, self.config.device_name)
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

    def _start_channel(self, speaker: Speaker, capture, device_name: str) -> None:
        stream = AliyunParaformerStream(
            lambda text, final: self.on_text(speaker, text, final),
            model=self.config.paraformer_model,
            vocabulary_id=self.config.paraformer_vocabulary_id,
            max_sentence_silence_ms=self.config.max_sentence_silence_ms,
        )
        stream.start()

        def send_block(block: np.ndarray, sample_rate: int) -> None:
            now = time.monotonic()
            if self.on_audio_level and now - self._last_level_at.get(speaker, 0.0) >= 0.25:
                rms = float(np.sqrt(np.mean(np.square(block)))) if block.size else 0.0
                db = 20.0 * np.log10(max(rms, 1e-6))
                level = max(0.0, min(1.0, (db + 60.0) / 60.0))
                self.on_audio_level(speaker, level)
                self._last_level_at[speaker] = now
            prepared = prepare_loopback_audio(block) if speaker == "interviewer" else block
            stream.send(prepared, sample_rate)

        thread = Thread(
            target=capture,
            args=(
                self.stop_event,
                device_name,
                self.config.sample_rate,
                self.config.block_seconds,
                send_block,
            ),
            daemon=True,
            name=f"audio-{speaker}",
        )
        thread.start()
        self.channels.append(_Channel(speaker, stream, thread))
