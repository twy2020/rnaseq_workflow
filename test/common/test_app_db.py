from __future__ import annotations

from rnaseq_workflow.core.app_db import AppDatabase


def test_app_db_user_session_and_task_roundtrip(tmp_path):
    db = AppDatabase(tmp_path / "workspace" / "app.db")

    user = db.create_user("Alice", "secret", display_name="Alice A")
    assert user.username == "alice"
    assert db.authenticate("alice", "wrong") is None

    logged_in = db.authenticate("alice", "secret")
    assert logged_in is not None
    session_id = db.create_session(logged_in.user_id)
    session_user = db.get_session_user(session_id)
    assert session_user is not None
    assert session_user.user_id == user.user_id

    task = db.upsert_task(
        task_id="task-1",
        user_id=user.user_id,
        task_dir=tmp_path / "workspace" / "users" / user.user_id / "tasks" / "task-1",
        task_name="demo",
        description="test",
    )
    assert task.task_name == "demo"
    assert db.list_tasks(user.user_id)[0].task_id == "task-1"

    db.delete_task("task-1", user_id=user.user_id)
    assert db.list_tasks(user.user_id) == []

    db.logout(session_id)
    assert db.get_session_user(session_id) is None


def test_app_db_reference_roundtrip(tmp_path):
    db = AppDatabase(tmp_path / "workspace" / "app.db")
    ref = db.upsert_reference(
        reference_id="demo",
        reference_dir=tmp_path / "workspace" / "shared" / "references",
        provider="ensembl",
        annotation_provider="ensembl",
        species="demo species",
        build_status="completed",
        description="demo reference",
    )
    assert ref.reference_id == "demo"
    listed = db.list_references()
    assert [item.reference_id for item in listed] == ["demo"]
    loaded = db.get_reference("demo")
    assert loaded is not None
    assert loaded.provider == "ensembl"
    db.delete_reference("demo")
    assert db.list_references() == []
