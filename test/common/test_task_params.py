from __future__ import annotations

from rnaseq_workflow.core.assets import AssetWorkspace
from rnaseq_workflow.core.task_params import TaskParams, default_task_params, read_task_params, validate_task_params, write_task_params


def test_default_task_params_use_task_paths(tmp_path):
    task = AssetWorkspace(tmp_path / "workspace").ensure_user("user-1").create_task()

    params = default_task_params(task)

    assert params.execution_mode == "sample_pipeline"
    assert params.cleanup_policy == "cleanup_after_task"
    assert params.downloads_dir == str(task.downloads_dir)
    assert params.output_dir == str(task.task_output_dir)
    assert params.reports_dir == str(task.reports_dir)
    assert params.docker_workspace == str(tmp_path / "workspace")
    assert params.resource_guard_enabled is True
    assert params.disk_guard_strategy == "cancel"
    assert params.spill_paths == []
    assert params.spill_large_outputs is True


def test_validate_task_params_reports_invalid_values():
    params = TaskParams(
        max_workers=0,
        download_workers=0,
        featurecounts_strandness=4,
        execution_mode="bad",
        disk_guard_min_free_gb=-1,
        disk_guard_min_free_percent=101,
        disk_guard_strategy="bad",
        expression_output_formats=[],
    )

    issues = validate_task_params(params)

    assert {issue.field for issue in issues} >= {
        "max_workers",
        "download_workers",
        "featurecounts_strandness",
        "execution_mode",
        "disk_guard_min_free_gb",
        "disk_guard_min_free_percent",
        "disk_guard_strategy",
        "expression_output_formats",
    }


def test_task_params_roundtrip(tmp_path):
    path = tmp_path / "params.json"
    params = TaskParams(
        max_workers=3,
        download_workers=1,
        download_proxy="http://127.0.0.1:7890",
        disk_guard_strategy="transfer",
        spill_paths=[str(tmp_path / "spill-a"), str(tmp_path / "spill-b")],
    )

    write_task_params(params, path)

    assert read_task_params(path).max_workers == 3
    assert read_task_params(path).download_workers == 1
    assert read_task_params(path).download_proxy == "http://127.0.0.1:7890"
    assert read_task_params(path).spill_paths == [str(tmp_path / "spill-a"), str(tmp_path / "spill-b")]
    assert read_task_params(path).expression_output_formats == ["raw_counts", "fpkm"]
    assert read_task_params(path).stringtie_threads == 2


def test_read_task_params_backfills_download_workers(tmp_path):
    path = tmp_path / "params.json"
    path.write_text('{"max_workers": 4}', encoding="utf-8")

    params = read_task_params(path)

    assert params.max_workers == 4
    assert params.download_workers == 2
