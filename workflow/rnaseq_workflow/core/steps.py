from __future__ import annotations

from typing import Protocol

from rnaseq_workflow.core.models import RunContext, Sample, StepResult


class PipelineStep(Protocol):
    step_id: str
    name: str

    def validate_inputs(self, sample: Sample, context: RunContext) -> None:
        ...

    def run(self, sample: Sample, context: RunContext) -> StepResult:
        ...
