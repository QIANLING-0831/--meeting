from interview_copilot.question_assembler import QuestionAssembler


def test_assembles_short_continuation_from_recent_fragments():
    assembler = QuestionAssembler(window_seconds=6)

    assembler.add_interviewer("你说一下", is_question=False, observed_at=1)
    assembler.add_interviewer("C++的特性有", is_question=False, observed_at=3)
    result = assembler.add_interviewer("哪些？", is_question=True, observed_at=5)

    assert result == "你说一下C++的特性有哪些？"


def test_expired_fragment_is_not_merged():
    assembler = QuestionAssembler(window_seconds=6)

    assembler.add_interviewer("C++的特性有", is_question=False, observed_at=1)
    result = assembler.add_interviewer("哪些？", is_question=True, observed_at=8)

    assert result == "哪些？"


def test_complete_new_question_replaces_previous_context():
    assembler = QuestionAssembler(window_seconds=6)

    assembler.add_interviewer("项目有哪些难点？", is_question=True, observed_at=1)
    result = assembler.add_interviewer("为什么选择 C++？", is_question=True, observed_at=3)

    assert result == "为什么选择 C++？"
