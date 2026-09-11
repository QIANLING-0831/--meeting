from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Callable


class CodexAppServerError(RuntimeError):
    pass


def resolve_codex_executable() -> str:
    """Locate Codex even when an Explorer-launched process has a stale PATH."""
    override = os.environ.get("INTERVIEW_COPILOT_CODEX", "").strip()
    if override:
        override_path = Path(override).expanduser()
        if override_path.is_file():
            return str(override_path)
        raise CodexAppServerError(f"INTERVIEW_COPILOT_CODEX 指向的文件不存在：{override_path}")

    path_match = shutil.which("codex")
    if path_match:
        return path_match

    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        desktop_bin = Path(local_app_data) / "OpenAI" / "Codex" / "bin"
        candidates = sorted(
            desktop_bin.glob("*/codex.exe"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if candidates:
            return str(candidates[0])

    raise CodexAppServerError(
        "找不到 Codex CLI。请先安装并运行 codex 完成登录；"
        "如已安装，可设置 INTERVIEW_COPILOT_CODEX 为 codex.exe 的完整路径。"
    )


class CodexAppServerClient:
    """Minimal JSON-RPC client for a long-lived local `codex app-server`."""

    def __init__(self, cwd: Path, on_event: Callable[[dict], None] | None = None) -> None:
        self.cwd = cwd.resolve()
        self.on_event = on_event or (lambda event: None)
        self.process: subprocess.Popen[str] | None = None
        self._next_id = 1
        self._pending: dict[int, queue.Queue] = {}
        self._write_lock = threading.Lock()
        self._thread_lock = threading.Lock()
        self._reader_thread: threading.Thread | None = None
        self._turn_finished = threading.Event()
        self._turn_finished.set()
        self.thread_id: str | None = None
        self.turn_id: str | None = None

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def start(self) -> None:
        if self.running:
            return
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        codex_executable = resolve_codex_executable()
        self.process = subprocess.Popen(
            [codex_executable, "app-server", "--stdio"],
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
        with self._thread_lock:
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
        self._turn_finished.clear()
        result = self.request("turn/start", params, timeout=10)
        self.turn_id = result["turn"]["id"]
        return result

    def interrupt(self, completion_timeout: float = 1.5) -> None:
        if self.thread_id and self.turn_id:
            interrupted_turn_id = self.turn_id
            self.request(
                "turn/interrupt",
                {"threadId": self.thread_id, "turnId": interrupted_turn_id},
                timeout=10,
            )
            # The RPC response only acknowledges the cancellation request.  A
            # new turn must not start until the old one has actually emitted
            # turn/completed, otherwise Codex can leave the retry queued behind
            # a still-running turn.
            if not self._turn_finished.wait(timeout=completion_timeout):
                raise CodexAppServerError("Codex 中断确认超时：旧回答尚未结束")
            if self.turn_id == interrupted_turn_id:
                self.turn_id = None

    def reset_thread(self) -> None:
        """Abandon a stuck conversation without restarting the signed-in server."""
        self.thread_id = None
        self.turn_id = None
        self._turn_finished.set()

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
        self._turn_finished.set()
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
                if message.get("method") == "turn/completed":
                    self._turn_finished.set()
                self.on_event(message)
        self._turn_finished.set()
        if self.process and self.process.poll() not in (None, 0):
            error = "Codex App Server 意外退出"
            if self.process.stderr:
                error = self.process.stderr.read().strip() or error
            self.on_event({"method": "server/error", "params": {"message": error}})
