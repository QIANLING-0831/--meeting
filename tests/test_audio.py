from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np

from interview_copilot import audio


def test_microphone_uses_portaudio_device_and_its_native_rate(monkeypatch):
    stop = Event()
    received = []

    class FakeInputStream:
        def __init__(self, **options):
            self.options = options
            fake_sd.last_options = options

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def read(self, frames):
            return np.full((frames, 1), 0.25, dtype=np.float32), False

    fake_sd = SimpleNamespace(
        default=SimpleNamespace(device=(4, 9)),
        query_devices=Mock(return_value={"name": "USB microphone", "default_samplerate": 44_100}),
        InputStream=FakeInputStream,
        last_options=None,
    )
    monkeypatch.setattr(audio, "sd", fake_sd)

    def on_block(block, rate):
        received.append((block, rate))
        stop.set()

    audio.capture_microphone(stop, "7", 48_000, 0.1, on_block)

    assert fake_sd.query_devices.call_args.args == (7, "input")
    assert fake_sd.last_options["device"] == 7
    assert fake_sd.last_options["samplerate"] == 44_100
    assert received[0][0].shape == (4_410,)
    assert received[0][1] == 44_100


def test_low_loopback_speech_is_boosted_without_amplifying_silence():
    low_speech = np.full(1_600, 0.01, dtype=np.float32)
    silence = np.zeros(1_600, dtype=np.float32)
    normal = np.full(1_600, 0.08, dtype=np.float32)

    boosted = audio.prepare_loopback_audio(low_speech)

    assert np.allclose(boosted, 0.03)
    assert np.array_equal(audio.prepare_loopback_audio(silence), silence)
    assert np.array_equal(audio.prepare_loopback_audio(normal), normal)


def test_stateful_loopback_processor_stabilizes_alternating_weak_speech():
    processor = audio.LoopbackAudioProcessor()
    phase = np.linspace(0, 20 * np.pi, 4_800, endpoint=False)
    blocks = [
        (np.sin(phase) * amplitude).astype(np.float32)
        for amplitude in (0.002, 0.03, 0.002, 0.03, 0.002)
    ]

    levels = [
        float(np.sqrt(np.mean(np.square(processor.process(block)))))
        for block in blocks
    ]

    assert min(levels) > 0.01
    assert max(levels) / min(levels) < 2.5
    assert np.array_equal(
        processor.process(np.zeros(4_800, dtype=np.float32)),
        np.zeros(4_800, dtype=np.float32),
    )


def test_loopback_reopens_after_windows_invalidates_the_audio_device(monkeypatch):
    stop = Event()
    received = []
    statuses = []
    attempts = 0

    class FakeRecorder:
        def __init__(self, should_fail):
            self.should_fail = should_fail

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def record(self, numframes):
            if self.should_fail:
                raise RuntimeError("Error 0x88890004")
            return np.full((numframes, 1), 0.25, dtype=np.float32)

    class FakeDevice:
        def __init__(self, should_fail):
            self.should_fail = should_fail

        def recorder(self, **_options):
            return FakeRecorder(self.should_fail)

    def find_device(_name):
        nonlocal attempts
        attempts += 1
        return FakeDevice(should_fail=attempts == 1)

    monkeypatch.setattr(audio, "_find_device", find_device)

    def on_block(block, rate):
        received.append((block, rate))
        stop.set()

    audio.capture_loopback(
        stop,
        "",
        16_000,
        0.1,
        on_block,
        retry_delay_seconds=0,
        on_status=lambda status, message: statuses.append((status, message)),
    )

    assert attempts == 2
    assert received[0][0].shape == (1_600,)
    assert received[0][1] == 16_000
    assert "reconnecting" in [status for status, _message in statuses]
    assert statuses[-1][0] == "connected"
