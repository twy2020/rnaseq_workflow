from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from rnaseq_workflow.core.models import RunContext, Sample, StepResult, StepStatus
from rnaseq_workflow.core.steps import PipelineStep
from rnaseq_workflow.persistence.base import StateRepository


@dataclass(frozen=True, slots=True)
class PipelineEvent:
    event: str
    sample_id: str
    step_id: str
    step_name: str
    status: StepStatus
    message: str = ""


@dataclass(slots=True)
class Pipeline:
    steps: list[PipelineStep]
    repository: StateRepository
    event_callback: Callable[[PipelineEvent], None] | None = None

    def run_sample(self, sample: Sample, context: RunContext) -> None:
        for step in self.steps:
            cancellation_token = context.config.get("cancellation_token")
            if cancellation_token is not None and cancellation_token.is_cancelled():
                result = StepResult(
                    sample_id=sample.sample_id,
                    step_id=step.step_id,
                    status=StepStatus.CANCELLED,
                    message="cancelled before start",
                    inputs=sample.source_paths,
                )
                self.repository.save_step_result(step, result)
                self._emit(
                    PipelineEvent(
                        event="finished",
                        sample_id=sample.sample_id,
                        step_id=step.step_id,
                        step_name=step.name,
                        status=StepStatus.CANCELLED,
                        message=result.message,
                    )
                )
                break
            existing = self.repository.get_step_record(sample.sample_id, step.step_id)
            if existing and existing.status == StepStatus.COMPLETED and not bool(getattr(step, "rerun_completed", False)):
                apply_cached = getattr(step, "apply_cached_result", None)
                if callable(apply_cached):
                    apply_cached(sample, context, existing)
                self._emit(
                    PipelineEvent(
                        event="skipped_completed",
                        sample_id=sample.sample_id,
                        step_id=step.step_id,
                        step_name=step.name,
                        status=StepStatus.COMPLETED,
                        message="already completed",
                    )
                )
                continue

            self.repository.mark_running(sample, step)
            self._emit(
                PipelineEvent(
                    event="started",
                    sample_id=sample.sample_id,
                    step_id=step.step_id,
                    step_name=step.name,
                    status=StepStatus.RUNNING,
                )
            )
            try:
                step.validate_inputs(sample, context)
                result = step.run(sample, context)
            except Exception as exc:
                result = self.repository.make_failed_result(sample, step, str(exc))

            self.repository.save_step_result(step, result)
            self._emit(
                PipelineEvent(
                    event="finished",
                    sample_id=sample.sample_id,
                    step_id=step.step_id,
                    step_name=step.name,
                    status=result.status,
                    message=result.message,
                )
            )
            if result.status == StepStatus.FAILED:
                break

    def _emit(self, event: PipelineEvent) -> None:
        if self.event_callback is not None:
            self.event_callback(event)
