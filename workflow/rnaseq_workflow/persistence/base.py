from __future__ import annotations

from typing import Protocol

from rnaseq_workflow.core.models import Sample, StepRecord, StepResult
from rnaseq_workflow.core.steps import PipelineStep


class StateRepository(Protocol):
    def get_step_record(self, sample_id: str, step_id: str) -> StepRecord | None:
        ...

    def mark_running(self, sample: Sample, step: PipelineStep) -> None:
        ...

    def save_step_result(self, step: PipelineStep, result: StepResult) -> None:
        ...

    def make_failed_result(self, sample: Sample, step: PipelineStep, message: str) -> StepResult:
        ...
