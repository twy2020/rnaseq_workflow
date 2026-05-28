from __future__ import annotations

from pathlib import Path

import pytest

from rnaseq_workflow.core.models import RunContext, Sample, SampleLayout, StepResult, StepStatus
from rnaseq_workflow.steps.read_trimming.trim_galore import (
    TrimGaloreOptions,
    TrimGaloreStep,
    build_trim_galore_command,
    find_trimmed_fastq_outputs,
    is_trim_galore_output_complete,
)


def test_build_trim_galore_command_paired():
    command = build_trim_galore_command(
        ["S1_R1.fastq.gz", "S1_R2.fastq.gz"],
        "trimmed",
        TrimGaloreOptions(quality=25, phred="33", stringency=5, gzip_output=True, cores=2, paired=True),
    )

    assert command == [
        "trim_galore",
        "--quality",
        "25",
        "--stringency",
        "5",
        "--cores",
        "2",
        "--output_dir",
        "trimmed",
        "--phred33",
        "--paired",
        "--gzip",
        "S1_R1.fastq.gz",
        "S1_R2.fastq.gz",
    ]


def test_trim_galore_step_dry_run_single(tmp_path):
    sample = Sample(
        sample_id="S1",
        source_path=tmp_path / "S1.fastq",
        layout=SampleLayout.SINGLE,
    )
    context = RunContext(
        project_id="demo",
        work_dir=tmp_path,
        output_dir=tmp_path / "output",
        config={"trim_galore_quality": 20, "trim_galore_cores": 1},
        dry_run=True,
    )

    step = TrimGaloreStep()
    step.validate_inputs(sample, context)
    result = step.run(sample, context)

    assert result.status == StepStatus.COMPLETED
    assert result.command is not None
    assert result.command[0] == "trim_galore"
    assert "--paired" not in result.command
    assert Path(result.outputs[0]).name == "trimmed_fastq"
    assert not (Path(result.outputs[0]) / ".done.json").exists()


def test_trim_galore_step_dry_run_paired(tmp_path):
    sample = Sample(
        sample_id="S1",
        source_path=tmp_path / "S1_R1.fastq.gz",
        source_paths=[tmp_path / "S1_R1.fastq.gz", tmp_path / "S1_R2.fastq.gz"],
        layout=SampleLayout.PAIRED,
    )
    context = RunContext(project_id="demo", work_dir=tmp_path, output_dir=tmp_path / "output", dry_run=True)

    result = TrimGaloreStep().run(sample, context)

    assert result.status == StepStatus.COMPLETED
    assert result.command is not None
    assert "--paired" in result.command


def test_trim_galore_rejects_paired_with_one_fastq(tmp_path):
    sample = Sample(sample_id="S1", source_path=tmp_path / "S1_R1.fastq.gz", layout=SampleLayout.PAIRED)
    context = RunContext(project_id="demo", work_dir=tmp_path, output_dir=tmp_path / "output", dry_run=True)

    with pytest.raises(ValueError, match="exactly two"):
        TrimGaloreStep().validate_inputs(sample, context)


def test_find_trimmed_fastq_outputs(tmp_path):
    (tmp_path / "S1_trimmed.fq.gz").write_text("", encoding="utf-8")
    (tmp_path / "report.txt").write_text("", encoding="utf-8")

    assert find_trimmed_fastq_outputs(tmp_path) == [tmp_path / "S1_trimmed.fq.gz"]


def test_is_trim_galore_output_complete_single(tmp_path):
    (tmp_path / "S1_trimmed.fq.gz").write_text("reads", encoding="utf-8")
    (tmp_path / "S1.fastq.gz_trimming_report.txt").write_text("report", encoding="utf-8")

    assert is_trim_galore_output_complete(tmp_path, paired=False)
    assert not is_trim_galore_output_complete(tmp_path, paired=True)


def test_trim_galore_step_skips_done_output(tmp_path):
    from rnaseq_workflow.core.step_state import write_done_marker

    sample = Sample(sample_id="S1", source_path=tmp_path / "S1.fastq.gz", layout=SampleLayout.SINGLE)
    sample.source_path.write_text("", encoding="utf-8")
    context = RunContext(project_id="demo", work_dir=tmp_path, output_dir=tmp_path / "output", dry_run=False)
    output_dir = tmp_path / "output" / "samples" / "S1" / "trimmed_fastq"
    trimmed = output_dir / "S1_trimmed.fq.gz"
    trimmed.parent.mkdir(parents=True)
    trimmed.write_text("reads", encoding="utf-8")
    write_done_marker(
        output_dir,
        StepResult(sample_id="S1", step_id="trim_galore", status=StepStatus.COMPLETED, return_code=0, message="done"),
    )

    result = TrimGaloreStep().run(sample, context)

    assert result.status == StepStatus.SKIPPED
    assert sample.source_paths == [trimmed]
    assert sample.metadata["trimmed_fastq_dir"] == str(output_dir)


def test_trim_galore_step_cleans_failed_output(tmp_path):
    sample = Sample(sample_id="S1", source_path=tmp_path / "S1.fastq.gz", layout=SampleLayout.SINGLE)
    sample.source_path.write_text("", encoding="utf-8")
    context = RunContext(project_id="demo", work_dir=tmp_path, output_dir=tmp_path / "output", dry_run=False)

    result = TrimGaloreStep().run(sample, context)

    assert result.status == StepStatus.FAILED
    assert Path(result.outputs[0]).exists()
    assert (Path(result.outputs[0]) / ".error.txt").exists()
    assert not any(path.name.endswith(".fq.gz") for path in Path(result.outputs[0]).iterdir())


def test_trim_galore_step_runs_with_output_dir_as_cwd(tmp_path, monkeypatch):
    sample = Sample(sample_id="S1", source_path=tmp_path / "S1.fastq.gz", layout=SampleLayout.SINGLE)
    sample.source_path.write_text("@r\nA\n+\n!\n", encoding="utf-8")
    context = RunContext(project_id="demo", work_dir=tmp_path, output_dir=tmp_path / "output", dry_run=False)
    captured = {}

    from rnaseq_workflow.core.command import CommandResult
    from rnaseq_workflow.steps.read_trimming import trim_galore as module

    def fake_run_context_command(command, context, cwd=None):
        captured["cwd"] = cwd
        return CommandResult(
            command=command,
            return_code=2,
            stdout="",
            stderr="intro\nFailed to write to file 'S1.fastq.gz_trimming_report.txt': No such file or directory\n",
            started_at="2026-01-01T00:00:00",
            finished_at="2026-01-01T00:00:01",
            duration_seconds=1.0,
        )

    monkeypatch.setattr(module, "run_context_command", fake_run_context_command)

    result = TrimGaloreStep().run(sample, context)

    assert captured["cwd"] == tmp_path / "output" / "samples" / "S1" / "trimmed_fastq"
    assert result.message == "Failed to write to file 'S1.fastq.gz_trimming_report.txt': No such file or directory"


def test_trim_galore_step_recovers_complete_output_with_stale_lock(tmp_path):
    sample = Sample(
        sample_id="S1",
        source_path=tmp_path / "S1_R1.fastq.gz",
        source_paths=[tmp_path / "S1_R1.fastq.gz", tmp_path / "S1_R2.fastq.gz"],
        layout=SampleLayout.PAIRED,
    )
    for path in sample.source_paths:
        path.write_text("", encoding="utf-8")
    output_dir = tmp_path / "output" / "samples" / "S1" / "trimmed_fastq"
    output_dir.mkdir(parents=True)
    (output_dir / ".lock").write_text("stale", encoding="utf-8")
    (output_dir / "S1_R1_val_1.fq.gz").write_text("r1", encoding="utf-8")
    (output_dir / "S1_R2_val_2.fq.gz").write_text("r2", encoding="utf-8")
    (output_dir / "S1_R1.fastq.gz_trimming_report.txt").write_text("report", encoding="utf-8")
    (output_dir / "S1_R2.fastq.gz_trimming_report.txt").write_text("report", encoding="utf-8")
    context = RunContext(project_id="demo", work_dir=tmp_path, output_dir=tmp_path / "output", dry_run=False)

    result = TrimGaloreStep().run(sample, context)

    assert result.status == StepStatus.COMPLETED
    assert (output_dir / ".done.json").exists()
    assert not (output_dir / ".lock").exists()


def test_trim_galore_success_attaches_trimmed_fastqs(tmp_path, monkeypatch):
    sample = Sample(sample_id="S1", source_path=tmp_path / "S1.fastq.gz", layout=SampleLayout.SINGLE)
    sample.source_path.write_text("@r\nA\n+\n!\n", encoding="utf-8")
    context = RunContext(project_id="demo", work_dir=tmp_path, output_dir=tmp_path / "output", dry_run=False)
    output_dir = tmp_path / "output" / "samples" / "S1" / "trimmed_fastq"

    from rnaseq_workflow.core.command import CommandResult
    from rnaseq_workflow.steps.read_trimming import trim_galore as module

    def fake_run_context_command(command, context, cwd=None):
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "S1_trimmed.fq.gz").write_text("reads", encoding="utf-8")
        (output_dir / "S1.fastq.gz_trimming_report.txt").write_text("report", encoding="utf-8")
        return CommandResult(
            command=command,
            return_code=0,
            stdout="",
            stderr="",
            started_at="2026-01-01T00:00:00",
            finished_at="2026-01-01T00:00:01",
            duration_seconds=1.0,
        )

    monkeypatch.setattr(module, "run_context_command", fake_run_context_command)

    result = TrimGaloreStep().run(sample, context)

    assert result.status == StepStatus.COMPLETED
    assert sample.source_paths == [output_dir / "S1_trimmed.fq.gz"]
    assert sample.metadata["trimmed_fastq_dir"] == str(output_dir)
