from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from rnaseq_workflow.core.command import collect_context_commands
from rnaseq_workflow.core.logging import TaskLogManager
from rnaseq_workflow.core.models import RunContext, Sample, StepResult, StepStatus
from rnaseq_workflow.core.step_state import update_done_markers_for_result
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
    log_manager: TaskLogManager | None = None

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
                if self.log_manager is not None:
                    self.log_manager.log_step_result(
                        result,
                        step_name=step.name,
                        execution_mode=str(context.config.get("execution_mode", "local")),
                        event_name="step_cancelled",
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
            pause_record = self.repository.get_sample_pause_record(sample.sample_id)
            if pause_record is not None:
                result = StepResult(
                    sample_id=sample.sample_id,
                    step_id=step.step_id,
                    status=StepStatus.SKIPPED,
                    message=f"sample paused after {pause_record.step_id}: {pause_record.message}",
                    inputs=sample.source_paths,
                    extra={
                        "skip_reason": "sample_paused",
                        "paused_step_id": pause_record.step_id,
                        "pause_message": pause_record.message,
                    },
                )
                if self.log_manager is not None:
                    self.log_manager.log_step_result(
                        result,
                        step_name=step.name,
                        execution_mode=str(context.config.get("execution_mode", "local")),
                        event_name="step_skipped",
                    )
                self.repository.save_step_result(step, result)
                self._emit(
                    PipelineEvent(
                        event="finished",
                        sample_id=sample.sample_id,
                        step_id=step.step_id,
                        step_name=step.name,
                        status=StepStatus.SKIPPED,
                        message=result.message,
                    )
                )
                break
            existing = self.repository.get_step_record(sample.sample_id, step.step_id)
            if (
                existing
                and existing.status == StepStatus.COMPLETED
                and not bool(getattr(step, "rerun_completed", False))
                and _completed_record_can_be_reused(existing, context)
            ):
                apply_cached = getattr(step, "apply_cached_result", None)
                if callable(apply_cached):
                    apply_cached(sample, context, existing)
                if self.log_manager is not None:
                    self.log_manager.event(
                        "step_skipped",
                        sample_id=sample.sample_id,
                        step_id=step.step_id,
                        message="already completed",
                        status=StepStatus.COMPLETED.value,
                    )
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
            if self.log_manager is not None:
                self.log_manager.event("step_started", sample_id=sample.sample_id, step_id=step.step_id, message="step started")
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
                with collect_context_commands() as command_results:
                    result = step.run(sample, context)
                _attach_command_results(result, command_results)
            except Exception as exc:
                result = self.repository.make_failed_result(sample, step, str(exc))

            if self.log_manager is not None:
                self.log_manager.log_step_result(
                    result,
                    step_name=step.name,
                    execution_mode=str(context.config.get("execution_mode", "local")),
                    event_name=_event_name_for_status(result.status),
                )
                update_done_markers_for_result(result)
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
            if result.status == StepStatus.PAUSED:
                if self.log_manager is not None:
                    self.log_manager.event(
                        "sample_paused",
                        sample_id=sample.sample_id,
                        step_id=step.step_id,
                        message=result.message,
                        status=StepStatus.PAUSED.value,
                        reason=result.extra.get("pause_reason"),
                    )
                break

    def _emit(self, event: PipelineEvent) -> None:
        if self.event_callback is not None:
            self.event_callback(event)


def _event_name_for_status(status: StepStatus) -> str:
    if status == StepStatus.COMPLETED:
        return "step_completed"
    if status == StepStatus.FAILED:
        return "step_failed"
    if status == StepStatus.SKIPPED:
        return "step_skipped"
    if status == StepStatus.CANCELLED:
        return "step_cancelled"
    if status == StepStatus.PAUSED:
        return "step_paused"
    return "step_completed"


def _completed_record_can_be_reused(record, context: RunContext) -> bool:
    if not context.dry_run and bool(record.extra.get("dry_run", False)):
        return False
    outputs = [Path(path) for path in getattr(record, "outputs", []) if path]
    if context.dry_run or not outputs:
        return True
    return all(_output_path_available(path) for path in outputs)


def _output_path_available(path: Path) -> bool:
    if path.is_file():
        try:
            return path.stat().st_size > 0
        except OSError:
            return False
    if path.is_dir():
        return True
    return False


def _attach_command_results(result: StepResult, command_results) -> None:
    if not command_results:
        return
    result.extra["command_results"] = [
        {
            "command": item.command,
            "return_code": item.return_code,
            "started_at": item.started_at,
            "finished_at": item.finished_at,
            "duration_seconds": item.duration_seconds,
            "stdout": item.stdout,
            "stderr": item.stderr,
            "dry_run": item.dry_run,
        }
        for item in command_results
    ]
    result.extra["command_count"] = len(command_results)
