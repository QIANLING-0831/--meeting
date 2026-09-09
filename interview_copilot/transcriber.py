from __future__ import annotations

from http import HTTPStatus
import os
from pathlib import Path
import tempfile
from threading import Lock
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
        callback_target = on_text

        class Callback(RecognitionCallback):
            def on_event(self, result: RecognitionResult) -> None:
                sentence = result.get_sentence() or {}
                text = sentence.get("text", "").strip()
                if text:
                    callback_target(text, bool(RecognitionResult.is_sentence_end(sentence)))

            def on_error(self, result: RecognitionResult) -> None:
                message = getattr(result, "message", "未知错误")
                callback_target(f"[识别失败] {message}", True)

        options = {
            "model": model,
            "format": "pcm",
            "sample_rate": 16_000,
            "language_hints": ["zh", "en"],
            "callback": Callback(),
            "semantic_punctuation_enabled": False,
            "max_sentence_silence": max_sentence_silence_ms,
            "punctuation_prediction_enabled": True,
            "heartbeat": True,
        }
        if vocabulary_id:
            options["vocabulary_id"] = vocabulary_id
        self._recognition = Recognition(**options)
        self._started = False
        self._lock = Lock()

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
            self._recognition.send_audio_frame(pcm)

    def stop(self) -> None:
        with self._lock:
            if self._started:
                self._recognition.stop()
                self._started = False


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
