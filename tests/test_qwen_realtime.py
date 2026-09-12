import json

from interview_copilot.config import AppConfig
from interview_copilot.qwen_realtime import AliyunRealtimeAnswerStream
from interview_copilot.streaming_audio import StreamingAudioCoordinator


class FakeSocket:
    def __init__(self):
        self.messages = []

    def send(self, message):
        self.messages.append(json.loads(message))


def build_stream(events, **options):
    return AliyunRealtimeAnswerStream(
        events.append,
        api_key="sk-test-12345678",
        instructions="只回答面试问题",
        **options,
    )


def test_session_is_text_only_and_uses_smart_turn():
    events = []
    stream = build_stream(events)
    socket = FakeSocket()

    stream._on_open(socket)

    session = socket.messages[0]["session"]
    assert session["modalities"] == ["text"]
    assert session["turn_detection"] == {"type": "smart_turn"}
    assert session["instructions"] == "只回答面试问题"
    assert session["max_history_turns"] == 8


def test_workspace_id_selects_dedicated_beijing_endpoint():
    stream = build_stream([], workspace_id="ws_123")

    assert stream.url == (
        "wss://ws_123.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime"
        "?model=qwen-audio-3.0-realtime-flash"
    )


def test_text_events_are_mapped_to_fast_channel_events():
    events = []
    stream = build_stream(events)

    stream._on_message(None, json.dumps({"type": "session.updated"}))
    stream._on_message(
        None,
        json.dumps(
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "item_id": "question-1",
                "transcript": "什么是 RAG？",
            }
        ),
    )
    stream._on_message(
        None,
        json.dumps({"type": "response.created", "response": {"id": "response-1"}}),
    )
    stream._on_message(
        None,
        json.dumps(
            {
                "type": "response.text.delta",
                "response_id": "response-1",
                "delta": "RAG 是",
            }
        ),
    )
    stream._on_message(
        None,
        json.dumps(
            {
                "type": "response.done",
                "response": {"id": "response-1", "status": "completed"},
            }
        ),
    )

    assert [event["type"] for event in events] == [
        "fast_channel_ready",
        "fast_question_transcript",
        "fast_answer_started",
        "fast_answer_delta",
        "fast_answer_completed",
    ]
    assert events[1]["text"] == "什么是 RAG？"
    assert events[3]["delta"] == "RAG 是"


def test_qwen_only_coordinator_always_creates_qwen_connection(monkeypatch):
    created = []

    class FakeStream:
        def __init__(self, *_args, **options):
            created.append(options)

        def start(self):
            return None

    coordinator = StreamingAudioCoordinator(AppConfig(qwen_realtime_enabled=False), on_fast_event=lambda _event: None)
    started_channels = []
    monkeypatch.setattr(
        coordinator,
        "_start_channel",
        lambda speaker, *_args: started_channels.append(speaker),
    )
    monkeypatch.setattr(
        "interview_copilot.streaming_audio.AliyunRealtimeAnswerStream", FakeStream,
    )

    coordinator.start()

    assert coordinator.fast_stream is not None
    assert started_channels == ["interviewer"]
    assert created


def test_qwen_start_failure_aborts_audio_capture(monkeypatch):
    coordinator = StreamingAudioCoordinator(AppConfig(), on_fast_event=lambda _event: None)
    started_channels = []
    monkeypatch.setattr(
        coordinator,
        "_start_channel",
        lambda speaker, *_args: started_channels.append(speaker),
    )
    monkeypatch.setattr(
        "interview_copilot.streaming_audio.AliyunRealtimeAnswerStream",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("连接失败")),
    )

    import pytest

    with pytest.raises(RuntimeError, match="连接失败"):
        coordinator.start()
    assert started_channels == []
