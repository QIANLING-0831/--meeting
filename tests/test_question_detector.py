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
