import sys
import types
import unittest
import wave
from pathlib import Path
from unittest.mock import Mock

import numpy as np


class WhisperDeviceTest(unittest.TestCase):
    def test_transcriber_forces_cpu_to_avoid_partial_cuda_installations(self):
        whisper_model = Mock()
        fake_module = types.SimpleNamespace(WhisperModel=whisper_model)
        previous = sys.modules.get("faster_whisper")
        sys.modules["faster_whisper"] = fake_module
        try:
            from interview_copilot.transcriber import WhisperTranscriber

            WhisperTranscriber("base", cpu_threads=2)
        finally:
            if previous is None:
                sys.modules.pop("faster_whisper", None)
            else:
                sys.modules["faster_whisper"] = previous

        whisper_model.assert_called_once_with(
            "base", device="cpu", compute_type="int8", cpu_threads=2
        )


class AliyunParaformerTest(unittest.TestCase):
    def test_requires_api_key(self):
        from unittest.mock import patch

        from interview_copilot.transcriber import AliyunParaformerTranscriber

        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "DASHSCOPE_API_KEY"):
                AliyunParaformerTranscriber()

    def test_sends_16khz_mono_pcm_wav_and_returns_text(self):
        recognition_class = Mock()
        recognition = recognition_class.return_value
        result = recognition.call.return_value
        result.status_code = 200
        result.get_sentence.return_value = [{"text": "你好，"}, {"text": "世界。"}]
        inspected_path = None

        def inspect_wav(path):
            nonlocal inspected_path
            inspected_path = Path(path)
            with wave.open(path, "rb") as wav_file:
                self.assertEqual(wav_file.getnchannels(), 1)
                self.assertEqual(wav_file.getsampwidth(), 2)
                self.assertEqual(wav_file.getframerate(), 16_000)
                self.assertEqual(wav_file.getnframes(), 16_000)
            return result

        recognition.call.side_effect = inspect_wav
        fake_dashscope = types.ModuleType("dashscope")
        fake_audio = types.ModuleType("dashscope.audio")
        fake_asr = types.ModuleType("dashscope.audio.asr")
        fake_asr.Recognition = recognition_class

        previous_modules = {
            name: sys.modules.get(name)
            for name in ("dashscope", "dashscope.audio", "dashscope.audio.asr")
        }
        sys.modules.update(
            {
                "dashscope": fake_dashscope,
                "dashscope.audio": fake_audio,
                "dashscope.audio.asr": fake_asr,
            }
        )
        try:
            from interview_copilot.transcriber import AliyunParaformerTranscriber

            transcriber = AliyunParaformerTranscriber(api_key="test-key")
            text = transcriber.transcribe(np.zeros(48_000, dtype=np.float32), 48_000)
        finally:
            for name, module in previous_modules.items():
                if module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module

        self.assertEqual(text, "你好，世界。")
        self.assertFalse(inspected_path.exists())
        self.assertEqual(fake_dashscope.api_key, "test-key")
        recognition_class.assert_called_once_with(
            model="paraformer-realtime-v2",
            format="wav",
            sample_rate=16_000,
            language_hints=["zh", "en"],
            callback=None,
        )


if __name__ == "__main__":
    unittest.main()
