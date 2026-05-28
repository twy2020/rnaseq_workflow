from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from rnaseq_workflow.core.models import RunContext, Sample, SampleLayout, StepResult, StepStatus
from rnaseq_workflow.steps.quality_control.fastqc import (
    FastQCOptions,
    FastQCStep,
    TrimmedFastQCStep,
    _fastqc_outputs_complete,
    build_fastqc_command,
    summarize_fastqc_issues,
)


def test_build_fastqc_command():
    command = build_fastqc_command(
        ["S1_R1.fastq.gz", "S1_R2.fastq.gz"],
        "qc",
        FastQCOptions(threads=4, quiet=True, extract=True),
    )

    assert command == [
        "fastqc",
        "--threads",
        "4",
        "--outdir",
        "qc",
        "--quiet",
        "--extract",
        "S1_R1.fastq.gz",
        "S1_R2.fastq.gz",
    ]


def test_fastqc_step_dry_run(tmp_path):
    sample = Sample(
        sample_id="S1",
        source_path=tmp_path / "S1_R1.fastq.gz",
        source_paths=[tmp_path / "S1_R1.fastq.gz", tmp_path / "S1_R2.fastq.gz"],
        layout=SampleLayout.PAIRED,
    )
    context = RunContext(
        project_id="demo",
        work_dir=tmp_path,
        output_dir=tmp_path / "output",
        config={"fastqc_threads": 2},
        dry_run=True,
    )

    step = FastQCStep()
    step.validate_inputs(sample, context)
    result = step.run(sample, context)

    assert result.status == StepStatus.COMPLETED
    assert result.command is not None
    assert result.command[0] == "fastqc"
    assert Path(result.outputs[0]).name == "qc_raw"
    assert not (Path(result.outputs[0]) / ".done.json").exists()


def test_fastqc_step_docker_dry_run(tmp_path):
    sample = Sample(sample_id="S1", source_path=tmp_path / "S1.fastq", layout=SampleLayout.SINGLE)
    context = RunContext(
        project_id="demo",
        work_dir=tmp_path,
        output_dir=tmp_path / "output",
        config={"execution_mode": "docker", "docker_workspace": str(tmp_path)},
        dry_run=True,
    )

    result = FastQCStep().run(sample, context)

    assert result.command is not None
    assert result.command[:3] == ["docker", "run", "--rm"]
    assert "rnaseq-workflow:tools" in result.command
    assert "/workspace/S1.fastq" in result.command


def test_fastqc_step_rejects_non_fastq(tmp_path):
    sample = Sample(sample_id="S1", source_path=tmp_path / "S1.sra")
    context = RunContext(project_id="demo", work_dir=tmp_path, output_dir=tmp_path / "output", dry_run=True)

    with pytest.raises(ValueError, match="no FASTQ"):
        FastQCStep().validate_inputs(sample, context)


def test_fastqc_step_requires_existing_file_when_not_dry_run(tmp_path):
    sample = Sample(sample_id="S1", source_path=tmp_path / "missing.fastq.gz")
    context = RunContext(project_id="demo", work_dir=tmp_path, output_dir=tmp_path / "output", dry_run=False)

    with pytest.raises(FileNotFoundError):
        FastQCStep().validate_inputs(sample, context)


def test_fastqc_step_skips_done_output(tmp_path):
    sample = Sample(sample_id="S1", source_path=tmp_path / "S1.fastq.gz", layout=SampleLayout.SINGLE)
    sample.source_path.write_text("", encoding="utf-8")
    context = RunContext(project_id="demo", work_dir=tmp_path, output_dir=tmp_path / "output", dry_run=False)
    from rnaseq_workflow.core.step_state import write_done_marker

    output_dir = tmp_path / "output" / "samples" / "S1" / "qc_raw"
    write_done_marker(output_dir, StepResult(sample_id="S1", step_id="fastqc", status=StepStatus.COMPLETED, return_code=0, message="done"))
    second = FastQCStep().run(sample, context)

    assert second.status == StepStatus.SKIPPED
    assert second.message == "already completed; skip"


def test_fastqc_step_cleans_failed_output(tmp_path):
    sample = Sample(sample_id="S1", source_path=tmp_path / "S1.fastq.gz", layout=SampleLayout.SINGLE)
    sample.source_path.write_text("", encoding="utf-8")
    context = RunContext(project_id="demo", work_dir=tmp_path, output_dir=tmp_path / "output", dry_run=False)

    result = FastQCStep().run(sample, context)

    assert result.status == StepStatus.FAILED
    assert not Path(result.outputs[0]).exists()


def test_fastqc_outputs_complete_requires_html_and_valid_zip(tmp_path):
    fastq = tmp_path / "S1_R1.fastq"
    fastq.write_text("@r\nA\n+\n!\n", encoding="utf-8")
    output = tmp_path / "qc"
    output.mkdir()
    (output / "S1_R1_fastqc.html").write_text("<html></html>", encoding="utf-8")
    with zipfile.ZipFile(output / "S1_R1_fastqc.zip", "w") as archive:
        archive.writestr("S1_R1_fastqc/fastqc_data.txt", "ok")

    assert _fastqc_outputs_complete([fastq], output, stable_seconds=0)


def test_fastqc_step_treats_complete_outputs_as_success(tmp_path, monkeypatch):
    fastq = tmp_path / "S1.fastq"
    fastq.write_text("@r\nA\n+\n!\n", encoding="utf-8")
    sample = Sample(sample_id="S1", source_path=fastq, layout=SampleLayout.SINGLE)
    context = RunContext(
        project_id="demo",
        work_dir=tmp_path,
        output_dir=tmp_path / "output",
        config={"fastqc_completion_grace_seconds": 0},
        dry_run=False,
    )
    output = tmp_path / "output" / "samples" / "S1" / "qc_raw"
    output.mkdir(parents=True)

    from rnaseq_workflow.steps.quality_control import fastqc as module

    def fake_run_context_command(command, context, completion_check=None, completion_message=""):
        (output / "S1_fastqc.html").write_text("<html></html>", encoding="utf-8")
        with zipfile.ZipFile(output / "S1_fastqc.zip", "w") as archive:
            archive.writestr("S1_fastqc/fastqc_data.txt", "ok")
        assert completion_check is not None
        assert completion_check()
        from rnaseq_workflow.core.command import CommandResult

        return CommandResult(
            command=command,
            return_code=0,
            stdout="",
            stderr=completion_message,
            started_at="2026-01-01T00:00:00",
            finished_at="2026-01-01T00:00:01",
            duration_seconds=1.0,
        )

    monkeypatch.setattr(module, "run_context_command", fake_run_context_command)
    result = FastQCStep().run(sample, context)

    assert result.status == StepStatus.COMPLETED
    assert (output / ".done.json").exists()


def test_trimmed_fastqc_step_writes_qc_trimmed(tmp_path):
    sample = Sample(sample_id="S1", source_path=tmp_path / "S1_trimmed.fq.gz", layout=SampleLayout.SINGLE)
    context = RunContext(project_id="demo", work_dir=tmp_path, output_dir=tmp_path / "output", dry_run=True)

    result = TrimmedFastQCStep().run(sample, context)

    assert result.status == StepStatus.COMPLETED
    assert Path(result.outputs[0]).name == "qc_trimmed"
    assert result.extra["fastqc_output_kind"] == "trimmed"


def test_trimmed_fastqc_prefers_trimmed_fastq_dir(tmp_path):
    raw_r1 = tmp_path / "raw" / "S1_R1.fastq.gz"
    raw_r2 = tmp_path / "raw" / "S1_R2.fastq.gz"
    trimmed_dir = tmp_path / "output" / "samples" / "S1" / "trimmed_fastq"
    trimmed_dir.mkdir(parents=True)
    trimmed_r1 = trimmed_dir / "S1_R1_val_1.fq.gz"
    trimmed_r2 = trimmed_dir / "S1_R2_val_2.fq.gz"
    trimmed_r1.write_text("@r\nA\n+\n!\n", encoding="utf-8")
    trimmed_r2.write_text("@r\nA\n+\n!\n", encoding="utf-8")
    sample = Sample(
        sample_id="S1",
        source_path=raw_r1,
        source_paths=[raw_r1, raw_r2],
        layout=SampleLayout.PAIRED,
    )
    context = RunContext(project_id="demo", work_dir=tmp_path, output_dir=tmp_path / "output", dry_run=True)

    result = TrimmedFastQCStep().run(sample, context)

    assert result.command is not None
    assert str(trimmed_r1) in result.command
    assert str(trimmed_r2) in result.command
    assert str(raw_r1) not in result.command
    assert str(raw_r2) not in result.command


def test_trimmed_fastqc_disabled_policy_skips(tmp_path):
    sample = Sample(sample_id="S1", source_path=tmp_path / "S1_trimmed.fq.gz", layout=SampleLayout.SINGLE)
    context = RunContext(
        project_id="demo",
        work_dir=tmp_path,
        output_dir=tmp_path / "output",
        config={"trimmed_fastqc_policy": "disabled"},
        dry_run=False,
    )

    result = TrimmedFastQCStep().run(sample, context)

    assert result.status == StepStatus.SKIPPED
    assert result.message == "trimmed FastQC disabled by policy"


def test_summarize_fastqc_issues_reads_summary_txt(tmp_path):
    fastq = tmp_path / "S1_trimmed.fq.gz"
    fastq.write_text("@r\nA\n+\n!\n", encoding="utf-8")
    output = tmp_path / "qc_trimmed"
    output.mkdir()
    with zipfile.ZipFile(output / "S1_trimmed_fastqc.zip", "w") as archive:
        archive.writestr(
            "S1_trimmed_fastqc/summary.txt",
            "PASS\tBasic Statistics\tS1_trimmed.fq.gz\nWARN\tPer base sequence quality\tS1_trimmed.fq.gz\nFAIL\tAdapter Content\tS1_trimmed.fq.gz\n",
        )

    issues = summarize_fastqc_issues([fastq], output)

    assert [(item.status, item.module) for item in issues] == [
        ("WARN", "Per base sequence quality"),
        ("FAIL", "Adapter Content"),
    ]


def test_trimmed_fastqc_pause_policy_marks_sample_paused(tmp_path, monkeypatch):
    fastq = tmp_path / "S1_trimmed.fq.gz"
    fastq.write_text("@r\nA\n+\n!\n", encoding="utf-8")
    sample = Sample(sample_id="S1", source_path=fastq, layout=SampleLayout.SINGLE)
    context = RunContext(
        project_id="demo",
        work_dir=tmp_path,
        output_dir=tmp_path / "output",
        config={"trimmed_fastqc_policy": "pause_on_fail", "fastqc_completion_grace_seconds": 0},
        dry_run=False,
    )
    output = tmp_path / "output" / "samples" / "S1" / "qc_trimmed"

    from rnaseq_workflow.steps.quality_control import fastqc as module

    def fake_run_context_command(command, context, completion_check=None, completion_message=""):
        output.mkdir(parents=True, exist_ok=True)
        (output / "S1_trimmed_fastqc.html").write_text("<html></html>", encoding="utf-8")
        with zipfile.ZipFile(output / "S1_trimmed_fastqc.zip", "w") as archive:
            archive.writestr("S1_trimmed_fastqc/summary.txt", "FAIL\tAdapter Content\tS1_trimmed.fq.gz\n")
        from rnaseq_workflow.core.command import CommandResult

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

    result = TrimmedFastQCStep().run(sample, context)

    assert result.status == StepStatus.PAUSED
    assert result.extra["fastqc_quality_ok"] is False
    assert result.extra["fastqc_issues"][0]["module"] == "Adapter Content"
