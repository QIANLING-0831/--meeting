from __future__ import annotations

import warnings
from dataclasses import dataclass
from threading import Event, Lock, Thread
from typing import Callable

import numpy as np
import soundcard as sc
import sounddevice as sd


@dataclass(frozen=True)
class AudioDevice:
    name: str
    id: str


class LoopbackAudioProcessor:
    """Stateful speech gain control for short WASAPI loopback blocks."""

    def __init__(
        self,
        *,
        silence_rms: float = 0.0008,
        target_rms: float = 0.03,
        max_gain: float = 10.0,
        gain_release: float = 0.85,
        peak_limit: float = 0.95,
        initial_noise_rms: float = 0.0003,
        noise_ratio: float = 1.5,
    ) -> None:
        self.silence_rms = silence_rms
        self.target_rms = target_rms
        self.max_gain = max_gain
        self.gain_release = gain_release
        self.peak_limit = peak_limit
        self.noise_rms = initial_noise_rms
        self.noise_ratio = noise_ratio
        self.gain: float | None = None

    def process(self, audio: np.ndarray) -> np.ndarray:
        samples = np.asarray(audio, dtype=np.float32)
        if samples.size == 0:
            return samples
        centered = samples - float(np.mean(samples))
        rms = float(np.sqrt(np.mean(np.square(centered))))
        if rms < 1e-7:
            return np.zeros_like(samples)

        # Never erase a non-digital signal. Quiet meeting audio can sit below a
        # fixed gate, especially with Bluetooth/virtual endpoints. Learn the
        # device noise floor and only amplify blocks sufficiently above it;
        # probable noise is passed through unchanged for server-side VAD.
        speech_threshold = max(1e-7, self.noise_rms * self.noise_ratio)
        if rms <= speech_threshold:
            self.noise_rms = 0.98 * self.noise_rms + 0.02 * rms
            return centered.astype(np.float32, copy=False)

        desired_gain = min(self.max_gain, max(0.5, self.target_rms / rms))
        if self.gain is None or desired_gain <= self.gain:
            # Reduce gain immediately when speech becomes louder to avoid clipping.
            self.gain = desired_gain
        else:
            # Raise gain progressively so pauses and background noise do not pump.
            self.gain += self.gain_release * (desired_gain - self.gain)

        processed = centered * self.gain
        peak = float(np.max(np.abs(processed)))
        if peak > self.peak_limit:
            processed *= self.peak_limit / peak
        return processed.astype(np.float32, copy=False)


def prepare_loopback_audio(
    audio: np.ndarray,
    *,
    speech_floor_rms: float = 0.001,
    target_rms: float = 0.03,
    max_gain: float = 3.0,
) -> np.ndarray:
    """Conservatively lift quiet system speech without boosting digital silence."""
    samples = np.asarray(audio, dtype=np.float32)
    if samples.size == 0:
        return samples
    rms = float(np.sqrt(np.mean(np.square(samples))))
    if rms < speech_floor_rms or rms >= target_rms:
        return samples
    gain = min(max_gain, target_rms / max(rms, speech_floor_rms))
    return np.clip(samples * gain, -1.0, 1.0).astype(np.float32)


def list_loopback_devices() -> list[AudioDevice]:
    devices = sc.all_microphones(include_loopback=True)
    loopbacks = [d for d in devices if getattr(d, "isloopback", False)]
    return [AudioDevice(name=d.name, id=str(d.id)) for d in loopbacks]


def probe_loopback_levels(
    *, duration_seconds: float = 1.0, sample_rate: int = 48_000
) -> list[dict]:
    """Measure all loopback endpoints concurrently without changing selection."""
    devices = list_loopback_devices()
    stop = Event()
    lock = Lock()
    peaks = {device.name: 0.0 for device in devices}

    def callback_for(name: str):
        def on_block(block: np.ndarray, _rate: int) -> None:
            rms = float(np.sqrt(np.mean(np.square(block)))) if block.size else 0.0
            with lock:
                peaks[name] = max(peaks[name], rms)

        return on_block

    threads = [
        Thread(
            target=capture_loopback,
            args=(stop, device.name, sample_rate, 0.04, callback_for(device.name)),
            daemon=True,
            name=f"probe-{index}",
        )
        for index, device in enumerate(devices)
    ]
    for thread in threads:
        thread.start()
    stop.wait(max(0.01, duration_seconds))
    stop.set()
    for thread in threads:
        thread.join(timeout=1.5)
    return [
        {"name": device.name, "id": device.id, "rms": peaks[device.name]}
        for device in devices
    ]


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
    retry_delay_seconds: float = 0.1,
    on_status: Callable[[str, str], None] | None = None,
) -> None:
    frames = max(1, int(sample_rate * block_seconds))
    consecutive_failures = 0
    with warnings.catch_warnings():
        # WASAPI can recover from an occasional dropped buffer. Soundcard emits
        # this warning for every gap, which otherwise floods the live transcript.
        warnings.filterwarnings(
            "ignore",
            message="data discontinuity in recording",
            category=sc.SoundcardRuntimeWarning,
        )
        while not stop.is_set():
            try:
                # Resolve the device again on every attempt. Windows invalidates
                # existing WASAPI handles when a meeting app or output route resets.
                device = _find_device(device_name)
                # SoundCard documents corrupted data on Windows/WASAPI when a
                # recorder is forced to a single channel. Capture the endpoint's
                # native channel layout and downmix ourselves instead.
                with device.recorder(
                    samplerate=sample_rate,
                    blocksize=frames * 4,
                ) as recorder:
                    if on_status:
                        on_status("connected", getattr(device, "name", device_name))
                    while not stop.is_set():
                        block = np.asarray(recorder.record(numframes=frames), dtype=np.float32)
                        consecutive_failures = 0
                        if block.ndim == 1:
                            mono = block
                        else:
                            mono = np.mean(block, axis=1, dtype=np.float32)
                        on_block(mono, sample_rate)
            except Exception as exc:
                if stop.is_set():
                    return
                if on_status:
                    on_status("reconnecting", str(exc))
                # Event.wait makes shutdown immediate while preventing a tight
                # reconnect loop if Windows or Paraformer is temporarily offline.
                # The first recovery is nearly immediate; a sustained outage
                # backs off to one attempt per second.
                consecutive_failures += 1
                delay = min(1.0, retry_delay_seconds * (2 ** min(consecutive_failures - 1, 4)))
                stop.wait(delay)


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
