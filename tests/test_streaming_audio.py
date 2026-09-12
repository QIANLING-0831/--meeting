from threading import Event

import numpy as np

from interview_copilot.config import AppConfig
from interview_copilot import streaming_audio


def test_system_audio_is_sent_only_to_qwen(monkeypatch):
    sent = []

    class FakeQwen:
        def __init__(self, on_event, **options):
            self.on_event = on_event
            self.options = options

        def start(self):
            self.on_event({"type": "fast_channel_ready"})

        def send(self, block, sample_rate):
            sent.append((block.copy(), sample_rate))

        def stop(self):
            return None

    def fake_capture(stop: Event, _device_name, sample_rate, _block_seconds, on_block, **_options):
        assert _block_seconds == 0.1
        on_block(np.full(4_800, 0.01, dtype=np.float32), sample_rate)
        stop.set()

    events = []
    monkeypatch.setattr(streaming_audio, "AliyunRealtimeAnswerStream", FakeQwen)
    monkeypatch.setattr(streaming_audio, "capture_loopback", fake_capture)
    config = AppConfig()
    config.qwen_pipeline_mode = "realtime"
    coordinator = streaming_audio.StreamingAudioCoordinator(config, on_fast_event=events.append)

    coordinator.start(loopback_device_name="test-loopback", fast_instructions="use resume")
    coordinator.channels[0].thread.join(timeout=2)
    coordinator.stop()

    assert events == [{"type": "fast_channel_ready"}]
    assert len(sent) == 1
    assert sent[0][1] == 48_000
    assert coordinator.channels == []


def test_dual_mode_sends_system_audio_to_dedicated_asr(monkeypatch):
    sent = []

    class FakeAsr:
        def __init__(self, on_text, on_event, **options):
            self.on_text = on_text
            self.options = options

        def start(self):
            self.on_text("请介绍 RAG 项目", True)

        def send(self, block, sample_rate):
            sent.append((block.copy(), sample_rate))

        def stop(self):
            return None

    def fake_capture(stop: Event, _device_name, sample_rate, _block_seconds, on_block, **_options):
        on_block(np.full(4_800, 0.01, dtype=np.float32), sample_rate)
        stop.set()

    texts = []
    monkeypatch.setattr(streaming_audio, "QwenAsrStream", FakeAsr)
    monkeypatch.setattr(streaming_audio, "capture_loopback", fake_capture)
    coordinator = streaming_audio.StreamingAudioCoordinator(
        AppConfig(qwen_pipeline_mode="dual"),
        on_text=lambda speaker, text, final: texts.append((speaker, text, final)),
        on_fast_event=lambda _event: None,
    )

    coordinator.start(asr_context="岗位上下文", asr_vocabulary={"RAG": 5})
    coordinator.channels[0].thread.join(timeout=2)
    coordinator.stop()

    assert texts == [("interviewer", "请介绍 RAG 项目", True)]
    assert len(sent) == 1
    assert np.allclose(sent[0][0], 0.01)


def test_microphone_uses_separate_asr_and_only_updates_candidate_context(monkeypatch):
    instances = []

    class FakeAsr:
        def __init__(self, on_text, on_event, **options):
            self.on_text = on_text
            self.sent = []
            instances.append(self)

        def start(self):
            return None

        def send(self, block, sample_rate):
            self.sent.append((block.copy(), sample_rate))

        def stop(self):
            return None

    class FakeRealtime:
        def __init__(self, on_event, **options):
            self.updated = []

        def start(self):
            return None

        def send(self, block, sample_rate):
            return None

        def update_instructions(self, value):
            self.updated.append(value)

        def stop(self):
            return None

    def fake_loopback(stop, _device, rate, _seconds, on_block, **_options):
        on_block(np.full(4_800, 0.01, dtype=np.float32), rate)

    def fake_microphone(stop, device, rate, _seconds, on_block):
        assert device == "7"
        on_block(np.full(4_800, 0.02, dtype=np.float32), rate)

    texts = []
    monkeypatch.setattr(streaming_audio, "QwenAsrStream", FakeAsr)
    monkeypatch.setattr(streaming_audio, "AliyunRealtimeAnswerStream", FakeRealtime)
    monkeypatch.setattr(streaming_audio, "capture_loopback", fake_loopback)
    monkeypatch.setattr(streaming_audio, "capture_microphone", fake_microphone)
    coordinator = streaming_audio.StreamingAudioCoordinator(
        AppConfig(qwen_pipeline_mode="realtime"),
        on_text=lambda speaker, text, final: texts.append((speaker, text, final)),
        on_fast_event=lambda _event: None,
        instructions_provider=lambda: "包含候选人最新回答的提示词",
    )

    coordinator.start(microphone_enabled=True, microphone_device_id="7")
    for channel in coordinator.channels:
        channel.thread.join(timeout=2)
    instances[0].on_text("我先做任务规划", True)

    assert texts == [("candidate", "我先做任务规划", True)]
    assert len(instances) == 1
    assert len(instances[0].sent) == 1
    assert coordinator.fast_stream.updated == ["包含候选人最新回答的提示词"]
    coordinator.stop()
