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
