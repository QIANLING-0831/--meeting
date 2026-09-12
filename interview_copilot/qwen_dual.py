from __future__ import annotations

import os
import re
import threading
from collections.abc import Callable, Iterable
from http import HTTPStatus

import numpy as np

from .qwen_realtime import _resample


EventHandler = Callable[[dict], None]
TextHandler = Callable[[str, bool], None]


def extract_hotwords(text: str, limit: int = 80) -> dict[str, int]:
    """Extract stable technical names for Qwen ASR's free instant vocabulary."""
    preferred = [
        "AI Agent", "Agent Loop", "Plan-Execute", "Function Calling", "Tool Registry",
        "Context Window", "Structured Output", "Human-in-the-Loop", "OpenMontage",
        "DeepSeek Harness", "RAG", "FTS5", "RRF", "sqlite-vec", "Pytest", "Allure",
        "GitHub Actions", "FastAPI", "Docker", "TypeScript", "JavaScript", "C++",
        "大语言模型", "检索增强生成", "向量数据库", "智能体", "工作流", "多模态",
        "语音识别", "实时转写", "提示词工程", "知识库", "微服务", "分布式系统",
    ]
    candidates = preferred + re.findall(
        r"(?<![A-Za-z0-9])(?:[A-Z][A-Za-z0-9.+#-]{1,}(?:\s+[A-Z][A-Za-z0-9.+#-]{1,}){0,2})(?![A-Za-z0-9])",
        text,
    )
    candidates += re.findall(r"[《“\"]([^》”\"\n]{2,24})[》”\"]", text)
    result: dict[str, int] = {}
    lowered = text.casefold()
    for word in candidates:
        clean = " ".join(word.split()).strip(".,，。；;：:")
        if len(clean) < 2 or clean.casefold() not in lowered or clean.casefold() in {key.casefold() for key in result}:
            continue
        result[clean] = 5
        if len(result) >= limit:
            break
    return result


class QwenAsrStream:
    """Dedicated Qwen Audio ASR stream with context and instant hotwords."""

    def __init__(
        self,
        on_text: TextHandler,
        on_event: EventHandler,
        *,
        api_key: str | None = None,
        workspace_id: str = "",
        context: str = "",
        vocabulary: dict[str, int] | None = None,
        model: str = "qwen-audio-3.0-asr-flash-streaming",
        recognition_class=None,
        callback_base=None,
        result_class=None,
    ) -> None:
        resolved_key = api_key or os.environ.get("DASHSCOPE_API_KEY", "")
        if not resolved_key:
            raise RuntimeError("未设置 DASHSCOPE_API_KEY，无法启动 Qwen 专用 ASR")
        if recognition_class is None:
            import dashscope
            from dashscope.audio.asr import Recognition, RecognitionCallback, RecognitionResult

            dashscope.api_key = resolved_key
            recognition_class, callback_base, result_class = Recognition, RecognitionCallback, RecognitionResult

        self._on_text = on_text
        self._on_event = on_event
        self._context = context[:400]
        self._lock = threading.Lock()
        self._started = False
        self._audio_buffer = np.empty(0, dtype=np.float32)
        owner = self

        class Callback(callback_base):
            def on_open(self) -> None:
                owner._on_event({"type": "asr_channel_ready", "model": model})

            def on_event(self, result) -> None:
                sentence = result.get_sentence() or {}
                text = sentence.get("text", "").strip()
                if text:
                    owner._on_text(text, bool(result_class.is_sentence_end(sentence)))

            def on_error(self, result) -> None:
                owner._on_event(
                    {"type": "asr_error", "message": getattr(result, "message", "Qwen ASR 识别失败")}
                )

            def on_close(self) -> None:
                owner._on_event({"type": "asr_closed"})

        options = {
            "model": model,
            "format": "pcm",
            "sample_rate": 16_000,
            "language_hints": ["zh", "en"],
            "callback": Callback(),
            "semantic_punctuation_enabled": True,
            "max_sentence_silence": 1_200,
            "punctuation_prediction_enabled": True,
            "heartbeat": True,
        }
        if vocabulary:
            options["vocabulary"] = vocabulary
        if workspace_id.strip():
            options["workspace"] = workspace_id.strip()
        self._recognition = recognition_class(**options)

    def start(self) -> None:
        raw_input = None
        if self._context:
            raw_input = {
                "context": [
                    {"role": "user", "content": [{"type": "input_text", "text": self._context}]}
                ]
            }
        with self._lock:
            if not self._started:
                if raw_input:
                    self._recognition.start(raw_input=raw_input)
                else:
                    self._recognition.start()
                self._started = True

    def send(self, audio: np.ndarray, source_rate: int) -> None:
        samples = _resample(audio, source_rate, 16_000)
        with self._lock:
            if not self._started:
                return
            self._audio_buffer = np.concatenate((self._audio_buffer, samples))
            # DashScope recommends sending about 100 ms per packet. Capturing in
            # shorter blocks keeps the UI meter responsive, so aggregate here.
            while self._audio_buffer.size >= 1_600:
                packet = self._audio_buffer[:1_600]
                self._audio_buffer = self._audio_buffer[1_600:]
                self._send_packet(packet)

    def _send_packet(self, samples: np.ndarray) -> None:
        pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes()
        self._recognition.send_audio_frame(pcm)

    def stop(self) -> None:
        with self._lock:
            if self._started:
                self._started = False
                try:
                    if self._audio_buffer.size:
                        self._send_packet(self._audio_buffer)
                        self._audio_buffer = np.empty(0, dtype=np.float32)
                    self._recognition.stop()
                except Exception:
                    pass


class QwenTextAnswerer:
    """Stream a Qwen text answer without blocking audio capture or ASR."""

    def __init__(
        self,
        on_event: EventHandler,
        model: str = "qwen-plus",
        workspace_id: str = "",
        generation_call=None,
    ) -> None:
        self.on_event = on_event
        self.model = model
        self.workspace_id = workspace_id.strip()
        self._generation_call = generation_call
        self._generation = 0
        self._lock = threading.Lock()

    def answer(self, question: str, instructions: str) -> None:
        with self._lock:
            self._generation += 1
            generation = self._generation
        threading.Thread(
            target=self._run,
            args=(generation, question, instructions),
            daemon=True,
            name="qwen-text-answer",
        ).start()

    def _run(self, generation: int, question: str, instructions: str) -> None:
        response_id = f"qwen-text-{generation}"
        self.on_event({"type": "fast_answer_started", "responseId": response_id})
        try:
            call = self._generation_call
            if call is None:
                import dashscope

                call = dashscope.Generation.call
            options = dict(
                api_key=os.environ.get("DASHSCOPE_API_KEY"),
                model=self.model,
                messages=[
                    {"role": "system", "content": instructions},
                    {"role": "user", "content": question},
                ],
                result_format="message",
                enable_thinking=False,
                stream=True,
                incremental_output=True,
            )
            if self.workspace_id:
                options["workspace"] = self.workspace_id
            responses: Iterable = call(**options)
            for response in responses:
                with self._lock:
                    if generation != self._generation:
                        return
                status = getattr(response, "status_code", HTTPStatus.OK)
                if status != HTTPStatus.OK:
                    raise RuntimeError(getattr(response, "message", "Qwen 文本回答失败"))
                output = getattr(response, "output", None)
                choices = getattr(output, "choices", None)
                if choices is None and isinstance(output, dict):
                    choices = output.get("choices", [])
                if not choices:
                    continue
                message = getattr(choices[0], "message", None)
                if message is None and isinstance(choices[0], dict):
                    message = choices[0].get("message", {})
                delta = getattr(message, "content", None)
                if delta is None and isinstance(message, dict):
                    delta = message.get("content", "")
                if delta:
                    self.on_event(
                        {"type": "fast_answer_delta", "responseId": response_id, "delta": delta}
                    )
            self.on_event(
                {"type": "fast_answer_completed", "responseId": response_id, "status": "completed"}
            )
        except Exception as exc:
            self.on_event({"type": "fast_answer_error", "message": str(exc)})
