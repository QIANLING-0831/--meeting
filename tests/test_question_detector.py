from interview_copilot.question_detector import QuestionDetector


def test_detects_common_interview_question():
    result = QuestionDetector().detect("请你介绍一下 Agent 的记忆机制？")
    assert result is not None
    assert result.confidence >= 0.7
    assert result.question_type == "concept"


def test_ignores_short_acknowledgement():
    assert QuestionDetector().detect("好的") is None


def test_classifies_project_question():
    assert QuestionDetector().classify("你在这个项目中负责什么？") == "project"


def test_detects_polite_question_ending_with_period():
    result = QuestionDetector().detect(
        "讲到LLM，可以说一下一个用户请求发送给大模型后会经历哪些步骤。"
    )
    assert result is not None
    assert "LLM" in result.text


def test_waits_when_polite_question_prompt_has_no_subject_yet():
    assert QuestionDetector().detect("你说一下") is None
