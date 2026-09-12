from __future__ import annotations

import asyncio
import os
import queue
import tempfile
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .audio import list_loopback_devices, probe_loopback_levels
from .config import AppConfig
from .event_bus import EventBus
from .qwen_only import QwenOnlyEngine
from .streaming_audio import StreamingAudioCoordinator

ROOT = Path(__file__).resolve().parent.parent
STATIC = Path(__file__).resolve().parent / "static"


class FactsPayload(BaseModel):
    text: str


class StartPayload(BaseModel):
    loopback_device_name: str = ""


class RealtimeSettingsPayload(BaseModel):
    pipeline_mode: str = "realtime"
    model: str = "qwen-audio-3.0-realtime-plus"
    answer_model: str = "qwen-plus"
    workspace_id: str = ""
    turn_detection: str = "server_vad"


class AliyunKeyPayload(BaseModel):
    api_key: str
    confirm_api_key: str


def _mask_api_key(api_key: str) -> str:
    value = api_key.strip()
    return "****" if len(value) < 8 else f"{value[:3]}****{value[-4:]}"


class _BrowserConnectionTracker:
    """Stop Qwen and audio after the final browser page disconnects."""

    def __init__(self, engine, audio, bus: EventBus, grace_seconds: float) -> None:
        self.engine, self.audio, self.bus = engine, audio, bus
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
            if self._connections or not self.engine.active:
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
    )
    browser_connections = _BrowserConnectionTracker(
        engine, audio, bus, config.browser_disconnect_grace_seconds
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        browser_connections.close()
        audio.stop()

    app = FastAPI(title="Qwen Realtime Interview Copilot", lifespan=lifespan)
    app.state.engine, app.state.audio = engine, audio
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
        }

    @app.post("/api/session/prepare")
    async def prepare_session(company: str = Form(""), position: str = Form(""), jd_text: str = Form(""), resume: UploadFile | None = File(None)):
        temp_path: Path | None = None
        try:
            if resume and resume.filename:
                suffix = Path(resume.filename).suffix.lower()
                if suffix not in {".pdf", ".md", ".txt"}:
                    raise HTTPException(400, "简历仅支持 PDF、Markdown 或 TXT")
                with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as handle:
                    handle.write(await resume.read())
                    temp_path = Path(handle.name)
            session, facts = engine.sessions.create(company=company, position=position, jd_text=jd_text, resume_path=temp_path, knowledge_packs=[])
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
            audio.start(
                loopback_device_name=selected,
                fast_instructions=engine.instructions(),
                asr_context=engine.asr_context(),
                asr_vocabulary=engine.asr_vocabulary(),
            )
        except Exception as exc:
            audio.stop()
            raise HTTPException(500, f"Qwen 实时通道启动失败：{exc}") from exc
        engine.active = True
        if selected:
            config.device_name = selected
            config.save(app_root)
        bus.publish({"type": "interview_state", "running": True})
        return {"ok": True, "loopbackDeviceName": selected}

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
