from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from rnaseq_workflow.core.assets import build_asset_workspace
from rnaseq_workflow.core.errors import ConfigError


@dataclass(slots=True)
class ProjectConfig:
    project_id: str
    work_dir: Path
    output_dir: Path
    samples: list[dict[str, Any]] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    settings: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.project_id.strip():
            raise ConfigError("project_id must not be empty")
        if not self.samples:
            raise ConfigError("at least one sample is required")
        for index, sample in enumerate(self.samples, start=1):
            if not sample.get("sample_id"):
                raise ConfigError(f"samples[{index}] missing sample_id")
            if not sample.get("source_path"):
                raise ConfigError(f"samples[{index}] missing source_path")


def load_project_config(path: str | Path) -> ProjectConfig:
    config_path = Path(path)
    if not config_path.exists():
        raise ConfigError(f"config file not found: {config_path}")
    with config_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}

    if not isinstance(raw, dict):
        raise ConfigError("config root must be a mapping")

    project_id = str(raw.get("project_id") or raw.get("project_name") or "rnaseq_project")
    work_dir = Path(raw.get("work_dir") or config_path.parent).resolve()
    asset_root = raw.get("asset_root")
    user_id = raw.get("user_id")
    task_id = raw.get("task_id")
    if raw.get("output_dir"):
        output_dir = Path(raw["output_dir"]).resolve()
    elif asset_root and user_id and task_id:
        output_dir = build_asset_workspace(asset_root).user(str(user_id)).task(str(task_id)).task_output_dir.resolve()
    else:
        output_dir = Path(work_dir / "output").resolve()

    config = ProjectConfig(
        project_id=project_id,
        work_dir=work_dir,
        output_dir=output_dir,
        samples=list(raw.get("samples") or []),
        steps=list(raw.get("steps") or []),
        settings={
            key: value
            for key, value in raw.items()
            if key not in {"project_id", "project_name", "work_dir", "output_dir", "samples", "steps"}
        },
    )
    config.validate()
    return config
