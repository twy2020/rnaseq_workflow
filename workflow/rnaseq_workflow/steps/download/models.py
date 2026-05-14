from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from rnaseq_workflow.core.models import StepStatus


@dataclass(frozen=True, slots=True)
class DownloadRequest:
    accession: str
    output_dir: Path
    source: str = "sra"
    expected_size_bytes: int | None = None


@dataclass(slots=True)
class DownloadProgress:
    accession: str
    status: StepStatus
    downloaded_bytes: int = 0
    expected_size_bytes: int | None = None
    speed_bps: float = 0.0
    percent: float | None = None
    message: str = ""
    local_path: Path | None = None


@dataclass(slots=True)
class DownloadResult:
    accession: str
    status: StepStatus
    local_path: Path | None = None
    command: list[str] | None = None
    return_code: int | None = None
    message: str = ""
    cached: bool = False
    downloaded_bytes: int = 0
    speed_bps: float = 0.0


@dataclass(slots=True)
class BatchDownloadSummary:
    results: list[DownloadResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def completed(self) -> int:
        return sum(1 for result in self.results if result.status == StepStatus.COMPLETED)

    @property
    def failed(self) -> int:
        return sum(1 for result in self.results if result.status == StepStatus.FAILED)

    @property
    def cancelled(self) -> int:
        return sum(1 for result in self.results if result.status == StepStatus.CANCELLED)

    @property
    def skipped(self) -> int:
        return sum(1 for result in self.results if result.status == StepStatus.SKIPPED)
