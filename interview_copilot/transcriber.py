from __future__ import annotations

from collections import deque
from http import HTTPStatus
import os
from pathlib import Path
import re
import tempfile
from threading import Event, Lock
from typing import Callable, Protocol
import wave

import numpy as np


class Transcriber(Protocol):
    def transcribe(self, audio: np.ndarray, source_rate: int) -> str: ...


class WhisperTranscriber:
    def __init__(self, model_size: str = "base", cpu_threads: int | None = None) -> None:
        from faster_whisper import WhisperModel

        threads = cpu_threads or max(1, min(4, (os.cpu_count() or 2) - 1))
        self._model = WhisperModel(
            model_size,
            device="cpu",
            compute_type="int8",
            cpu_threads=threads,
        )
        self._lock = Lock()

    def transcribe(self, audio: np.ndarray, source_rate: int) -> str:
        samples = _resample(audio, source_rate, 16_000)
        with self._lock:
            segments, _ = self._model.transcribe(
                samples,
                language="zh",
                beam_size=3,
                vad_filter=True,
                condition_on_previous_text=False,
            )
            return "".join(segment.text.strip() for segment in segments).strip()


class AliyunParaformerTranscriber:
    """Use Alibaba Cloud Bailian's Paraformer service for one audio segment."""

    def __init__(
        self,
        model: str = "paraformer-realtime-v2",
        api_key: str | None = None,
    ) -> None:
        resolved_key = api_key or os.environ.get("DASHSCOPE_API_KEY")
        if not resolved_key:
            raise RuntimeError(
                "未设置 DASHSCOPE_API_KEY，无法使用阿里云 Paraformer。"
                "请先在 PowerShell 中配置该环境变量。"
            )

        import dashscope
        from dashscope.audio.asr import Recognition

        dashscope.api_key = resolved_key
        self._recognition_class = Recognition
        self._model = model
        self._lock = Lock()

    def transcribe(self, audio: np.ndarray, source_rate: int) -> str:
        samples = _resample(audio, source_rate, 16_000)
        path = _write_pcm_wav(samples, 16_000)
        try:
            with self._lock:
                recognition = self._recognition_class(
                    model=self._model,
                    format="wav",
                    sample_rate=16_000,
                    language_hints=["zh", "en"],
                    callback=None,
                )
                result = recognition.call(str(path))
            if result.status_code != HTTPStatus.OK:
                request_id = getattr(result, "request_id", "unknown")
                message = getattr(result, "message", "未知错误")
                raise RuntimeError(
                    f"Paraformer 请求失败（request_id={request_id}）：{message}"
                )
            return "".join(
                sentence.get("text", "").strip()
                for sentence in (result.get_sentence() or [])
            ).strip()
        finally:
            path.unlink(missing_ok=True)


class AliyunParaformerStream:
    """Long-lived Paraformer stream that emits provisional and final sentences."""

    def __init__(
        self,
        on_text: Callable[[str, bool], None],
        *,
        model: str = "paraformer-realtime-v2",
        api_key: str | None = None,
        vocabulary_id: str = "",
        max_sentence_silence_ms: int = 800,
    ) -> None:
        resolved_key = api_key or os.environ.get("DASHSCOPE_API_KEY")
        if not resolved_key:
            raise RuntimeError("未设置 DASHSCOPE_API_KEY，无法使用阿里云 Paraformer。")

        import dashscope
        from dashscope.audio.asr import Recognition, RecognitionCallback, RecognitionResult

        dashscope.api_key = resolved_key
        self._on_text = on_text
        self._latest_provisional = ""
        self._last_final_text = ""
        self._text_lock = Lock()
        owner = self
        recognition_failed = Event()
        pending_frames: deque[bytes] = deque()
        pending_lock = Lock()
        pending_size = [0]

        class Callback(RecognitionCallback):
            def on_event(self, result: RecognitionResult) -> None:
                sentence = result.get_sentence() or {}
                text = sentence.get("text", "").strip()
                if text:
                    is_final = bool(RecognitionResult.is_sentence_end(sentence))
                    owner._handle_text(text, is_final)
                    if is_final:
                        owner._clear_pending_audio()

            def on_error(self, result: RecognitionResult) -> None:
                message = getattr(result, "message", "未知错误")
                recognition_failed.set()
                owner._on_text(f"[识别失败] {message}", True)

        options = {
            "model": model,
            "format": "pcm",
            "sample_rate": 16_000,
            "language_hints": ["zh", "en"],
            "callback": Callback(),
            "semantic_punctuation_enabled": False,
            "max_sentence_silence": max_sentence_silence_ms,
            "multi_threshold_mode_enabled": True,
            "punctuation_prediction_enabled": True,
            "heartbeat": True,
        }
        if vocabulary_id:
            options["vocabulary_id"] = vocabulary_id
        self._recognition_class = Recognition
        self._options = options
        self._recognition = self._recognition_class(**self._options)
        self._recognition_failed = recognition_failed
        self._pending_frames = pending_frames
        self._pending_lock = pending_lock
        self._pending_size = pending_size
        self._max_pending_bytes = 20 * 16_000 * 2
        self._started = False
        self._lock = Lock()

    @staticmethod
    def _comparable_text(text: str) -> str:
        return re.sub(r"[\s，,。.!！?？；;：:]", "", text).casefold()

    def _handle_text(self, text: str, is_final: bool) -> None:
        clean = text.strip()
        if not clean:
            return
        should_emit = True
        with self._text_lock:
            if is_final:
                if self._comparable_text(clean) == self._comparable_text(self._last_final_text):
                    should_emit = False
                else:
                    self._last_final_text = clean
                self._latest_provisional = ""
            else:
                self._latest_provisional = clean
        if should_emit:
            self._on_text(clean, is_final)

    def _clear_pending_audio(self) -> None:
        with self._pending_lock:
            self._pending_frames.clear()
            self._pending_size[0] = 0

    def flush_pending(self) -> bool:
        """Finalize provisional text when local silence outlasts cloud VAD."""
        with self._text_lock:
            text = self._latest_provisional.strip()
            if not text:
                return False
            self._latest_provisional = ""
            self._last_final_text = text
        self._clear_pending_audio()
        self._on_text(text, True)
        return True

    def start(self) -> None:
        with self._lock:
            if not self._started:
                self._recognition.start()
                self._started = True

    def send(self, audio: np.ndarray, source_rate: int) -> None:
        samples = _resample(audio, source_rate, 16_000)
        pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes()
        with self._lock:
            if not self._started:
                raise RuntimeError("Paraformer 流尚未启动")
            self._remember_pending_frame(pcm)
            if getattr(self, "_recognition_failed", None) and self._recognition_failed.is_set():
                self._restart_locked()
                return
            try:
                self._recognition.send_audio_frame(pcm)
            except Exception:
                self._restart_locked()

    def _remember_pending_frame(self, pcm: bytes) -> None:
        if not hasattr(self, "_pending_frames"):
            self._pending_frames = deque()
            self._pending_lock = Lock()
            self._pending_size = [0]
            self._max_pending_bytes = 20 * 16_000 * 2
        with self._pending_lock:
            self._pending_frames.append(pcm)
            self._pending_size[0] += len(pcm)
            while self._pending_size[0] > self._max_pending_bytes:
                self._pending_size[0] -= len(self._pending_frames.popleft())

    def _pending_snapshot(self) -> list[bytes]:
        with self._pending_lock:
            return list(self._pending_frames)

    def _restart_locked(self) -> None:
        try:
            self._recognition.stop()
        except Exception:
            pass
        self._started = False
        self._recognition = self._recognition_class(**self._options)
        self._recognition.start()
        self._started = True
        failed = getattr(self, "_recognition_failed", None)
        if failed:
            failed.clear()
        # Replay only audio that has not yet produced a final sentence. This
        # preserves a question split by a transient recognition disconnect.
        for frame in self._pending_snapshot():
            self._recognition.send_audio_frame(frame)

    def stop(self) -> None:
        with self._lock:
            if self._started:
                self._started = False
                try:
                    self._recognition.stop()
                except Exception:
                    # Cleanup is idempotent: DashScope raises when its server has
                    # already closed the recognition session.
                    pass


def create_transcriber(
    provider: str,
    whisper_model: str = "base",
    paraformer_model: str = "paraformer-realtime-v2",
) -> Transcriber:
    normalized = provider.strip().lower()
    if normalized == "paraformer":
        return AliyunParaformerTranscriber(paraformer_model)
    if normalized == "whisper":
        return WhisperTranscriber(whisper_model)
    raise ValueError(f"不支持的语音识别服务：{provider}（可选 paraformer 或 whisper）")


def _write_pcm_wav(audio: np.ndarray, sample_rate: int) -> Path:
    samples = np.asarray(audio, dtype=np.float32)
    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2")
    temporary = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    path = Path(temporary.name)
    temporary.close()
    try:
        with wave.open(str(path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(pcm.tobytes())
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path


def _resample(audio: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    if source_rate == target_rate or audio.size == 0:
        return np.asarray(audio, dtype=np.float32)
    target_length = max(1, round(audio.size * target_rate / source_rate))
    old_x = np.linspace(0.0, 1.0, num=audio.size, endpoint=False)
    new_x = np.linspace(0.0, 1.0, num=target_length, endpoint=False)
    return np.interp(new_x, old_x, audio).astype(np.float32)
