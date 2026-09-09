from interview_copilot.session import SessionManager


def test_resume_is_sanitized_and_session_files_created(tmp_path):
    resume = tmp_path / "resume.txt"
    resume.write_text("张三 13812345678 zhang@example.com\n负责 Agent 项目", encoding="utf-8")
    manager = SessionManager(tmp_path / "sessions")
    session, facts = manager.create(
        company="示例公司",
        position="Agent 开发",
        jd_text="负责智能体应用",
        resume_path=resume,
        knowledge_packs=["agent"],
    )
    assert "13812345678" not in facts
    assert "zhang@example.com" not in facts
    assert (session.path / "candidate-facts.md").exists()
    assert (session.path / "interviewer-transcript.md").exists()


def test_rejects_path_traversal(tmp_path):
    manager = SessionManager(tmp_path / "sessions")
    try:
        manager.update_candidate_facts("../outside", "bad")
    except ValueError:
        pass
    else:
        raise AssertionError("path traversal should be rejected")


def test_latest_restores_saved_session(tmp_path):
    manager = SessionManager(tmp_path / "sessions")
    created, _ = manager.create(
        company="恢复测试",
        position="Agent 工程师",
        jd_text="JD",
        resume_path=None,
        knowledge_packs=["agent"],
    )
    restored = manager.latest()
    assert restored is not None
    assert restored.id == created.id
    assert restored.knowledge_packs == ["agent"]
