from __future__ import annotations

from dataclasses import dataclass

from rnaseq_workflow.core.models import RunContext, Sample, StepResult, StepStatus


@dataclass(slots=True)
class PlaceholderStep:
    step_id: str
    name: str

    def validate_inputs(self, sample: Sample, context: RunContext) -> None:
        if not context.dry_run and not sample.source_path.exists():
            raise FileNotFoundError(f"Sample source not found: {sample.source_path}")

    def run(self, sample: Sample, context: RunContext) -> StepResult:
        message = "dry-run placeholder completed" if context.dry_run else "placeholder completed"
        return StepResult(
            sample_id=sample.sample_id,
            step_id=self.step_id,
            status=StepStatus.COMPLETED,
            message=message,
            inputs=[sample.source_path],
        )
