from __future__ import annotations

import warnings
from dataclasses import dataclass
from threading import Event
from typing import Callable

import numpy as np
import soundcard as sc
import sounddevice as sd


@dataclass(frozen=True)
class AudioDevice:
    name: str
    id: str


def list_loopback_devices() -> list[AudioDevice]:
    devices = sc.all_microphones(include_loopback=True)
    loopbacks = [d for d in devices if getattr(d, "isloopback", False)]
    return [AudioDevice(name=d.name, id=str(d.id)) for d in loopbacks]


def list_input_devices() -> list[AudioDevice]:
    default_id = int(sd.default.device[0])
    default_info = sd.query_devices(default_id, "input")
    default_host_api = default_info["hostapi"]
    devices = []
    for index, info in enumerate(sd.query_devices()):
        if info["max_input_channels"] <= 0 or info["hostapi"] != default_host_api:
            continue
        name = str(info["name"])
        if index == default_id:
            name += "（系统默认）"
        devices.append(AudioDevice(name=name, id=str(index)))
    return devices


def default_input_device_id() -> str:
    return str(int(sd.default.device[0]))


def _find_device(device_name: str):
    devices = sc.all_microphones(include_loopback=True)
    loopbacks = [d for d in devices if getattr(d, "isloopback", False)]
    if not loopbacks:
        raise RuntimeError("没有找到 WASAPI 回环设备，请确认 Windows 有可用的扬声器输出。")
    if device_name:
        for device in loopbacks:
            if device.name == device_name:
                return device
    default_speaker = sc.default_speaker()
    for device in loopbacks:
        if default_speaker and default_speaker.name in device.name:
            return device
    return loopbacks[0]


def capture_loopback(
    stop: Event,
    device_name: str,
    sample_rate: int,
    block_seconds: float,
    on_block: Callable[[np.ndarray, int], None],
) -> None:
    device = _find_device(device_name)
    frames = max(1, int(sample_rate * block_seconds))
    with warnings.catch_warnings():
        # WASAPI can recover from an occasional dropped buffer. Soundcard emits
        # this warning for every gap, which otherwise floods the live transcript.
        warnings.filterwarnings(
            "ignore",
            message="data discontinuity in recording",
            category=sc.SoundcardRuntimeWarning,
        )
        with device.recorder(samplerate=sample_rate, channels=1) as recorder:
            while not stop.is_set():
                block = recorder.record(numframes=frames)
                on_block(np.asarray(block[:, 0], dtype=np.float32), sample_rate)


def capture_microphone(
    stop: Event,
    device_name: str,
    sample_rate: int,
    block_seconds: float,
    on_block: Callable[[np.ndarray, int], None],
) -> None:
    device: int | str | None
    device = int(device_name) if device_name.isdigit() else (device_name or None)
    info = sd.query_devices(device, "input")
    native_rate = int(info["default_samplerate"])
    frames = max(1, int(native_rate * block_seconds))
    with sd.InputStream(
        device=device,
        samplerate=native_rate,
        channels=1,
        dtype="float32",
        blocksize=frames,
    ) as recorder:
        while not stop.is_set():
            block, _overflowed = recorder.read(frames)
            on_block(np.asarray(block[:, 0], dtype=np.float32), native_rate)
