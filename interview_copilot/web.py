from __future__ import annotations

import asyncio
import json
import os
import queue
import tempfile
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .audio import default_input_device_id, list_input_devices, list_loopback_devices, probe_loopback_levels
from .config import AppConfig
from .event_bus import EventBus
from .external_sources import import_github_source
from .overlay import OverlayManager
from .qwen_only import QwenOnlyEngine
from .streaming_audio import StreamingAudioCoordinator

ROOT = Path(__file__).resolve().parent.parent
STATIC = Path(__file__).resolve().parent / "static"


class FactsPayload(BaseModel):
    text: str


class ExternalSourcePayload(BaseModel):
    url: str
    label: str = ""


class KnowledgePacksPayload(BaseModel):
    packs: list[str]


class StartPayload(BaseModel):
    loopback_device_name: str = ""
    microphone_enabled: bool = False
    microphone_device_id: str = ""


class RealtimeSettingsPayload(BaseModel):
    pipeline_mode: str = "realtime"
    model: str = "qwen-audio-3.0-realtime-plus"
    answer_model: str = "qwen-plus"
    workspace_id: str = ""
    turn_detection: str = "server_vad"


class AliyunKeyPayload(BaseModel):
    api_key: str
    confirm_api_key: str


class OverlaySettingsPayload(BaseModel):
    opacity: float = 0.88
    background_opacity: float = 0.78
    text_opacity: float = 1.0
    font_size: int = 24
    history_font_size: int = 14
    text_color: str = "#E8F5EE"
    history_color: str = "#A9BBB2"
    show_question: bool = True
    show_history: bool = True
    history_count: int = 2
    highlight_progress: bool = True
    auto_microphone: bool = True
    size_preset: str = "standard"


class OverlayGeometryPayload(BaseModel):
    x: int
    y: int
    width: int
    height: int


class OverlayRuntimePayload(BaseModel):
    hidden: bool
    frozen: bool
    locked: bool
    hotkeys: dict[str, bool] = Field(default_factory=dict)


def _mask_api_key(api_key: str) -> str:
    value = api_key.strip()
    return "****" if len(value) < 8 else f"{value[:3]}****{value[-4:]}"


class _BrowserConnectionTracker:
    """Stop Qwen and audio after the final browser page disconnects."""

    def __init__(self, engine, audio, overlay, bus: EventBus, grace_seconds: float) -> None:
        self.engine, self.audio, self.overlay, self.bus = engine, audio, overlay, bus
        self.grace_seconds = max(0.0, grace_seconds)
        self._connections = 0
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def connected(self) -> None:
        with self._lock:
            self._connections += 1
            if self._timer:
                self._timer.cancel()
                self._timer = None

    def disconnected(self) -> None:
        with self._lock:
            self._connections = max(0, self._connections - 1)
            if self._connections or self._timer:
                return
            self._timer = threading.Timer(self.grace_seconds, self._stop_if_abandoned)
            self._timer.daemon = True
            self._timer.start()

    def close(self) -> None:
        with self._lock:
            if self._timer:
                self._timer.cancel()
                self._timer = None

    def _stop_if_abandoned(self) -> None:
        with self._lock:
            self._timer = None
            if self._connections or not self.engine.active or self.overlay.running:
                return
            self.engine.active = False
        self.audio.stop()
        self.bus.publish({"type": "interview_state", "running": False})


def create_app(root: Path | None = None) -> FastAPI:
    app_root = (root or ROOT).resolve()
    config = AppConfig.load(app_root)
    if config.aliyun_api_key.strip():
        os.environ["DASHSCOPE_API_KEY"] = config.aliyun_api_key.strip()
    bus = EventBus()
    engine = QwenOnlyEngine(
        app_root,
        bus,
        config.qwen_answer_model,
        workspace_id=config.qwen_realtime_workspace_id,
    )
    latest_session = engine.sessions.latest()
    if latest_session:
        engine.set_session(latest_session)
    audio = StreamingAudioCoordinator(
        config,
        on_text=engine.on_asr_text,
        on_audio_level=lambda speaker, level: bus.publish(
            {"type": "audio_level", "speaker": speaker, "level": level}
        ),
        on_audio_status=lambda speaker, status, message: bus.publish(
            {"type": "audio_status", "speaker": speaker, "status": status, "message": message}
        ),
        on_fast_event=engine.on_qwen_event,
        instructions_provider=engine.instructions,
    )

    overlay = OverlayManager(f"http://{config.web_host}:{config.web_port}")
    browser_connections = _BrowserConnectionTracker(
        engine, audio, overlay, bus, config.browser_disconnect_grace_seconds
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if config.overlay_enabled and not overlay.start():
            config.overlay_enabled = False
            config.save(app_root)
        yield
        browser_connections.close()
        audio.stop()
        overlay.stop()

    app = FastAPI(title="Qwen Realtime Interview Copilot", lifespan=lifespan)
    app.state.engine, app.state.audio, app.state.overlay = engine, audio, overlay
    app.state.browser_connections = browser_connections
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.middleware("http")
    async def disable_ui_cache(request, call_next):
        response = await call_next(request)
        if request.url.path == "/" or request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/status")
    def status():
        session_data = None
        if engine.session:
            session_data = engine.session.to_dict()
            session_data["jd_text"] = (engine.session.path / "jd.md").read_text(encoding="utf-8")
            session_data["candidate_facts"] = (engine.session.path / "candidate-facts.md").read_text(encoding="utf-8")
            session_data["externalSources"] = [
                {"label": item.get("label", ""), "url": item.get("url", "")}
                for item in engine.sessions.external_sources(engine.session.id)
            ]
        api_key = os.getenv("DASHSCOPE_API_KEY", "")
        return {
            "aliyunConfigured": bool(api_key),
            "aliyunKeyMasked": _mask_api_key(api_key),
            "running": engine.active,
            "session": session_data,
            "answerSnapshot": engine.answer_snapshot(),
            "realtimeSettings": {
                "pipelineMode": config.qwen_pipeline_mode,
                "model": config.qwen_realtime_model,
                "asrModel": config.qwen_asr_model,
                "answerModel": config.qwen_answer_model,
                "workspaceId": config.qwen_realtime_workspace_id,
                "turnDetection": config.qwen_realtime_turn_detection,
            },
            "loopbackDevices": [item.__dict__ for item in list_loopback_devices()],
            "selectedLoopbackDeviceName": config.device_name,
            "microphoneDevices": [item.__dict__ for item in list_input_devices()],
            "selectedMicrophoneDeviceId": config.microphone_name or default_input_device_id(),
            "microphoneEnabled": config.microphone_enabled,
            "localKnowledge": {
                "packs": engine.context_provider.available_packs(),
                "enabled": bool(engine.context_provider.available_packs()),
            },
            "overlay": {
                "enabled": config.overlay_enabled,
                **overlay.state(),
                "settings": overlay_settings(),
            },
        }

    def overlay_settings() -> dict:
        return {
            "opacity": config.overlay_opacity,
            "backgroundOpacity": config.overlay_background_opacity,
            "textOpacity": config.overlay_text_opacity,
            "fontSize": config.overlay_font_size,
            "historyFontSize": config.overlay_history_font_size,
            "textColor": config.overlay_text_color,
            "historyColor": config.overlay_history_color,
            "showQuestion": config.overlay_show_question,
            "showHistory": config.overlay_show_history,
            "historyCount": config.overlay_history_count,
            "highlightProgress": config.overlay_highlight_progress,
            "autoMicrophone": config.overlay_auto_microphone,
            "sizePreset": config.overlay_size_preset,
        }

    @app.get("/api/overlay/data")
    def overlay_data():
        return {
            "snapshot": engine.answer_snapshot(),
            "settings": overlay_settings(),
            "geometry": {
                "x": config.overlay_x,
                "y": config.overlay_y,
                "width": config.overlay_width,
                "height": config.overlay_height,
            },
        }

    @app.post("/api/overlay/geometry")
    def save_overlay_geometry(payload: OverlayGeometryPayload):
        config.overlay_x = payload.x
        config.overlay_y = payload.y
        config.overlay_width = max(360, min(1800, payload.width))
        config.overlay_height = max(180, min(1200, payload.height))
        config.overlay_size_preset = "custom"
        config.save(app_root)
        return {"ok": True}

    @app.post("/api/overlay/runtime")
    def update_overlay_runtime(payload: OverlayRuntimePayload):
        overlay.update_runtime(payload.hidden, payload.frozen, payload.locked, payload.hotkeys)
        return {"ok": True}

    @app.post("/api/overlay/start")
    def start_overlay():
        config.overlay_enabled = True
        config.save(app_root)
        if not overlay.start():
            config.overlay_enabled = False
            config.save(app_root)
            raise HTTPException(500, f"悬浮回答台启动失败：{overlay.last_error or '窗口未能启动'}")
        return {"enabled": True, **overlay.state()}

    @app.post("/api/overlay/stop")
    def stop_overlay():
        config.overlay_enabled = False
        config.save(app_root)
        overlay.stop()
        return {"enabled": False, **overlay.state()}

    @app.post("/api/overlay/show")
    def show_overlay():
        if not overlay.running:
            raise HTTPException(409, "悬浮回答台尚未启用")
        overlay.show()
        return overlay.state()

    @app.post("/api/overlay/freeze")
    def freeze_overlay():
        if not overlay.running:
            raise HTTPException(409, "悬浮回答台尚未启用")
        overlay.toggle_freeze()
        return overlay.state()

    @app.post("/api/overlay/lock")
    def lock_overlay():
        if not overlay.running:
            raise HTTPException(409, "悬浮回答台尚未启用")
        overlay.set_locked(True)
        return overlay.state()

    @app.post("/api/overlay/unlock")
    def unlock_overlay():
        if not overlay.running:
            raise HTTPException(409, "悬浮回答台尚未启用")
        overlay.set_locked(False)
        return overlay.state()

    @app.post("/api/settings/overlay")
    def save_overlay_settings(payload: OverlaySettingsPayload):
        if not 0.05 <= payload.background_opacity <= 1.0:
            raise HTTPException(400, "背景透明度必须在 5% 到 100% 之间")
        if not 0.15 <= payload.text_opacity <= 1.0:
            raise HTTPException(400, "文字透明度必须在 15% 到 100% 之间")
        if not 8 <= payload.font_size <= 96:
            raise HTTPException(400, "当前答案字号必须在 8 到 96 之间")
        if not 6 <= payload.history_font_size <= 64:
            raise HTTPException(400, "历史回答字号必须在 6 到 64 之间")
        if payload.history_count not in {1, 2, 3}:
            raise HTTPException(400, "历史问答数量只能是 1、2 或 3")
        if payload.size_preset not in {"compact", "standard", "wide", "custom"}:
            raise HTTPException(400, "不支持的悬浮层尺寸")
        for color in (payload.text_color, payload.history_color):
            if len(color) != 7 or color[0] != "#" or any(
                character not in "0123456789abcdefABCDEF" for character in color[1:]
            ):
                raise HTTPException(400, "文字颜色格式不正确")
        preset_changed = payload.size_preset != config.overlay_size_preset and payload.size_preset != "custom"
        config.overlay_opacity = payload.opacity
        config.overlay_background_opacity = payload.background_opacity
        config.overlay_text_opacity = payload.text_opacity
        config.overlay_font_size = payload.font_size
        config.overlay_history_font_size = payload.history_font_size
        config.overlay_text_color = payload.text_color
        config.overlay_history_color = payload.history_color
        config.overlay_show_question = payload.show_question
        config.overlay_show_history = payload.show_history
        config.overlay_history_count = payload.history_count
        config.overlay_highlight_progress = payload.highlight_progress
        config.overlay_auto_microphone = payload.auto_microphone
        config.overlay_size_preset = payload.size_preset
        if preset_changed:
            config.overlay_width = 0
            config.overlay_height = 0
            config.overlay_x = None
            config.overlay_y = None
        config.save(app_root)
        if overlay.running and preset_changed:
            overlay.stop()
            overlay.start()
        else:
            overlay.settings_changed()
        saved_settings = overlay_settings()
        bus.publish({"type": "overlay_settings", "settings": saved_settings})
        return saved_settings

    @app.post("/api/session/prepare")
    async def prepare_session(
        company: str = Form(""),
        position: str = Form(""),
        jd_text: str = Form(""),
        knowledge_packs: str = Form("[]"),
        resume: UploadFile | None = File(None),
    ):
        temp_path: Path | None = None
        try:
            if resume and resume.filename:
                suffix = Path(resume.filename).suffix.lower()
                if suffix not in {".pdf", ".md", ".txt"}:
                    raise HTTPException(400, "简历仅支持 PDF、Markdown 或 TXT")
                with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as handle:
                    handle.write(await resume.read())
                    temp_path = Path(handle.name)
            try:
                requested_packs = json.loads(knowledge_packs)
            except json.JSONDecodeError as exc:
                raise HTTPException(400, "知识库选择格式不正确") from exc
            available_packs = set(engine.context_provider.available_packs())
            if not isinstance(requested_packs, list) or any(
                not isinstance(item, str) or item not in available_packs
                for item in requested_packs
            ):
                raise HTTPException(400, "包含不存在的本地知识库")
            session, facts = engine.sessions.create(
                company=company,
                position=position,
                jd_text=jd_text,
                resume_path=temp_path,
                knowledge_packs=requested_packs,
            )
            engine.set_session(session)
            bus.publish({"type": "session_ready", "session": session.to_dict()})
            return {"session": session.to_dict(), "candidateFacts": facts}
        finally:
            if temp_path:
                temp_path.unlink(missing_ok=True)

    @app.post("/api/session/{session_id}/facts")
    def update_facts(session_id: str, payload: FactsPayload):
        if not engine.session or engine.session.id != session_id:
            raise HTTPException(404, "当前会话不存在")
        engine.sessions.update_candidate_facts(session_id, payload.text)
        return {"ok": True}

    @app.post("/api/session/{session_id}/knowledge-packs")
    def update_knowledge_packs(session_id: str, payload: KnowledgePacksPayload):
        if not engine.session or engine.session.id != session_id:
            raise HTTPException(404, "当前会话不存在")
        available = set(engine.context_provider.available_packs())
        if any(pack not in available for pack in payload.packs):
            raise HTTPException(400, "包含不存在的本地知识库")
        engine.sessions.update_knowledge_packs(engine.session, payload.packs)
        return {"packs": engine.session.knowledge_packs, "requiresRestart": engine.active}

    @app.post("/api/session/{session_id}/external-sources")
    def add_external_source(session_id: str, payload: ExternalSourcePayload):
        if not engine.session or engine.session.id != session_id:
            raise HTTPException(404, "当前会话不存在")
        try:
            source = import_github_source(payload.url, payload.label)
            sources = engine.sessions.add_external_source(session_id, source)
        except Exception as exc:
            raise HTTPException(400, f"外部资料导入失败：{exc}") from exc
        visible = [{"label": item.get("label", ""), "url": item.get("url", "")} for item in sources]
        return {"sources": visible, "requiresRestart": engine.active}

    @app.post("/api/interview/start")
    def start_interview(payload: StartPayload):
        if not engine.session:
            raise HTTPException(400, "请先准备本次面试")
        if not os.getenv("DASHSCOPE_API_KEY"):
            raise HTTPException(400, "请先配置阿里云百炼 API Key")
        devices = list_loopback_devices()
        selected = payload.loopback_device_name.strip() or config.device_name
        if selected and selected not in {item.name for item in devices}:
            raise HTTPException(400, "选择的系统声音设备已不存在，请重新选择")
        measured = probe_loopback_levels(duration_seconds=0.8)
        if measured:
            best = max(measured, key=lambda item: item["rms"])
            selected_rms = next(
                (item["rms"] for item in measured if item["name"] == selected), 0.0
            )
            if best["rms"] >= 0.0005 and best["rms"] > max(selected_rms * 1.8, selected_rms + 0.0002):
                selected = best["name"]
                bus.publish(
                    {
                        "type": "audio_device_selected",
                        "name": selected,
                        "message": f"已自动切换到检测到会议声音的设备：{selected}",
                    }
                )
        try:
            microphone_ids = {item.id for item in list_input_devices()}
            microphone_id = payload.microphone_device_id.strip() or config.microphone_name or default_input_device_id()
            microphone_enabled = payload.microphone_enabled or (
                config.overlay_enabled and config.overlay_auto_microphone
            )
            if microphone_enabled and microphone_id not in microphone_ids:
                raise HTTPException(400, "选择的本地麦克风已不存在，请重新选择")
            audio.start(
                microphone_enabled=microphone_enabled,
                microphone_device_id=microphone_id,
                loopback_device_name=selected,
                fast_instructions=engine.instructions(),
                asr_context=engine.asr_context(),
                asr_vocabulary=engine.asr_vocabulary(),
            )
        except Exception as exc:
            audio.stop()
            raise HTTPException(500, f"Qwen 实时通道启动失败：{exc}") from exc
        engine.active = True
        if config.overlay_enabled and not overlay.running:
            overlay.start()
        if selected:
            config.device_name = selected
        config.microphone_enabled = microphone_enabled
        config.microphone_name = microphone_id
        config.save(app_root)
        bus.publish({"type": "interview_state", "running": True})
        return {
            "ok": True,
            "loopbackDeviceName": selected,
            "microphoneDeviceId": microphone_id,
            "microphoneEnabled": microphone_enabled,
        }

    @app.get("/api/audio/probe")
    def probe_audio():
        if engine.active:
            raise HTTPException(409, "请先停止实时会话，再检测设备")
        return {"devices": probe_loopback_levels(duration_seconds=1.2)}

    @app.post("/api/interview/stop")
    def stop_interview():
        engine.active = False
        audio.stop()
        bus.publish({"type": "interview_state", "running": False})
        return {"ok": True}

    @app.post("/api/settings/realtime")
    def select_realtime_settings(payload: RealtimeSettingsPayload):
        if engine.active:
            raise HTTPException(409, "请先停止实时会话，再修改 Qwen 设置")
        if payload.pipeline_mode not in {"dual", "realtime"}:
            raise HTTPException(400, "不支持的 Qwen 处理模式")
        if payload.model not in {"qwen-audio-3.0-realtime-flash", "qwen-audio-3.0-realtime-plus"}:
            raise HTTPException(400, "不支持的 Qwen 实时模型")
        if payload.turn_detection not in {"smart_turn", "server_vad"}:
            raise HTTPException(400, "不支持的轮次检测方式")
        if payload.answer_model not in {"qwen-plus", "qwen-flash"}:
            raise HTTPException(400, "不支持的 Qwen 文本回答模型")
        workspace_id = payload.workspace_id.strip()
        if workspace_id and not all(char.isalnum() or char in "-_" for char in workspace_id):
            raise HTTPException(400, "业务空间 ID 格式不正确")
        config.qwen_realtime_enabled = True
        config.qwen_pipeline_mode = payload.pipeline_mode
        config.qwen_realtime_model = payload.model
        config.qwen_answer_model = payload.answer_model
        engine.text_answerer.model = payload.answer_model
        engine.text_answerer.workspace_id = workspace_id
        config.qwen_realtime_workspace_id = workspace_id
        config.qwen_realtime_turn_detection = payload.turn_detection
        config.save(app_root)
        return {
            "pipelineMode": config.qwen_pipeline_mode,
            "model": config.qwen_realtime_model,
            "answerModel": config.qwen_answer_model,
            "workspaceId": workspace_id,
            "turnDetection": config.qwen_realtime_turn_detection,
        }

    @app.post("/api/settings/aliyun-key")
    def save_aliyun_key(payload: AliyunKeyPayload):
        if engine.active:
            raise HTTPException(409, "请先停止实时会话，再修改 API Key")
        api_key = payload.api_key.strip()
        if len(api_key) < 8:
            raise HTTPException(400, "API Key 格式过短，请检查后重新输入")
        if api_key != payload.confirm_api_key.strip():
            raise HTTPException(400, "两次输入的 API Key 不一致")
        config.aliyun_api_key = api_key
        config.save(app_root)
        os.environ["DASHSCOPE_API_KEY"] = api_key
        masked = _mask_api_key(api_key)
        bus.publish({"type": "aliyun_key_updated", "masked": masked})
        return {"configured": True, "masked": masked}

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        await websocket.accept()
        browser_connections.connected()
        events = bus.subscribe()
        try:
            while True:
                try:
                    await asyncio.wait_for(websocket.receive_text(), timeout=0.05)
                except TimeoutError:
                    pass
                while True:
                    try:
                        event = events.get_nowait()
                    except queue.Empty:
                        break
                    await websocket.send_json(event)
        except (WebSocketDisconnect, RuntimeError, asyncio.CancelledError):
            pass
        finally:
            bus.unsubscribe(events)
            browser_connections.disconnected()

    return app
