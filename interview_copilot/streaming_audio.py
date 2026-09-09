from __future__ import annotations

from dataclasses import dataclass
from threading import Event, Thread
from typing import Callable

from .audio import capture_loopback, capture_microphone
from .config import AppConfig
from .models import Speaker
from .transcriber import AliyunParaformerStream


@dataclass
class _Channel:
    speaker: Speaker
    stream: AliyunParaformerStream
    thread: Thread


class StreamingAudioCoordinator:
    def __init__(self, config: AppConfig, on_text: Callable[[Speaker, str, bool], None]) -> None:
        self.config = config
        self.on_text = on_text
        self.stop_event = Event()
        self.channels: list[_Channel] = []

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
            channel.stream.stop()
        self.channels.clear()

    def _start_channel(self, speaker: Speaker, capture, device_name: str) -> None:
        stream = AliyunParaformerStream(
            lambda text, final: self.on_text(speaker, text, final),
            model=self.config.paraformer_model,
            vocabulary_id=self.config.paraformer_vocabulary_id,
            max_sentence_silence_ms=self.config.max_sentence_silence_ms,
        )
        stream.start()
        thread = Thread(
            target=capture,
            args=(
                self.stop_event,
                device_name,
                self.config.sample_rate,
                self.config.block_seconds,
                stream.send,
            ),
            daemon=True,
            name=f"audio-{speaker}",
        )
        thread.start()
        self.channels.append(_Channel(speaker, stream, thread))
