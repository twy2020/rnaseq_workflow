from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class SampleLayout(str, Enum):
    SINGLE = "single"
    PAIRED = "paired"
    UNKNOWN = "unknown"


class StepStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    CANCELLED = "CANCELLED"
    PAUSED = "PAUSED"


@dataclass(slots=True)
class Sample:
    sample_id: str
    source_path: Path
    layout: SampleLayout = SampleLayout.UNKNOWN
    project_id: str | None = None
    source_paths: list[Path] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.source_paths:
            self.source_paths = [self.source_path]


@dataclass(slots=True)
class RunContext:
    project_id: str
    work_dir: Path
    output_dir: Path
    config: dict[str, Any] = field(default_factory=dict)
    dry_run: bool = False


@dataclass(slots=True)
class StepResult:
    sample_id: str
    step_id: str
    status: StepStatus
    message: str = ""
    command: list[str] | None = None
    return_code: int | None = None
    inputs: list[Path] = field(default_factory=list)
    outputs: list[Path] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)
    log_file: str | None = None

    def to_record(self, step_name: str) -> "StepRecord":
        return StepRecord(
            sample_id=self.sample_id,
            step_id=self.step_id,
            step_name=step_name,
            status=self.status,
            message=self.message,
            command=self.command,
            return_code=self.return_code,
            inputs=[str(path) for path in self.inputs],
            outputs=[str(path) for path in self.outputs],
            extra=self.extra,
            log_file=self.log_file,
        )


@dataclass(slots=True)
class StepRecord:
    sample_id: str
    step_id: str
    step_name: str
    status: StepStatus
    message: str = ""
    command: list[str] | None = None
    return_code: int | None = None
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    started_at: str | None = None
    finished_at: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    log_file: str | None = None
