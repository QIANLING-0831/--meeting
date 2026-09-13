from __future__ import annotations

import argparse
import ctypes
import json
import queue
import re
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any


class OverlayManager:
    """Run the Tk overlay out of process so it cannot destabilize audio capture."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self._process: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()
        self._hidden = False
        self._frozen = False
        self._locked = False
        self._hotkeys: dict[str, bool] = {}
        self.last_error = ""

    @property
    def running(self) -> bool:
        return bool(self._process and self._process.poll() is None)

    def state(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "hidden": self._hidden,
            "frozen": self._frozen,
            "locked": self._locked,
            "hotkeys": dict(self._hotkeys),
            "error": self.last_error,
        }

    def start(self) -> bool:
        with self._lock:
            if self.running:
                self._send("show")
                return True
            self.last_error = ""
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            self._process = subprocess.Popen(
                [sys.executable, "-m", "interview_copilot.overlay", "--base-url", self.base_url],
                cwd=Path(__file__).resolve().parent.parent,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                creationflags=flags,
            )
        time.sleep(0.35)
        if self.running:
            self._hidden = self._frozen = self._locked = False
            return True
        process = self._process
        if process:
            _, error = process.communicate(timeout=1)
            self.last_error = error.strip() or "窗口进程启动后立即退出"
        return False

    def stop(self) -> None:
        with self._lock:
            process = self._process
            if not process:
                return
            self._send("quit")
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=2)
            self._process = None
            self._hidden = False

    def _send(self, command: str) -> None:
        process = self._process
        if not process or process.poll() is not None or not process.stdin:
            return
        try:
            process.stdin.write(f"{command}\n")
            process.stdin.flush()
        except (BrokenPipeError, OSError):
            pass

    def show(self) -> None:
        self._hidden = False
        self._send("show")

    def hide(self) -> None:
        self._hidden = True
        self._send("hide")

    def toggle_freeze(self) -> None:
        self._frozen = not self._frozen
        self._send("freeze")

    def toggle_lock(self) -> None:
        self.set_locked(not self._locked)

    def set_locked(self, locked: bool) -> None:
        self._locked = locked
        self._send("lock_on" if locked else "lock_off")

    def settings_changed(self) -> None:
        self._send("refresh")

    def update_runtime(
        self, hidden: bool, frozen: bool, locked: bool, hotkeys: dict[str, bool] | None = None
    ) -> None:
        self._hidden, self._frozen, self._locked = hidden, frozen, locked
        if hotkeys is not None:
            self._hotkeys = dict(hotkeys)


class OverlayWindow:
    def __init__(self, base_url: str) -> None:
        import tkinter as tk

        try:
            ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except Exception:
            pass
        self.tk = tk
        self.base_url = base_url.rstrip("/")
        self.commands: queue.SimpleQueue[str] = queue.SimpleQueue()
        self.events: queue.SimpleQueue[dict[str, Any]] = queue.SimpleQueue()
        self.settings: dict[str, Any] = {}
        self.snapshot: dict[str, Any] = {}
        self.hidden = False
        self.frozen = False
        self.locked = False
        self.running = True
        self.transparent_color = "#010203"
        self.root = tk.Tk()
        self.root.title("Interview Copilot 悬浮回答台")
        self.root.configure(bg=self.transparent_color)
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-transparentcolor", self.transparent_color)
        self.root.protocol("WM_DELETE_WINDOW", self.hide)
        self.backdrop = tk.Toplevel(self.root)
        self.backdrop.title("Interview Copilot 背景层")
        self.backdrop.configure(bg="#0B100E")
        self.backdrop.overrideredirect(True)
        self.backdrop.attributes("-topmost", True)
        self._build_ui()
        self._load_data(initial=True)
        self._register_hotkeys()
        self._report_runtime()
        threading.Thread(target=self._read_commands, daemon=True).start()
        threading.Thread(target=self._read_events, daemon=True).start()

    def run(self) -> None:
        self.root.after(40, self._tick)
        self.root.mainloop()
        self._unregister_hotkeys()

    def _request(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = None
        headers: dict[str, str] = {}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(f"{self.base_url}{path}", data=data, headers=headers)
        with urllib.request.urlopen(request, timeout=0.45) as response:
            return json.loads(response.read().decode("utf-8"))

    def _load_data(self, initial: bool = False) -> None:
        try:
            data = self._request("/api/overlay/data")
        except Exception:
            if initial:
                self.settings = {
                    "opacity": 0.88,
                    "backgroundOpacity": 0.78,
                    "textOpacity": 1.0,
                    "fontSize": 24,
                    "historyFontSize": 14,
                    "textColor": "#E8F5EE",
                    "historyColor": "#A9BBB2",
                    "showQuestion": True,
                    "showHistory": True,
                    "historyCount": 2,
                    "highlightProgress": True,
                    "sizePreset": "standard",
                }
            return
        self.settings = data.get("settings", self.settings)
        if not self.frozen:
            self.snapshot = data.get("snapshot", self.snapshot)
        if initial:
            self._apply_geometry(data.get("geometry", {}))
        self._apply_visual_settings()

    def _read_commands(self) -> None:
        for line in sys.stdin:
            self.commands.put(line.strip())

    def _read_events(self) -> None:
        from websocket import WebSocketTimeoutException, create_connection

        websocket_url = self.base_url.replace("http://", "ws://", 1).replace("https://", "wss://", 1)
        while self.running:
            connection = None
            try:
                connection = create_connection(f"{websocket_url}/ws", timeout=1)
                connection.settimeout(0.5)
                self.commands.put("refresh")
                while self.running:
                    try:
                        event = json.loads(connection.recv())
                    except WebSocketTimeoutException:
                        continue
                    if event.get("type") == "answer_snapshot":
                        self.events.put(event.get("snapshot", {}))
            except Exception:
                time.sleep(0.5)
            finally:
                if connection:
                    connection.close()

    def _tick(self) -> None:
        if not self.running:
            self.root.destroy()
            return
        while True:
            try:
                command = self.commands.get_nowait()
            except queue.Empty:
                break
            self._handle_command(command)
        while True:
            try:
                snapshot = self.events.get_nowait()
            except queue.Empty:
                break
            if not self.frozen:
                self.snapshot = snapshot
        self._render()
        self._sync_backdrop()
        self.root.after(120, self._tick)

    def _handle_command(self, command: str) -> None:
        if command == "quit":
            self.running = False
        elif command == "show":
            self.show()
        elif command == "hide":
            self.hide()
        elif command == "freeze":
            self.toggle_freeze()
        elif command == "lock":
            self.toggle_lock()
        elif command == "lock_on":
            self.set_locked(True)
        elif command == "lock_off":
            self.set_locked(False)
        elif command == "refresh":
            self._load_data()

    def _build_ui(self) -> None:
        tk = self.tk
        root = self.root
        root.grid_columnconfigure(0, weight=1)
        root.grid_rowconfigure(2, weight=3)
        root.grid_rowconfigure(3, weight=2)
        self.bar = tk.Frame(root, bg="#111A16", height=32, cursor="fleur")
        self.bar.grid(row=0, column=0, sticky="nsew")
        self.bar.grid_columnconfigure(1, weight=1)
        self.status = tk.Label(self.bar, text="等待问题", bg="#111A16", fg="#6DE3AA", cursor="fleur", font=("Microsoft YaHei UI", 9))
        self.status.grid(row=0, column=0, padx=(10, 4), pady=5)
        self.hint = tk.Label(self.bar, text="拖动此栏移动 · Ctrl+Alt+H 隐藏", bg="#111A16", fg="#718078", cursor="fleur", font=("Microsoft YaHei UI", 8))
        self.hint.grid(row=0, column=1, sticky="e", padx=5)
        self.freeze_button = self._bar_button("冻结", self.toggle_freeze, 2)
        self.lock_button = self._bar_button("锁定", self.toggle_lock, 3)
        self._bar_button("隐藏", self.hide, 4)
        for widget in (self.bar, self.status, self.hint):
            widget.bind("<ButtonPress-1>", self._drag_start)
            widget.bind("<B1-Motion>", self._drag_move)
            widget.bind("<ButtonRelease-1>", self._save_geometry)
        for sequence, callback in (
            ("<ButtonPress-1>", self._drag_start),
            ("<B1-Motion>", self._drag_move),
            ("<ButtonRelease-1>", self._save_geometry),
        ):
            self.backdrop.bind(sequence, callback)
        self.question = tk.Label(root, anchor="w", justify="left", bg=self.transparent_color, fg="#E5C06F", padx=12, pady=6, font=("Microsoft YaHei UI", 10))
        self.question.grid(row=1, column=0, sticky="nsew")
        self.answer = tk.Text(root, wrap="word", relief="flat", borderwidth=0, padx=16, pady=12, cursor="arrow", takefocus=0)
        self.answer.grid(row=2, column=0, sticky="nsew")
        self.answer.configure(state="disabled")
        self.history = tk.Text(root, wrap="word", relief="flat", borderwidth=0, padx=14, pady=9, cursor="arrow", takefocus=0)
        self.history.grid(row=3, column=0, sticky="nsew")
        self.history.configure(state="disabled")
        for widget in (self.question, self.answer, self.history):
            widget.bind("<ButtonPress-1>", self._drag_start)
            widget.bind("<B1-Motion>", self._drag_move)
            widget.bind("<ButtonRelease-1>", self._save_geometry)
        self.resize_handles: dict[str, Any] = {}
        self.resize_places = {
            "n": {"relx": 0, "rely": 0, "relwidth": 1, "height": 3, "anchor": "nw"},
            "s": {"relx": 0, "rely": 1, "relwidth": 1, "height": 3, "anchor": "sw"},
            "w": {"relx": 0, "rely": 0, "width": 3, "relheight": 1, "anchor": "nw"},
            "e": {"relx": 1, "rely": 0, "width": 3, "relheight": 1, "anchor": "ne"},
            "nw": {"relx": 0, "rely": 0, "width": 8, "height": 8, "anchor": "nw"},
            "ne": {"relx": 1, "rely": 0, "width": 8, "height": 8, "anchor": "ne"},
            "sw": {"relx": 0, "rely": 1, "width": 8, "height": 8, "anchor": "sw"},
            "se": {"relx": 1, "rely": 1, "width": 8, "height": 8, "anchor": "se"},
        }
        cursors = {"n": "size_ns", "s": "size_ns", "w": "size_we", "e": "size_we", "nw": "size_nw_se", "se": "size_nw_se", "ne": "size_ne_sw", "sw": "size_ne_sw"}
        for direction, placement in self.resize_places.items():
            handle = tk.Frame(root, bg="#1A2922", cursor=cursors[direction])
            handle.place(**placement)
            handle.bind("<ButtonPress-1>", lambda event, edge=direction: self._resize_start(event, edge))
            handle.bind("<B1-Motion>", self._resize_move)
            handle.bind("<ButtonRelease-1>", self._save_geometry)
            handle.bind("<Enter>", lambda _event, item=handle: item.configure(bg="#315542"))
            handle.bind("<Leave>", lambda _event, item=handle: item.configure(bg="#1A2922"))
            self.resize_handles[direction] = handle
        self.drag_origin = (0, 0, 0, 0)
        self.resize_origin = (0, 0, 0, 0)
        self.resize_direction = "se"

    def _bar_button(self, text: str, command: Any, column: int) -> Any:
        button = self.tk.Button(self.bar, text=text, command=command, bg="#223029", fg="#C9D5CF", activebackground="#315542", activeforeground="#FFFFFF", relief="flat", borderwidth=0, font=("Microsoft YaHei UI", 8), padx=8, pady=3)
        button.grid(row=0, column=column, padx=(0, 4), pady=4)
        return button

    def _apply_geometry(self, geometry: dict[str, Any]) -> None:
        screen_width, screen_height = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        presets = {"compact": (820, 360), "standard": (1120, 480), "wide": (1280, 560)}
        width, height = int(geometry.get("width", 0)), int(geometry.get("height", 0))
        if width <= 0 or height <= 0:
            width, height = presets.get(self.settings.get("sizePreset", "standard"), presets["standard"])
            if screen_width < 1600:
                width, height = max(360, int(screen_width * 0.58)), max(180, int(screen_height * 0.44))
        width, height = min(width, int(screen_width * 0.92)), min(height, int(screen_height * 0.85))
        stored_x, stored_y = geometry.get("x"), geometry.get("y")
        legacy_unset = stored_x == -1 and stored_y == -1
        if stored_x is None or stored_y is None or legacy_unset:
            x, y = (screen_width - width) // 2, 60
        else:
            x, y = int(stored_x), int(stored_y)
            virtual_x = ctypes.windll.user32.GetSystemMetrics(76)
            virtual_y = ctypes.windll.user32.GetSystemMetrics(77)
            virtual_width = ctypes.windll.user32.GetSystemMetrics(78)
            virtual_height = ctypes.windll.user32.GetSystemMetrics(79)
            x = max(virtual_x, min(x, virtual_x + virtual_width - width))
            y = max(virtual_y, min(y, virtual_y + virtual_height - height))
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self._sync_backdrop(raise_text=True)

    def _sync_backdrop(self, raise_text: bool = False) -> None:
        if not hasattr(self, "backdrop"):
            return
        target = (self.root.winfo_width(), self.root.winfo_height(), self.root.winfo_x(), self.root.winfo_y())
        current = (self.backdrop.winfo_width(), self.backdrop.winfo_height(), self.backdrop.winfo_x(), self.backdrop.winfo_y())
        if current != target:
            width, height, x, y = target
            self.backdrop.geometry(f"{width}x{height}+{x}+{y}")
        self.backdrop.attributes("-topmost", True)
        self.root.attributes("-topmost", True)
        if raise_text:
            self.root.lift(self.backdrop)

    def _apply_visual_settings(self) -> None:
        if not self.settings:
            return
        background_opacity = float(self.settings.get("backgroundOpacity", self.settings.get("opacity", 0.78)))
        text_opacity = float(self.settings.get("textOpacity", 1.0))
        self.backdrop.attributes("-alpha", max(0.05, min(1.0, background_opacity)))
        self.root.attributes("-alpha", max(0.15, min(1.0, text_opacity)))
        self.question.grid() if self.settings.get("showQuestion", True) else self.question.grid_remove()
        if self.settings.get("showHistory", True):
            self.history.grid()
            self.root.grid_rowconfigure(2, weight=3)
            self.root.grid_rowconfigure(3, weight=2)
        else:
            self.history.grid_remove()
            self.root.grid_rowconfigure(2, weight=1)
            self.root.grid_rowconfigure(3, weight=0)
        font_size = int(self.settings.get("fontSize", 24))
        self.answer.configure(bg=self.transparent_color, fg=self.settings.get("textColor", "#E8F5EE"), font=("Microsoft YaHei UI", font_size))
        self.answer.tag_configure("covered", foreground="#65736C")
        self.answer.tag_configure("next", foreground="#FFFFFF", background="#183E2E")
        self.history.configure(bg=self.transparent_color, fg=self.settings.get("historyColor", "#A9BBB2"))

    def _render(self) -> None:
        pending = str(self.snapshot.get("pendingQuestion", ""))
        state = str(self.snapshot.get("candidateState", "idle"))
        labels = {"idle": "等待问题", "waiting": "答案已就绪", "answering": "正在听取我的回答", "likely_complete": "初步判断回答接近完成", "answered": "上一题已回答", "interrupted": "检测到追问，已保留上一题"}
        error = str(self.snapshot.get("error", ""))
        status = "已冻结显示" if self.frozen else labels.get(state, "实时同步")
        if error:
            status = "回答生成异常 · 上次回答已保留"
        elif pending:
            status = "新问题处理中 · 当前答案继续保留"
        self.status.configure(text=status)
        self.freeze_button.configure(text="继续" if self.frozen else "冻结")
        self.lock_button.configure(text="解锁" if self.locked else "锁定")
        question = str(self.snapshot.get("question", "")) or "识别到有效问题后显示"
        limit = max(28, int(max(1, self.root.winfo_width()) / 13))
        self.question.configure(text=question if len(question) <= limit else f"{question[:limit - 1]}…")
        self._render_answer()
        self._render_history()

    def _replace_text(self, widget: Any, parts: list[tuple[str, str]]) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        for value, tag in parts:
            widget.insert("end", value, tag)
        widget.configure(state="disabled")
        widget.yview_moveto(0)

    def _render_answer(self) -> None:
        answer = str(self.snapshot.get("text", "")) or "等待 Qwen 生成回答…"
        covered = int(self.snapshot.get("coveredSentenceCount", 0)) if self.settings.get("highlightProgress", True) else 0
        sentences = [part for part in re.split(r"(?<=[。！？!?；;])", answer) if part]
        parts = [(sentence, "covered" if index < covered else "next" if covered and index == covered else "") for index, sentence in enumerate(sentences)]
        self._replace_text(self.answer, parts or [(answer, "")])

    def _render_history(self) -> None:
        count = int(self.settings.get("historyCount", 2))
        history = list(self.snapshot.get("history", []))[:count]
        content = "历史问答会保留在这里" if not history else "\n\n".join(f"历史 {index}｜{item.get('question', '')}\n{item.get('answer', '')}" for index, item in enumerate(history, 1))
        base_size = int(self.settings.get("historyFontSize", 14))
        self.history.configure(font=("Microsoft YaHei UI", self._fit_history_font(content, base_size)))
        self._replace_text(self.history, [(content, "")])

    def _fit_history_font(self, content: str, base_size: int) -> int:
        width, height = max(360, self.root.winfo_width()), max(180, self.root.winfo_height())
        chars_per_line = max(20, int((width - 34) / max(8, base_size * 0.95)))
        lines = sum(max(1, (len(line) + chars_per_line - 1) // chars_per_line) for line in content.splitlines())
        required, available = lines * (base_size + 8) + 24, height * 0.40
        screen_limit = int(self.root.winfo_screenheight() * 0.85)
        if required > available and height < screen_limit:
            target = min(screen_limit, max(height, int(required / 0.40)))
            self.root.geometry(f"{width}x{target}+{self.root.winfo_x()}+{max(0, min(self.root.winfo_y(), self.root.winfo_screenheight() - target))}")
            available = target * 0.40
        return base_size if required <= available else max(6, int(base_size * available / required))

    def show(self) -> None:
        self.hidden = False
        self.backdrop.deiconify()
        self.root.deiconify()
        self._sync_backdrop(raise_text=True)
        self._report_runtime()

    def hide(self) -> None:
        self.hidden = True
        self.root.withdraw()
        self.backdrop.withdraw()
        self._report_runtime()

    def toggle_freeze(self) -> None:
        self.frozen = not self.frozen
        self._report_runtime()

    def toggle_lock(self) -> None:
        self.set_locked(not self.locked)

    def set_locked(self, locked: bool) -> None:
        self.locked = locked
        for direction, handle in self.resize_handles.items():
            handle.place_forget() if self.locked else handle.place(**self.resize_places[direction])
        for window in (self.root, self.backdrop):
            hwnd = window.winfo_id()
            current = ctypes.windll.user32.GetWindowLongW(hwnd, -20)
            style = current | 0x80000 | 0x20 if self.locked else current & ~0x20
            ctypes.windll.user32.SetWindowLongW(hwnd, -20, style)
        self._report_runtime()

    def _report_runtime(self) -> None:
        try:
            self._request(
                "/api/overlay/runtime",
                {
                    "hidden": self.hidden,
                    "frozen": self.frozen,
                    "locked": self.locked,
                    "hotkeys": self.hotkey_status,
                },
            )
        except Exception:
            pass

    def _save_geometry(self, _event: Any = None) -> None:
        if self.locked:
            return
        try:
            self._request("/api/overlay/geometry", {"x": self.root.winfo_x(), "y": self.root.winfo_y(), "width": self.root.winfo_width(), "height": self.root.winfo_height()})
        except Exception:
            pass

    def _drag_start(self, event: Any) -> None:
        if not self.locked:
            self.root.lift(self.backdrop)
            self.drag_origin = (event.x_root, event.y_root, self.root.winfo_x(), self.root.winfo_y())

    def _drag_move(self, event: Any) -> None:
        if not self.locked:
            sx, sy, wx, wy = self.drag_origin
            x, y = wx + event.x_root - sx, wy + event.y_root - sy
            width, height = self.root.winfo_width(), self.root.winfo_height()
            self.root.geometry(f"+{x}+{y}")
            self.backdrop.geometry(f"{width}x{height}+{x}+{y}")

    def _resize_start(self, event: Any, direction: str = "se") -> None:
        if not self.locked:
            self.resize_direction = direction
            self.resize_origin = (
                event.x_root,
                event.y_root,
                self.root.winfo_x(),
                self.root.winfo_y(),
                self.root.winfo_width(),
                self.root.winfo_height(),
            )

    def _resize_move(self, event: Any) -> None:
        if not self.locked:
            sx, sy, x, y, width, height = self.resize_origin
            dx, dy = event.x_root - sx, event.y_root - sy
            new_x, new_y, new_width, new_height = x, y, width, height
            if "e" in self.resize_direction:
                new_width = max(360, min(1800, width + dx))
            if "s" in self.resize_direction:
                new_height = max(180, min(int(self.root.winfo_screenheight() * 0.85), height + dy))
            if "w" in self.resize_direction:
                new_width = max(360, min(1800, width - dx))
                new_x = x + width - new_width
            if "n" in self.resize_direction:
                new_height = max(180, min(int(self.root.winfo_screenheight() * 0.85), height - dy))
                new_y = y + height - new_height
            self.root.geometry(f"{new_width}x{new_height}+{new_x}+{new_y}")
            self.backdrop.geometry(f"{new_width}x{new_height}+{new_x}+{new_y}")

    def _register_hotkeys(self) -> None:
        self.hotkey_status: dict[str, bool] = {}
        self.hotkey_thread_id = 0
        self.hotkey_ready = threading.Event()
        self.hotkey_thread = threading.Thread(target=self._hotkey_message_loop, daemon=True)
        self.hotkey_thread.start()
        self.hotkey_ready.wait(timeout=1.0)

    def _unregister_hotkeys(self) -> None:
        if self.hotkey_thread_id:
            ctypes.windll.user32.PostThreadMessageW(self.hotkey_thread_id, 0x0012, 0, 0)
        if getattr(self, "hotkey_thread", None):
            self.hotkey_thread.join(timeout=0.5)

    def _hotkey_message_loop(self) -> None:
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        self.hotkey_thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        message = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)
        modifiers = 0x0001 | 0x0002 | 0x4000
        hotkeys = ((1, "hide", "H"), (2, "freeze", "P"), (3, "lock", "L"))
        for hotkey_id, name, key in hotkeys:
            self.hotkey_status[name] = bool(user32.RegisterHotKey(None, hotkey_id, modifiers, ord(key)))
        self.hotkey_ready.set()
        try:
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                if message.message != 0x0312:
                    continue
                command = {1: "hide" if not self.hidden else "show", 2: "freeze", 3: "lock"}.get(
                    int(message.wParam)
                )
                if command:
                    self.commands.put(command)
        finally:
            for hotkey_id, name, _key in hotkeys:
                if self.hotkey_status.get(name):
                    user32.UnregisterHotKey(None, hotkey_id)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    args = parser.parse_args()
    OverlayWindow(args.base_url).run()


if __name__ == "__main__":
    main()
