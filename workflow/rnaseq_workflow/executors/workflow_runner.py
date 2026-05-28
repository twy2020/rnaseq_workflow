from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from rnaseq_workflow.core.logging import TaskLogManager
from rnaseq_workflow.core.models import RunContext, Sample, StepStatus
from rnaseq_workflow.core.pipeline import Pipeline, PipelineEvent
from rnaseq_workflow.core.steps import PipelineStep
from rnaseq_workflow.executors.local import LocalExecutor
from rnaseq_workflow.persistence.base import StateRepository


WorkflowEventCallback = Callable[[PipelineEvent], None]


@dataclass(frozen=True, slots=True)
class WorkflowRunSummary:
    mode: str
    sample_count: int
    step_count: int
    completed_events: int = 0
    failed_events: int = 0
    paused_events: int = 0


@dataclass(slots=True)
class WorkflowRunner:
    steps: list[PipelineStep]
    repository: StateRepository
    mode: str = "sample_pipeline"
    max_workers: int = 2
    event_callback: WorkflowEventCallback | None = None
    log_manager: TaskLogManager | None = None
    events: list[PipelineEvent] = field(default_factory=list)

    def run(self, samples: list[Sample], context: RunContext) -> WorkflowRunSummary:
        if self.mode == "stage_batch":
            return self._run_stage_batch(samples, context)
        return self._run_sample_pipeline(samples, context)

    def _run_sample_pipeline(self, samples: list[Sample], context: RunContext) -> WorkflowRunSummary:
        pipeline = Pipeline(self.steps, self.repository, event_callback=self._on_event, log_manager=self.log_manager)
        LocalExecutor(pipeline, max_workers=self.max_workers).run(samples, context)
        return self._summary("sample_pipeline", samples)

    def _run_stage_batch(self, samples: list[Sample], context: RunContext) -> WorkflowRunSummary:
        for step in self.steps:
            pipeline = Pipeline([step], self.repository, event_callback=self._on_event, log_manager=self.log_manager)
            LocalExecutor(pipeline, max_workers=self.max_workers).run(samples, context)
        return self._summary("stage_batch", samples)

    def _on_event(self, event: PipelineEvent) -> None:
        self.events.append(event)
        if self.event_callback:
            self.event_callback(event)

    def _summary(self, mode: str, samples: list[Sample]) -> WorkflowRunSummary:
        return WorkflowRunSummary(
            mode=mode,
            sample_count=len(samples),
            step_count=len(self.steps),
            completed_events=sum(1 for event in self.events if event.event == "finished" and event.status == StepStatus.COMPLETED),
            failed_events=sum(1 for event in self.events if event.event == "finished" and event.status == StepStatus.FAILED),
            paused_events=sum(1 for event in self.events if event.event == "finished" and event.status == StepStatus.PAUSED),
        )
