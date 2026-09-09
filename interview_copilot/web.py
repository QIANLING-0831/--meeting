from __future__ import annotations

import asyncio
import json
import os
import queue
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .answering import ANSWER_INSTRUCTIONS, InterviewEngine
from .audio import default_input_device_id, list_input_devices, list_loopback_devices
from .config import AppConfig
from .event_bus import EventBus
from .streaming_audio import StreamingAudioCoordinator


ROOT = Path(__file__).resolve().parent.parent
STATIC = Path(__file__).resolve().parent / "static"


class FactsPayload(BaseModel):
    text: str


class StartPayload(BaseModel):
    microphone_enabled: bool = False
    microphone_device_id: str = ""


class TranscriptPayload(BaseModel):
    speaker: str
    text: str
    final: bool = True


class QuestionPayload(BaseModel):
    question: str


class ModelPayload(BaseModel):
    model: str
    effort: str


class AnswerSettingsPayload(BaseModel):
    include_core_points: bool = False


def create_app(root: Path | None = None) -> FastAPI:
    app_root = (root or ROOT).resolve()
    config = AppConfig.load(app_root)
    bus = EventBus()
    engine = InterviewEngine(app_root, config, bus)
    latest_session = engine.sessions.latest()
    if latest_session:
        engine.set_session(latest_session)
    audio = StreamingAudioCoordinator(
        config,
        engine.ingest_transcript,
        lambda speaker, level: bus.publish(
            {"type": "audio_level", "speaker": speaker, "level": level}
        ),
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        audio.stop()
        engine.codex.close()

    app = FastAPI(title="Interview Copilot", lifespan=lifespan)
    app.state.engine = engine
    app.state.audio = audio
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

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
        return {
            "paraformerConfigured": bool(os.getenv("DASHSCOPE_API_KEY")),
            "running": engine.active,
            "session": session_data,
            "answerSettings": {"includeCorePoints": config.include_core_points},
            "answerSnapshot": engine.answer_snapshot(),
            "loopbackDevices": [item.__dict__ for item in list_loopback_devices()],
            "microphoneDevices": [item.__dict__ for item in list_input_devices()],
            "defaultMicrophoneDeviceId": default_input_device_id(),
            "selectedMicrophoneDeviceId": config.microphone_name,
        }

    @app.get("/api/knowledge/packs")
    def packs():
        return {"packs": engine.knowledge.list_packs()}

    @app.post("/api/knowledge/reindex")
    def reindex():
        result = engine.knowledge.rebuild()
        bus.publish({"type": "notice", "message": f"知识库索引完成：{result['files']} 个文件，{result['chunks']} 个片段"})
        return result

    @app.post("/api/session/prepare")
    async def prepare_session(
        company: str = Form(""),
        position: str = Form(""),
        jd_text: str = Form(""),
        knowledge_packs: str = Form("[]"),
        resume: UploadFile | None = File(None),
    ):
        try:
            selected = json.loads(knowledge_packs)
            if not isinstance(selected, list):
                raise ValueError
        except (json.JSONDecodeError, ValueError) as exc:
            raise HTTPException(400, "知识库选择格式错误") from exc

        temp_path: Path | None = None
        try:
            if resume and resume.filename:
                suffix = Path(resume.filename).suffix.lower()
                if suffix not in {".pdf", ".md", ".txt"}:
                    raise HTTPException(400, "简历仅支持 PDF、Markdown 或 TXT")
                with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as handle:
                    handle.write(await resume.read())
                    temp_path = Path(handle.name)
            session, facts = engine.sessions.create(
                company=company,
                position=position,
                jd_text=jd_text,
                resume_path=temp_path,
                knowledge_packs=[str(item) for item in selected],
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

    @app.post("/api/interview/start")
    def start_interview(payload: StartPayload):
        if not engine.session:
            raise HTTPException(400, "请先准备本次面试")
        if not os.getenv("DASHSCOPE_API_KEY"):
            raise HTTPException(400, "未检测到 DASHSCOPE_API_KEY")
        try:
            audio.start(payload.microphone_enabled, payload.microphone_device_id)
            if payload.microphone_enabled and payload.microphone_device_id:
                config.microphone_name = payload.microphone_device_id
                config.save(app_root)
        except Exception as exc:
            raise HTTPException(500, f"音频启动失败：{exc}") from exc
        engine.active = True
        bus.publish({"type": "interview_state", "running": True})
        return {"ok": True}

    @app.post("/api/interview/stop")
    def stop_interview():
        engine.active = False
        audio.stop()
        bus.publish({"type": "interview_state", "running": False})
        return {"ok": True}

    @app.post("/api/transcript/simulate")
    def simulate_transcript(payload: TranscriptPayload):
        if payload.speaker not in {"interviewer", "candidate"}:
            raise HTTPException(400, "speaker 必须是 interviewer 或 candidate")
        engine.ingest_transcript(payload.speaker, payload.text, payload.final)
        return {"ok": True}

    @app.post("/api/questions/answer")
    def answer(payload: QuestionPayload):
        if not payload.question.strip():
            raise HTTPException(400, "问题不能为空")
        engine.answer(payload.question)
        return {"ok": True}

    @app.post("/api/questions/interrupt")
    def interrupt():
        try:
            engine.codex.interrupt()
        except Exception as exc:
            raise HTTPException(500, str(exc)) from exc
        return {"ok": True}

    @app.get("/api/codex/account")
    def codex_account():
        try:
            return engine.codex.account()
        except Exception as exc:
            raise HTTPException(503, f"Codex 状态读取失败：{exc}") from exc

    @app.get("/api/codex/models")
    def codex_models():
        try:
            return {
                "models": engine.codex.models(),
                "selectedModel": config.codex_model,
                "selectedEffort": config.codex_reasoning_effort,
            }
        except Exception as exc:
            raise HTTPException(503, f"Codex 模型读取失败：{exc}") from exc

    @app.post("/api/codex/warmup")
    def codex_warmup():
        try:
            engine.codex.ensure_thread(
                model=config.codex_model,
                instructions=ANSWER_INSTRUCTIONS,
            )
            return {"ready": True}
        except Exception as exc:
            raise HTTPException(503, f"Codex 预热失败：{exc}") from exc

    @app.post("/api/settings/model")
    def select_model(payload: ModelPayload):
        try:
            models = engine.codex.models()
        except Exception as exc:
            raise HTTPException(503, f"Codex 模型读取失败：{exc}") from exc
        selected = next((item for item in models if item.get("model") == payload.model), None)
        if not selected:
            raise HTTPException(400, "该模型不在当前 Codex 账户的可用列表中")
        efforts = {
            item.get("reasoningEffort")
            for item in selected.get("supportedReasoningEfforts", [])
        }
        if payload.effort not in efforts:
            raise HTTPException(400, "该模型不支持所选推理强度")
        config.codex_model = payload.model
        config.codex_reasoning_effort = payload.effort
        config.save(app_root)
        bus.publish({"type": "notice", "message": "回答模型已更新，从下一题开始生效"})
        return {"model": payload.model, "effort": payload.effort}

    @app.post("/api/settings/answer")
    def select_answer_settings(payload: AnswerSettingsPayload):
        config.include_core_points = payload.include_core_points
        config.save(app_root)
        return {"includeCorePoints": config.include_core_points}

    @app.post("/api/codex/login")
    def codex_login():
        try:
            return engine.codex.begin_chatgpt_login()
        except Exception as exc:
            raise HTTPException(503, f"无法开始 Codex 登录：{exc}") from exc

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        await websocket.accept()
        events = bus.subscribe()
        try:
            while True:
                try:
                    await asyncio.wait_for(websocket.receive_text(), timeout=0.1)
                except TimeoutError:
                    pass
                try:
                    event = events.get_nowait()
                except queue.Empty:
                    continue
                await websocket.send_json(event)
        except (WebSocketDisconnect, RuntimeError, asyncio.CancelledError):
            pass
        finally:
            bus.unsubscribe(events)

    return app
