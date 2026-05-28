from __future__ import annotations

from rnaseq_workflow.core.models import RunContext, Sample, StepResult, StepStatus
from rnaseq_workflow.executors.workflow_runner import WorkflowRunner
from rnaseq_workflow.persistence.json_state import JsonStateRepository


class RecordingStep:
    name = "Recording"

    def __init__(self, step_id: str, calls: list[tuple[str, str]]) -> None:
        self.step_id = step_id
        self.calls = calls

    def validate_inputs(self, sample, context):
        return None

    def run(self, sample, context):
        self.calls.append((sample.sample_id, self.step_id))
        return StepResult(sample.sample_id, self.step_id, StepStatus.COMPLETED)


class RerunCompletedStep(RecordingStep):
    rerun_completed = True


class PausingStep(RecordingStep):
    def run(self, sample, context):
        self.calls.append((sample.sample_id, self.step_id))
        return StepResult(sample.sample_id, self.step_id, StepStatus.PAUSED, message="manual review")


def test_sample_pipeline_runs_each_sample_through_steps_before_next_sample(tmp_path):
    calls: list[tuple[str, str]] = []
    steps = [RecordingStep("a", calls), RecordingStep("b", calls)]
    samples = [Sample("S1", tmp_path / "S1.fastq"), Sample("S2", tmp_path / "S2.fastq")]

    summary = WorkflowRunner(steps, JsonStateRepository(tmp_path / "state1.json"), mode="sample_pipeline", max_workers=1).run(
        samples,
        RunContext("demo", tmp_path, tmp_path / "out", dry_run=True),
    )

    assert calls == [("S1", "a"), ("S1", "b"), ("S2", "a"), ("S2", "b")]
    assert summary.completed_events == 4


def test_stage_batch_runs_all_samples_per_step(tmp_path):
    calls: list[tuple[str, str]] = []
    steps = [RecordingStep("a", calls), RecordingStep("b", calls)]
    samples = [Sample("S1", tmp_path / "S1.fastq"), Sample("S2", tmp_path / "S2.fastq")]

    WorkflowRunner(steps, JsonStateRepository(tmp_path / "state2.json"), mode="stage_batch", max_workers=1).run(
        samples,
        RunContext("demo", tmp_path, tmp_path / "out", dry_run=True),
    )

    assert calls == [("S1", "a"), ("S2", "a"), ("S1", "b"), ("S2", "b")]


def test_stage_batch_skips_later_stage_for_paused_sample(tmp_path):
    calls: list[tuple[str, str]] = []
    steps = [PausingStep("fastqc_trimmed", calls), RecordingStep("hisat2", calls)]
    samples = [Sample("S1", tmp_path / "S1.fastq")]
    repo = JsonStateRepository(tmp_path / "state-paused.json")

    WorkflowRunner(steps, repo, mode="stage_batch", max_workers=1).run(
        samples,
        RunContext("demo", tmp_path, tmp_path / "out", dry_run=True),
    )

    assert calls == [("S1", "fastqc_trimmed")]
    record = repo.get_step_record("S1", "hisat2")
    assert record is not None
    assert record.status == StepStatus.SKIPPED
    assert record.extra["skip_reason"] == "sample_paused"


def test_rerun_completed_step_does_not_skip_existing_completed_record(tmp_path):
    calls: list[tuple[str, str]] = []
    sample = Sample("S1", tmp_path / "S1.fastq")
    repo = JsonStateRepository(tmp_path / "state3.json")
    old_step = RecordingStep("download", [])
    repo.mark_running(sample, old_step)
    repo.save_step_result(old_step, StepResult("S1", "download", StepStatus.COMPLETED))

    WorkflowRunner([RerunCompletedStep("download", calls)], repo, mode="sample_pipeline", max_workers=1).run(
        [sample],
        RunContext("demo", tmp_path, tmp_path / "out", dry_run=True),
    )

    assert calls == [("S1", "download")]


def test_json_state_repository_recovers_corrupt_progress_file(tmp_path):
    path = tmp_path / "progress.json"
    path.write_text('{"samples": {"S1": ', encoding="utf-8")

    repo = JsonStateRepository(path)

    assert repo.get_step_record("S1", "download") is None
    assert path.read_text(encoding="utf-8") == '{\n  "samples": {}\n}'
    backups = list(tmp_path.glob("progress.json.corrupt-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == '{"samples": {"S1": '
