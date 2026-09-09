from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
from pathlib import Path
from typing import Callable


class CodexAppServerError(RuntimeError):
    pass


class CodexAppServerClient:
    """Minimal JSON-RPC client for a long-lived local `codex app-server`."""

    def __init__(self, cwd: Path, on_event: Callable[[dict], None] | None = None) -> None:
        self.cwd = cwd.resolve()
        self.on_event = on_event or (lambda event: None)
        self.process: subprocess.Popen[str] | None = None
        self._next_id = 1
        self._pending: dict[int, queue.Queue] = {}
        self._write_lock = threading.Lock()
        self._reader_thread: threading.Thread | None = None
        self.thread_id: str | None = None
        self.turn_id: str | None = None

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def start(self) -> None:
        if self.running:
            return
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.process = subprocess.Popen(
            ["codex", "app-server", "--stdio"],
            cwd=self.cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=flags,
        )
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True, name="codex-app-server")
        self._reader_thread.start()
        self.request(
            "initialize",
            {"clientInfo": {"name": "interview_copilot", "title": "Interview Copilot", "version": "0.2.0"}},
            timeout=15,
        )
        self.notify("initialized", {})

    def account(self) -> dict:
        self.start()
        return self.request("account/read", {"refreshToken": False}, timeout=15)

    def models(self) -> list[dict]:
        self.start()
        return self.request(
            "model/list",
            {"limit": 100, "includeHidden": False},
            timeout=15,
        ).get("data", [])

    def begin_chatgpt_login(self) -> dict:
        account = self.account().get("account")
        if account:
            return {"type": "alreadyLoggedIn", "account": account}
        params = {
            "type": "chatgpt",
            "useHostedLoginSuccessPage": True,
            "appBrand": "chatgpt",
            "codexStreamlinedLogin": True,
        }
        try:
            return self.request("account/login/start", params, timeout=15)
        except CodexAppServerError as exc:
            if "超时" not in str(exc):
                raise
            self.close()
            self.start()
            return self.request("account/login/start", params, timeout=15)

    def ensure_thread(self, *, model: str = "", instructions: str = "") -> str:
        self.start()
        if self.thread_id:
            return self.thread_id
        params: dict = {
            "cwd": str(self.cwd),
            "approvalPolicy": "never",
            "sandbox": "read-only",
            "ephemeral": False,
            "developerInstructions": instructions,
        }
        if model:
            params["model"] = model
        result = self.request("thread/start", params, timeout=10)
        self.thread_id = result["thread"]["id"]
        return self.thread_id

    def start_turn(self, prompt: str, *, model: str = "", effort: str = "") -> dict:
        if not self.thread_id:
            raise CodexAppServerError("Codex 会话尚未创建")
        params: dict = {
            "threadId": self.thread_id,
            "input": [{"type": "text", "text": prompt}],
        }
        if model:
            params["model"] = model
        if effort:
            params["effort"] = effort
        result = self.request(
            "turn/start",
            params,
            timeout=10,
        )
        self.turn_id = result["turn"]["id"]
        return result

    def interrupt(self) -> None:
        if self.thread_id and self.turn_id:
            self.request(
                "turn/interrupt",
                {"threadId": self.thread_id, "turnId": self.turn_id},
                timeout=10,
            )

    def request(self, method: str, params: dict | None, timeout: float = 20) -> dict:
        request_id = self._next_id
        self._next_id += 1
        response_queue: queue.Queue = queue.Queue(maxsize=1)
        self._pending[request_id] = response_queue
        self._send({"method": method, "id": request_id, "params": params})
        try:
            response = response_queue.get(timeout=timeout)
        except queue.Empty as exc:
            self._pending.pop(request_id, None)
            raise CodexAppServerError(f"Codex 请求超时：{method}") from exc
        if "error" in response:
            raise CodexAppServerError(response["error"].get("message", str(response["error"])))
        return response.get("result", {})

    def notify(self, method: str, params: dict | None) -> None:
        self._send({"method": method, "params": params})

    def close(self) -> None:
        process = self.process
        self.process = None
        self.thread_id = None
        self.turn_id = None
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()

    def _send(self, message: dict) -> None:
        if not self.process or not self.process.stdin:
            raise CodexAppServerError("Codex App Server 未运行")
        payload = json.dumps(message, ensure_ascii=False)
        with self._write_lock:
            self.process.stdin.write(payload + "\n")
            self.process.stdin.flush()

    def _read_loop(self) -> None:
        assert self.process and self.process.stdout
        for line in self.process.stdout:
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            request_id = message.get("id")
            if request_id is not None and request_id in self._pending:
                self._pending.pop(request_id).put(message)
            elif "method" in message:
                self.on_event(message)
        if self.process and self.process.poll() not in (None, 0):
            error = "Codex App Server 意外退出"
            if self.process.stderr:
                error = self.process.stderr.read().strip() or error
            self.on_event({"method": "server/error", "params": {"message": error}})
