from __future__ import annotations

import base64
import json
import os
import queue
import threading
from collections.abc import Callable
from urllib.parse import urlencode

import numpy as np


RealtimeEventHandler = Callable[[dict], None]


def _resample(audio: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    if source_rate == target_rate or audio.size == 0:
        return np.asarray(audio, dtype=np.float32)
    target_length = max(1, round(audio.size * target_rate / source_rate))
    old_x = np.linspace(0.0, 1.0, num=audio.size, endpoint=False)
    new_x = np.linspace(0.0, 1.0, num=target_length, endpoint=False)
    return np.interp(new_x, old_x, audio).astype(np.float32)


class AliyunRealtimeAnswerStream:
    """Send one 16 kHz PCM channel to Qwen-Audio and emit text-answer events."""

    def __init__(
        self,
        on_event: RealtimeEventHandler,
        *,
        model: str = "qwen-audio-3.0-realtime-flash",
        api_key: str | None = None,
        workspace_id: str = "",
        turn_detection: str = "smart_turn",
        instructions: str = "",
    ) -> None:
        self._api_key = api_key or os.environ.get("DASHSCOPE_API_KEY", "")
        if not self._api_key:
            raise RuntimeError("未设置 DASHSCOPE_API_KEY，无法使用 Qwen 实时回答。")
        if model not in {
            "qwen-audio-3.0-realtime-flash",
            "qwen-audio-3.0-realtime-plus",
        }:
            raise ValueError("不支持的 Qwen 实时回答模型")
        if turn_detection not in {"smart_turn", "server_vad"}:
            raise ValueError("Qwen 实时轮次检测仅支持 smart_turn 或 server_vad")

        self.on_event = on_event
        self.model = model
        self.workspace_id = workspace_id.strip()
        self.turn_detection = turn_detection
        self.instructions = instructions.strip()
        self._audio_queue: queue.Queue[bytes | None] = queue.Queue(maxsize=80)
        self._configured = threading.Event()
        self._stopped = threading.Event()
        self._connection_error: Exception | None = None
        self._ws = None
        self._socket_thread: threading.Thread | None = None
        self._sender_thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        if self.workspace_id:
            base = (
                f"wss://{self.workspace_id}.cn-beijing.maas.aliyuncs.com"
                "/api-ws/v1/realtime"
            )
        else:
            # Compatibility endpoint for accounts that have not configured a
            # Bailian business-space ID yet.
            base = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
        return f"{base}?{urlencode({'model': self.model})}"

    def start(self, timeout: float = 8.0) -> None:
        if self._socket_thread and self._socket_thread.is_alive():
            return
        import websocket

        self._stopped.clear()
        self._configured.clear()
        self._connection_error = None
        self._ws = websocket.WebSocketApp(
            self.url,
            header=[f"Authorization: Bearer {self._api_key}"],
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        self._socket_thread = threading.Thread(
            target=self._ws.run_forever,
            daemon=True,
            name="qwen-realtime-socket",
        )
        self._socket_thread.start()
        if not self._configured.wait(timeout):
            error = self._connection_error or RuntimeError("连接或会话配置超时")
            self.stop()
            raise RuntimeError(f"Qwen 实时回答启动失败：{error}")
        if self._connection_error:
            error = self._connection_error
            self.stop()
            raise RuntimeError(f"Qwen 实时回答启动失败：{error}")
        self._sender_thread = threading.Thread(
            target=self._send_loop,
            daemon=True,
            name="qwen-realtime-audio",
        )
        self._sender_thread.start()

    def send(self, audio: np.ndarray, source_rate: int) -> None:
        if not self._configured.is_set() or self._stopped.is_set():
            return
        samples = _resample(audio, source_rate, 16_000)
        pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes()
        try:
            self._audio_queue.put_nowait(pcm)
        except queue.Full:
            # Keep capture real-time: discard the oldest 100 ms block instead
            # of blocking the system-audio callback indefinitely.
            try:
                self._audio_queue.get_nowait()
            except queue.Empty:
                pass
            self._audio_queue.put_nowait(pcm)

    def stop(self) -> None:
        self._stopped.set()
        self._configured.clear()
        try:
            self._audio_queue.put_nowait(None)
        except queue.Full:
            pass
        if self._ws:
            self._ws.close()
        current = threading.current_thread()
        for thread in (self._sender_thread, self._socket_thread):
            if thread and thread is not current:
                thread.join(timeout=2)
        self._ws = None
        self._sender_thread = None
        self._socket_thread = None
        while True:
            try:
                self._audio_queue.get_nowait()
            except queue.Empty:
                break

    def _on_open(self, ws) -> None:
        turn_detection: dict[str, object]
        if self.turn_detection == "smart_turn":
            turn_detection = {"type": "smart_turn"}
        else:
            turn_detection = {
                "type": "server_vad",
                "threshold": 0.5,
                "silence_duration_ms": 600,
            }
        ws.send(
            json.dumps(
                {
                    "type": "session.update",
                    "session": {
                        "modalities": ["text"],
                        "instructions": self.instructions,
                        "turn_detection": turn_detection,
                        "max_history_turns": 8,
                    },
                },
                ensure_ascii=False,
            )
        )

    def _on_message(self, _ws, message: str) -> None:
        try:
            event = json.loads(message)
        except json.JSONDecodeError:
            return
        event_type = event.get("type")
        if event_type == "session.updated":
            self._configured.set()
            self.on_event({"type": "fast_channel_ready", "model": self.model})
        elif event_type == "conversation.item.input_audio_transcription.completed":
            self.on_event(
                {
                    "type": "fast_question_transcript",
                    "text": event.get("transcript", ""),
                    "itemId": event.get("item_id", ""),
                }
            )
        elif event_type == "response.created":
            response = event.get("response") or {}
            self.on_event(
                {"type": "fast_answer_started", "responseId": response.get("id", "")}
            )
        elif event_type == "response.text.delta":
            self.on_event(
                {
                    "type": "fast_answer_delta",
                    "responseId": event.get("response_id", ""),
                    "delta": event.get("delta", ""),
                }
            )
        elif event_type == "response.text.done":
            self.on_event(
                {
                    "type": "fast_answer_text_done",
                    "responseId": event.get("response_id", ""),
                    "text": event.get("text", ""),
                }
            )
        elif event_type == "response.done":
            response = event.get("response") or {}
            self.on_event(
                {
                    "type": "fast_answer_completed",
                    "responseId": response.get("id", ""),
                    "status": response.get("status", "completed"),
                }
            )
        elif event_type == "error":
            error = event.get("error") or {}
            self.on_event(
                {
                    "type": "fast_answer_error",
                    "message": error.get("message", "Qwen 实时回答服务异常"),
                }
            )

    def _on_error(self, _ws, error) -> None:
        self._connection_error = error if isinstance(error, Exception) else RuntimeError(str(error))
        self._configured.set()
        if not self._stopped.is_set():
            self.on_event({"type": "fast_answer_error", "message": str(error)})

    def _on_close(self, _ws, _code, reason) -> None:
        if not self._configured.is_set() and not self._stopped.is_set():
            self._connection_error = RuntimeError(reason or "连接已关闭")
            self._configured.set()
        elif not self._stopped.is_set():
            self.on_event(
                {"type": "fast_answer_error", "message": reason or "Qwen 实时连接已断开"}
            )

    def _send_loop(self) -> None:
        while not self._stopped.is_set():
            try:
                pcm = self._audio_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if pcm is None:
                return
            try:
                self._ws.send(
                    json.dumps(
                        {
                            "type": "input_audio_buffer.append",
                            "audio": base64.b64encode(pcm).decode("ascii"),
                        }
                    )
                )
            except Exception as exc:
                if not self._stopped.is_set():
                    self.on_event({"type": "fast_answer_error", "message": str(exc)})
                return
