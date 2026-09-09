from __future__ import annotations

import queue
import threading
import time
from pathlib import Path

import numpy as np

from .audio import capture_loopback, list_loopback_devices
from .config import AppConfig
from .transcriber import Transcriber, create_transcriber


class InterviewCopilotApp:
    """第一阶段控制台：捕获系统声音并输出中文转写。"""

    def __init__(self) -> None:
        self.root_dir = Path(__file__).resolve().parents[1]
        self.config = AppConfig.load(self.root_dir)
        self.stop_event = threading.Event()
        self.audio_parts: list[np.ndarray] = []
        self.speech_seconds = 0.0
        self.silence_started: float | None = None
        self.jobs: queue.Queue[tuple[np.ndarray, int] | None] = queue.Queue()
        self.transcriber: Transcriber | None = None

    def run(self) -> None:
        devices = list_loopback_devices()
        if not devices:
            raise SystemExit("没有找到 Windows 系统声音回环设备。")
        print("\n可用的系统声音设备：")
        for index, device in enumerate(devices, start=1):
            marker = "（上次使用）" if device.name == self.config.device_name else ""
            print(f"  {index}. {device.name}{marker}")
        selected = self._choose_device(devices)
        self.config.device_name = selected.name
        self.config.save(self.root_dir)

        if self.config.stt_provider.lower() == "paraformer":
            print(f"\n正在连接阿里云 {self.config.paraformer_model}…")
        else:
            print(f"\n正在加载 faster-whisper {self.config.model_size} 模型…")
            print("首次运行会下载模型，请稍候。")
        self.transcriber = create_transcriber(
            self.config.stt_provider,
            whisper_model=self.config.model_size,
            paraformer_model=self.config.paraformer_model,
        )
        worker = threading.Thread(target=self._transcription_worker, daemon=True)
        worker.start()

        print(f"\n正在监听：{selected.name}")
        print("播放中文语音，停顿约 1 秒后显示文字。按 Ctrl+C 停止。\n")
        try:
            capture_loopback(
                self.stop_event,
                selected.name,
                self.config.sample_rate,
                self.config.block_seconds,
                self._on_audio_block,
            )
        except KeyboardInterrupt:
            print("\n正在停止…")
        finally:
            self.stop_event.set()
            self._flush_audio(self.config.sample_rate)
            self.jobs.put(None)
            worker.join(timeout=30)
            print("已停止。")

    def _choose_device(self, devices):
        default_index = next(
            (i for i, device in enumerate(devices) if device.name == self.config.device_name), 0
        )
        raw = input(f"请选择设备 [默认 {default_index + 1}]：").strip()
        if not raw:
            return devices[default_index]
        try:
            return devices[int(raw) - 1]
        except (ValueError, IndexError):
            raise SystemExit("设备编号无效。") from None

    def _on_audio_block(self, block: np.ndarray, sample_rate: int) -> None:
        rms = float(np.sqrt(np.mean(np.square(block)))) if block.size else 0.0
        if rms >= self.config.silence_rms:
            self.audio_parts.append(block)
            self.speech_seconds += block.size / sample_rate
            self.silence_started = None
            return
        if not self.audio_parts:
            return
        self.audio_parts.append(block)
        self.silence_started = self.silence_started or time.monotonic()
        if time.monotonic() - self.silence_started >= self.config.silence_seconds:
            self._flush_audio(sample_rate)

    def _flush_audio(self, sample_rate: int) -> None:
        if not self.audio_parts:
            return
        audio = np.concatenate(self.audio_parts)
        duration = self.speech_seconds
        self.audio_parts, self.speech_seconds, self.silence_started = [], 0.0, None
        if duration >= self.config.min_speech_seconds:
            self.jobs.put((audio, sample_rate))

    def _transcription_worker(self) -> None:
        assert self.transcriber is not None
        while True:
            job = self.jobs.get()
            if job is None:
                return
            audio, sample_rate = job
            try:
                text = self.transcriber.transcribe(audio, sample_rate)
                if text:
                    timestamp = time.strftime("%H:%M:%S")
                    print(f"[{timestamp}] {text}", flush=True)
            except Exception as exc:
                print(f"[识别失败] {exc}", flush=True)
