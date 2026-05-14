from __future__ import annotations

from rnaseq_workflow.core.assets import (
    AssetWorkspace,
    cleanup_allowed_targets,
    cleanup_plan,
    reference_search_dirs,
)


def test_asset_workspace_paths(tmp_path):
    workspace = AssetWorkspace(tmp_path / "workspace")
    user = workspace.ensure_user("user-1")
    task = user.task("task-1")

    assert workspace.global_reference_dir == tmp_path / "workspace" / "shared" / "references"
    assert user.user_reference_dir == tmp_path / "workspace" / "users" / "user-1" / "references"
    assert task.task_output_dir == tmp_path / "workspace" / "users" / "user-1" / "tasks" / "task-1"
    assert task.downloads_dir == task.root / "downloads"


def test_task_metadata_roundtrip(tmp_path):
    task = AssetWorkspace(tmp_path / "workspace").ensure_user("user-1").create_task(
        task_name="demo",
        description="test task",
    )

    metadata = task.read_metadata()

    assert metadata is not None
    assert metadata.user_id == "user-1"
    assert metadata.task_id == task.task_id
    assert metadata.task_name == "demo"
    assert metadata.description == "test task"
    assert task.metadata_path.exists()

    updated = task.update_metadata(task_name="renamed", description="new")
    assert updated.task_name == "renamed"
    assert task.read_metadata().description == "new"


def test_user_workspace_delete_task_removes_task_dir(tmp_path):
    user = AssetWorkspace(tmp_path / "workspace").ensure_user("user-1")
    task = user.create_task(task_name="demo")

    user.delete_task(task.task_id)

    assert not task.root.exists()


def test_reference_search_dirs_user_then_global(tmp_path):
    workspace = AssetWorkspace(tmp_path / "workspace")

    dirs = reference_search_dirs(workspace, "user-1")

    assert dirs == [
        tmp_path / "workspace" / "users" / "user-1" / "references",
        tmp_path / "workspace" / "shared" / "references",
    ]


def test_cleanup_plan_only_allows_known_root_children(tmp_path):
    (tmp_path / "downloads").mkdir()
    (tmp_path / "downloads" / "a.txt").write_text("hello", encoding="utf-8")
    (tmp_path / "workflow").mkdir()

    targets = cleanup_plan(tmp_path, names=("downloads", "workflow"))

    assert targets[0].allowed
    assert targets[0].file_count == 1
    assert targets[0].size_bytes == 5
    assert not targets[1].allowed


def test_cleanup_allowed_targets_dry_run_does_not_delete(tmp_path):
    (tmp_path / "runtime_logs").mkdir()
    (tmp_path / "runtime_logs" / "a.txt").write_text("hello", encoding="utf-8")

    targets = cleanup_allowed_targets(tmp_path, dry_run=True)

    assert (tmp_path / "runtime_logs").exists()
    assert any(target.path.name == "runtime_logs" and target.exists for target in targets)
