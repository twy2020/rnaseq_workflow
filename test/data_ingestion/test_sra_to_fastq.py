from __future__ import annotations

from pathlib import Path

from rnaseq_workflow.core.models import RunContext, Sample, StepRecord, StepResult, StepStatus
from rnaseq_workflow.steps.data_ingestion.sra_to_fastq import (
    SraToFastqOptions,
    SraToFastqStep,
    build_fasterq_dump_command,
)


def test_build_fasterq_dump_command():
    command = build_fasterq_dump_command(
        "SRR001.sra",
        "fastq",
        SraToFastqOptions(threads=8, split_files=True, include_progress=True),
    )

    assert command == [
        "fasterq-dump",
        "SRR001.sra",
        "--outdir",
        "fastq",
        "--threads",
        "8",
        "--split-files",
        "--progress",
    ]


def test_build_fasterq_dump_command_can_set_temp_dir():
    command = build_fasterq_dump_command(
        "SRR001.sra",
        "fastq",
        SraToFastqOptions(temp_dir=Path("fastq") / "_fasterq_tmp"),
    )

    assert command[-2:] == ["--temp", str(Path("fastq") / "_fasterq_tmp")]


def test_sra_to_fastq_step_dry_run(tmp_path):
    sample = Sample(sample_id="SRR001", source_path=tmp_path / "SRR001.sra")
    context = RunContext(
        project_id="demo",
        work_dir=tmp_path,
        output_dir=tmp_path / "output",
        config={"fasterq_dump_threads": 2},
        dry_run=True,
    )

    result = SraToFastqStep().run(sample, context)

    assert result.status == StepStatus.COMPLETED
    assert result.return_code == 0
    assert result.command is not None
    assert result.command[0] == "fasterq-dump"
    assert "--threads" in result.command
    assert "--temp" in result.command
    assert Path(result.outputs[0]).name == "raw_fastq"
    assert not (Path(result.outputs[0]) / ".done.json").exists()


def test_sra_to_fastq_step_skips_non_sra(tmp_path):
    sample = Sample(sample_id="S1", source_path=tmp_path / "S1.fastq.gz")
    context = RunContext(project_id="demo", work_dir=tmp_path, output_dir=tmp_path / "output", dry_run=True)

    result = SraToFastqStep().run(sample, context)

    assert result.status == StepStatus.SKIPPED


def test_sra_to_fastq_step_skips_done_output(tmp_path):
    sample = Sample(sample_id="SRR001", source_path=tmp_path / "SRR001.sra")
    context = RunContext(project_id="demo", work_dir=tmp_path, output_dir=tmp_path / "output", dry_run=False)
    from rnaseq_workflow.core.step_state import write_done_marker

    output_dir = tmp_path / "output" / "samples" / "SRR001" / "raw_fastq"
    write_done_marker(
        output_dir,
        StepResult(sample_id="SRR001", step_id="sra_to_fastq", status=StepStatus.COMPLETED, return_code=0, message="done"),
    )
    second = SraToFastqStep().run(sample, context)

    assert second.status == StepStatus.SKIPPED
    assert second.message == "already completed; skip"


def test_sra_to_fastq_step_attaches_existing_fastqs_after_done(tmp_path):
    sample = Sample(sample_id="SRR001", source_path=tmp_path / "SRR001.sra")
    context = RunContext(project_id="demo", work_dir=tmp_path, output_dir=tmp_path / "output", dry_run=False)
    from rnaseq_workflow.core.step_state import write_done_marker

    output_dir = tmp_path / "output" / "samples" / "SRR001" / "raw_fastq"
    output_dir.mkdir(parents=True)
    fastq = output_dir / "SRR001_1.fastq"
    fastq.write_text("@r\nA\n+\n!\n", encoding="utf-8")
    write_done_marker(
        output_dir,
        StepResult(sample_id="SRR001", step_id="sra_to_fastq", status=StepStatus.COMPLETED, return_code=0, message="done"),
    )

    SraToFastqStep().run(sample, context)

    assert sample.source_paths == [fastq]
    assert sample.metadata["input_type"] == "fastq"


def test_sra_to_fastq_apply_cached_result_attaches_fastqs(tmp_path):
    sample = Sample(sample_id="SRR001", source_path=tmp_path / "SRR001.sra", source_paths=[tmp_path / "SRR001.sra"])
    context = RunContext(project_id="demo", work_dir=tmp_path, output_dir=tmp_path / "output", dry_run=False)
    output_dir = tmp_path / "output" / "samples" / "SRR001" / "raw_fastq"
    output_dir.mkdir(parents=True)
    fastq = output_dir / "SRR001_1.fastq"
    fastq.write_text("@r\nA\n+\n!\n", encoding="utf-8")
    record = StepRecord(
        sample_id="SRR001",
        step_id="sra_to_fastq",
        step_name="SRA to FASTQ",
        status=StepStatus.COMPLETED,
        outputs=[str(output_dir)],
    )

    SraToFastqStep().apply_cached_result(sample, context, record)

    assert sample.source_paths == [fastq]
    assert sample.metadata["input_type"] == "fastq"
