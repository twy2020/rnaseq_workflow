from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from rnaseq_workflow.core.assets import TaskWorkspace


EXECUTION_MODES = {"sample_pipeline", "stage_batch"}
CLEANUP_POLICIES = {"cleanup_after_task", "cleanup_after_step", "no_auto_cleanup"}
DISK_GUARD_STRATEGIES = {"cancel", "transfer"}


@dataclass(frozen=True, slots=True)
class TaskParams:
    execution_mode: str = "sample_pipeline"
    cleanup_policy: str = "cleanup_after_task"
    max_workers: int = 2
    download_workers: int = 2
    docker_image: str = "rnaseq-workflow:tools"
    docker_workspace: str = "."
    download_source: str = "auto"
    download_max_size: str = "5G"
    download_proxy: str = ""
    sra_threads: int = 4
    fastqc_threads: int = 2
    trim_quality: int = 20
    trim_cores: int = 1
    hisat2_threads: int = 4
    samtools_threads: int = 2
    featurecounts_threads: int = 2
    featurecounts_feature_type: str = "exon"
    featurecounts_attribute_type: str = "gene_id"
    featurecounts_strandness: int = 0
    featurecounts_paired: bool = False
    reference_id: str = ""
    reference_dir: str = ""
    hisat2_index: str = ""
    annotation: str = ""
    downloads_dir: str = ""
    output_dir: str = ""
    reports_dir: str = ""
    resource_estimate: dict[str, Any] | None = None
    resource_guard_enabled: bool = True
    disk_guard_min_free_gb: float = 20.0
    disk_guard_min_free_percent: float = 10.0
    disk_guard_strategy: str = "cancel"
    spill_paths: list[str] = field(default_factory=list)
    spill_large_outputs: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ParamIssue:
    field: str
    message: str


def default_task_params(task: TaskWorkspace | None = None, defaults: dict[str, Any] | None = None) -> TaskParams:
    data: dict[str, Any] = {}
    if defaults:
        data.update(defaults)
    if task:
        data.setdefault("downloads_dir", str(task.downloads_dir))
        data.setdefault("output_dir", str(task.task_output_dir))
        data.setdefault("reports_dir", str(task.reports_dir))
        data.setdefault("docker_workspace", str(task.root.parents[3]))
    if "download_workers" not in data and "max_workers" in data:
        try:
            data["download_workers"] = max(1, min(int(data["max_workers"]), 2))
        except (TypeError, ValueError):
            data["download_workers"] = 2
    return TaskParams(**{key: value for key, value in data.items() if key in TaskParams.__dataclass_fields__})


def validate_task_params(params: TaskParams) -> list[ParamIssue]:
    issues: list[ParamIssue] = []
    if params.execution_mode not in EXECUTION_MODES:
        issues.append(ParamIssue("execution_mode", "must be sample_pipeline or stage_batch"))
    if params.cleanup_policy not in CLEANUP_POLICIES:
        issues.append(ParamIssue("cleanup_policy", "must be cleanup_after_task, cleanup_after_step, or no_auto_cleanup"))
    for field in (
        "max_workers",
        "download_workers",
        "sra_threads",
        "fastqc_threads",
        "trim_cores",
        "hisat2_threads",
        "samtools_threads",
        "featurecounts_threads",
    ):
        if int(getattr(params, field)) < 1:
            issues.append(ParamIssue(field, "must be >= 1"))
    if not 0 <= int(params.trim_quality) <= 40:
        issues.append(ParamIssue("trim_quality", "must be between 0 and 40"))
    if int(params.featurecounts_strandness) not in {0, 1, 2}:
        issues.append(ParamIssue("featurecounts_strandness", "must be 0, 1, or 2"))
    if not str(params.download_source or "").strip():
        issues.append(ParamIssue("download_source", "must not be empty"))
    if float(params.disk_guard_min_free_gb) < 0:
        issues.append(ParamIssue("disk_guard_min_free_gb", "must be >= 0"))
    if not 0 <= float(params.disk_guard_min_free_percent) <= 100:
        issues.append(ParamIssue("disk_guard_min_free_percent", "must be between 0 and 100"))
    if params.disk_guard_strategy not in DISK_GUARD_STRATEGIES:
        issues.append(ParamIssue("disk_guard_strategy", "must be cancel or transfer"))
    return issues


def write_task_params(params: TaskParams, path: str | Path) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(params.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return output


def read_task_params(path: str | Path) -> TaskParams:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if "download_workers" not in data and "max_workers" in data:
        try:
            data["download_workers"] = max(1, min(int(data["max_workers"]), 2))
        except (TypeError, ValueError):
            data["download_workers"] = 2
    return TaskParams(**{key: value for key, value in data.items() if key in TaskParams.__dataclass_fields__})
