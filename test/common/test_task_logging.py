from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

from rnaseq_workflow.core.logging import TaskLogManager, sanitize_text
from rnaseq_workflow.core.models import RunContext, Sample, StepResult, StepStatus
from rnaseq_workflow.core.pipeline import Pipeline
from rnaseq_workflow.core.step_state import write_done_marker
from rnaseq_workflow.persistence.json_state import JsonStateRepository


class CommandStep:
    step_id = "hisat2"
    name = "HISAT2 alignment"

    def validate_inputs(self, sample, context):
        return None

    def run(self, sample, context):
        return StepResult(
            sample_id=sample.sample_id,
            step_id=self.step_id,
            status=StepStatus.COMPLETED,
            message="done",
            command=["tool", "--token=secret-value", "http://user:pass@example.org:8080/path"],
            return_code=0,
            inputs=sample.source_paths,
            outputs=[context.output_dir / "out.txt"],
            extra={"stdout": "ok", "stderr": "Authorization: Bearer abc", "duration_seconds": 1.25},
        )


class MultiCommandStep:
    step_id = "samtools_sort"
    name = "samtools sort"

    def validate_inputs(self, sample, context):
        return None

    def run(self, sample, context):
        from rnaseq_workflow.core.command import run_context_command

        first = run_context_command(["samtools", "sort", "-o", "S1.bam", "S1.sam"], context)
        second = run_context_command(["samtools", "index", "S1.bam"], context)
        return StepResult(
            sample_id=sample.sample_id,
            step_id=self.step_id,
            status=StepStatus.COMPLETED if first.ok and second.ok else StepStatus.FAILED,
            message="samtools completed",
            command=second.command,
            return_code=second.return_code,
            inputs=sample.source_paths,
            outputs=[context.output_dir / "S1.bam", context.output_dir / "S1.bam.bai"],
            extra={"stdout": second.stdout, "stderr": second.stderr, "duration_seconds": second.duration_seconds},
        )


class DoneMarkerStep(CommandStep):
    step_id = "fastqc"
    name = "FastQC"

    def run(self, sample, context):
        result = StepResult(
            sample_id=sample.sample_id,
            step_id=self.step_id,
            status=StepStatus.COMPLETED,
            message="done",
            command=["fastqc", str(sample.source_path)],
            return_code=0,
            inputs=sample.source_paths,
            outputs=[context.output_dir / "samples" / sample.sample_id / "qc_raw"],
            extra={"stdout": "", "stderr": "", "duration_seconds": 0.5},
        )
        write_done_marker(result.outputs[0], result)
        return result


def test_task_log_manager_writes_valid_jsonl_concurrently(tmp_path):
    manager = TaskLogManager(tmp_path, task_id="task-1", user_id="user-1")

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda idx: manager.event("step_started", sample_id=f"S{idx}", step_id="fastqc"), range(20)))

    records = [json.loads(line) for line in manager.events_path.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 20
    assert {record["event"] for record in records} == {"step_started"}
    assert all(record["task_id"] == "task-1" for record in records)


def test_sanitize_text_masks_proxy_and_secret_values():
    text = sanitize_text("proxy=http://user:pass@host:8080 token=abc Authorization: Bearer secret")

    assert "user:pass" not in text
    assert "token=abc" not in text
    assert "Bearer secret" not in text
    assert "http://***:***@host:8080" in text


def test_pipeline_writes_step_command_logs_and_progress_references(tmp_path):
    sample = Sample("S1", tmp_path / "S1.fastq")
    context = RunContext("task-1", tmp_path, tmp_path / "output", config={"execution_mode": "local"}, dry_run=True)
    repository = JsonStateRepository(tmp_path / "output" / "progress.json")
    manager = TaskLogManager(tmp_path / "output", task_id="task-1")

    Pipeline([CommandStep()], repository, log_manager=manager).run_sample(sample, context)

    progress = json.loads((tmp_path / "output" / "progress.json").read_text(encoding="utf-8"))
    record = progress["samples"]["S1"]["steps"]["hisat2"]
    assert record["log_file"] == "logs/samples/S1/hisat2.log"
    assert record["extra"]["command_log_file"] == "logs/commands.jsonl"
    assert record["extra"]["command_id"].startswith("cmd-")

    command_record = json.loads(manager.commands_path.read_text(encoding="utf-8").splitlines()[0])
    assert command_record["sample_id"] == "S1"
    assert command_record["step_id"] == "hisat2"
    assert command_record["stdout_log"] == "logs/samples/S1/hisat2.log"
    assert command_record["duration_seconds"] == 1.25
    assert "secret-value" not in json.dumps(command_record)

    step_log = (tmp_path / "output" / "logs" / "samples" / "S1" / "hisat2.log").read_text(encoding="utf-8")
    assert "[stdout]" in step_log
    assert "ok" in step_log
    assert "Bearer abc" not in step_log

    events = [json.loads(line)["event"] for line in manager.events_path.read_text(encoding="utf-8").splitlines()]
    assert events == ["step_started", "step_completed"]


def test_pipeline_writes_one_command_record_per_external_command(tmp_path):
    sample = Sample("S1", tmp_path / "S1.sam")
    context = RunContext("task-1", tmp_path, tmp_path / "output", config={"execution_mode": "local"}, dry_run=True)
    repository = JsonStateRepository(tmp_path / "output" / "progress.json")
    manager = TaskLogManager(tmp_path / "output", task_id="task-1")

    Pipeline([MultiCommandStep()], repository, log_manager=manager).run_sample(sample, context)

    progress = json.loads((tmp_path / "output" / "progress.json").read_text(encoding="utf-8"))
    extra = progress["samples"]["S1"]["steps"]["samtools_sort"]["extra"]
    assert extra["command_count"] == 2
    assert len(extra["command_results"]) == 2
    assert len(extra["command_ids"]) == 2
    assert extra["command_id"] == extra["command_ids"][-1]

    command_records = [json.loads(line) for line in manager.commands_path.read_text(encoding="utf-8").splitlines()]
    assert [record["command_index"] for record in command_records] == [1, 2]
    assert [record["command"][:2] for record in command_records] == [["samtools", "sort"], ["samtools", "index"]]
    assert [record["command_id"] for record in command_records] == extra["command_ids"]

    step_log = (tmp_path / "output" / "logs" / "samples" / "S1" / "samtools_sort.log").read_text(encoding="utf-8")
    assert "command_index=1" in step_log
    assert "command_index=2" in step_log


def test_pipeline_backfills_done_marker_log_links(tmp_path):
    sample = Sample("S1", tmp_path / "S1.fastq")
    context = RunContext("task-1", tmp_path, tmp_path / "output", config={"execution_mode": "local"}, dry_run=True)
    repository = JsonStateRepository(tmp_path / "output" / "progress.json")
    manager = TaskLogManager(tmp_path / "output", task_id="task-1")

    Pipeline([DoneMarkerStep()], repository, log_manager=manager).run_sample(sample, context)

    done = json.loads((tmp_path / "output" / "samples" / "S1" / "qc_raw" / ".done.json").read_text(encoding="utf-8"))
    assert done["log_file"] == "logs/samples/S1/fastqc.log"
    assert done["command_id"].startswith("cmd-")
    assert done["command_log_file"] == "logs/commands.jsonl"
