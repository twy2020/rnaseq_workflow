from __future__ import annotations

from pathlib import Path

import pytest

from rnaseq_workflow.core.models import RunContext, Sample, SampleLayout, StepStatus
from rnaseq_workflow.steps.alignment.hisat2 import (
    Hisat2AlignStep,
    Hisat2Options,
    build_hisat2_command,
    build_hisat2_sort_command,
    hisat2_index_exists,
)


def test_build_hisat2_command_single():
    command = build_hisat2_command(
        ["S1.fastq.gz"],
        "genome_index",
        "S1.sam",
        "S1.log",
        Hisat2Options(index_prefix=Path("genome_index"), threads=8),
    )

    assert command == [
        "hisat2",
        "-p",
        "8",
        "-x",
        "genome_index",
        "-U",
        "S1.fastq.gz",
        "-S",
        "S1.sam",
        "--summary-file",
        "S1.log",
    ]


def test_build_hisat2_command_paired():
    command = build_hisat2_command(["R1.fq.gz", "R2.fq.gz"], "idx", "out.sam", "out.log")

    assert "-1" in command
    assert "-2" in command
    assert command[command.index("-1") + 1] == "R1.fq.gz"
    assert command[command.index("-2") + 1] == "R2.fq.gz"


def test_build_hisat2_sort_command_pipes_to_samtools():
    command = build_hisat2_sort_command(
        ["R1.fq.gz", "R2.fq.gz"],
        "idx",
        "out.sorted.bam",
        "out.log",
        Hisat2Options(index_prefix=Path("idx"), threads=2),
        samtools_threads=3,
    )

    assert command[:3] == ["bash", "-lc", command[2]]
    assert "hisat2" in command[2]
    assert "samtools sort" in command[2]
    assert "-S" not in command[2]
    assert "out.sorted.bam" in command


def test_hisat2_index_exists(tmp_path):
    prefix = tmp_path / "genome"
    for idx in range(1, 9):
        (tmp_path / f"genome.{idx}.ht2").write_text("", encoding="utf-8")

    assert hisat2_index_exists(prefix)


def test_hisat2_step_dry_run(tmp_path):
    sample = Sample(sample_id="S1", source_path=tmp_path / "S1.fastq", layout=SampleLayout.SINGLE)
    context = RunContext(
        project_id="demo",
        work_dir=tmp_path,
        output_dir=tmp_path / "output",
        config={"hisat2_index": str(tmp_path / "genome"), "hisat2_threads": 2},
        dry_run=True,
    )

    step = Hisat2AlignStep()
    step.validate_inputs(sample, context)
    result = step.run(sample, context)

    assert result.status == StepStatus.COMPLETED
    assert result.command is not None
    assert "-U" in result.command
    assert result.outputs[0].name == "S1.sam"


def test_hisat2_step_direct_sort_dry_run_outputs_bam(tmp_path):
    sample = Sample(sample_id="S1", source_path=tmp_path / "S1.fastq", layout=SampleLayout.SINGLE)
    context = RunContext(
        project_id="demo",
        work_dir=tmp_path,
        output_dir=tmp_path / "output",
        config={"hisat2_index": str(tmp_path / "genome"), "hisat2_sort_bam": True, "samtools_threads": 1},
        dry_run=True,
    )

    result = Hisat2AlignStep().run(sample, context)

    assert result.status == StepStatus.COMPLETED
    assert result.command is not None
    assert result.command[0:2] == ["bash", "-lc"]
    assert result.outputs[0].name == "S1.sorted.bam"
    assert result.extra["direct_bam_sort"] is True


def test_hisat2_requires_index(tmp_path):
    sample = Sample(sample_id="S1", source_path=tmp_path / "S1.fastq")
    context = RunContext(project_id="demo", work_dir=tmp_path, output_dir=tmp_path / "output", dry_run=True)

    with pytest.raises(ValueError, match="hisat2_index"):
        Hisat2AlignStep().validate_inputs(sample, context)
