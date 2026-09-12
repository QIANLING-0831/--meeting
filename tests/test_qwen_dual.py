import time
from http import HTTPStatus
from types import SimpleNamespace

import numpy as np

from interview_copilot.qwen_dual import QwenAsrStream, QwenTextAnswerer, extract_hotwords


def test_hotwords_include_resume_technical_terms_and_named_projects():
    words = extract_hotwords('负责 RAG 和向量数据库，项目名为“面试知识助手”，使用 FastAPI。')

    assert words["RAG"] == 5
    assert words["向量数据库"] == 5
    assert words["面试知识助手"] == 5
    assert words["FastAPI"] == 5


def test_asr_stream_passes_context_vocabulary_and_finality():
    calls = []
    texts = []

    class CallbackBase:
        pass

    class ResultClass:
        @staticmethod
        def is_sentence_end(sentence):
            return sentence["end"]

    class Recognition:
        def __init__(self, **options):
            self.options = options
            calls.append(self)

        def start(self, **options):
            self.start_options = options
            self.options["callback"].on_open()

        def send_audio_frame(self, frame):
            self.frame = frame

        def stop(self):
            self.stopped = True

    events = []
    stream = QwenAsrStream(
        lambda text, final: texts.append((text, final)),
        events.append,
        api_key="test-key",
        context="岗位是 Agent 工程师",
        vocabulary={"RAG": 5},
        recognition_class=Recognition,
        callback_base=CallbackBase,
        result_class=ResultClass,
    )
    stream.start()
    result = SimpleNamespace(get_sentence=lambda: {"text": "什么是 RAG？", "end": True})
    calls[0].options["callback"].on_event(result)
    stream.send(np.full(480, 0.1, dtype=np.float32), 48_000)
    stream.stop()

    assert calls[0].options["vocabulary"] == {"RAG": 5}
    assert calls[0].start_options["raw_input"]["context"][0]["content"][0]["text"] == "岗位是 Agent 工程师"
    assert len(calls[0].frame) == 320
    assert texts == [("什么是 RAG？", True)]
    assert events[0]["type"] == "asr_channel_ready"
    assert calls[0].stopped is True


def test_text_answerer_streams_incremental_qwen_answer():
    events = []

    def generation_call(**options):
        assert options["model"] == "qwen-plus"
        assert options["enable_thinking"] is False
        for text in ["先说结论。", "再举例。"]:
            yield SimpleNamespace(
                status_code=HTTPStatus.OK,
                output=SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content=text))]
                ),
            )

    answerer = QwenTextAnswerer(events.append, generation_call=generation_call)
    answerer.answer("怎么设计 RAG？", "只依据简历")
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and not any(
        item["type"] == "fast_answer_completed" for item in events
    ):
        time.sleep(0.01)

    assert [item.get("delta") for item in events if item["type"] == "fast_answer_delta"] == [
        "先说结论。",
        "再举例。",
    ]
    assert events[-1]["type"] == "fast_answer_completed"


def test_asr_stream_buffers_short_capture_blocks_into_100ms_packets():
    class CallbackBase:
        pass

    class ResultClass:
        @staticmethod
        def is_sentence_end(_sentence):
            return False

    class Recognition:
        def __init__(self, **_options):
            self.frames = []

        def start(self, **_options):
            return None

        def send_audio_frame(self, frame):
            self.frames.append(frame)

        def stop(self):
            return None

    stream = QwenAsrStream(
        lambda *_args: None,
        lambda _event: None,
        api_key="test-key",
        recognition_class=Recognition,
        callback_base=CallbackBase,
        result_class=ResultClass,
    )
    stream.start()
    for _ in range(3):
        stream.send(np.full(1_920, 0.01, dtype=np.float32), 48_000)

    assert [len(frame) for frame in stream._recognition.frames] == [3_200]

    stream.stop()
    assert [len(frame) for frame in stream._recognition.frames] == [3_200, 640]
