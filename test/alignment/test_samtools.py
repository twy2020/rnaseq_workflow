from __future__ import annotations

from rnaseq_workflow.core.models import RunContext, Sample, StepStatus
from rnaseq_workflow.steps.alignment.samtools import (
    SamtoolsSortOptions,
    SamtoolsSortStep,
    build_samtools_index_command,
    build_samtools_sort_command,
)


def test_build_samtools_sort_command():
    command = build_samtools_sort_command("S1.sam", "S1.bam", SamtoolsSortOptions(threads=4))

    assert command == ["samtools", "sort", "-@", "4", "-o", "S1.bam", "S1.sam"]


def test_build_samtools_index_command():
    assert build_samtools_index_command("S1.bam") == ["samtools", "index", "S1.bam"]


def test_samtools_sort_step_dry_run(tmp_path):
    sample = Sample(sample_id="S1", source_path=tmp_path / "S1.fastq")
    context = RunContext(
        project_id="demo",
        work_dir=tmp_path,
        output_dir=tmp_path / "output",
        config={"samtools_threads": 2},
        dry_run=True,
    )

    result = SamtoolsSortStep().run(sample, context)

    assert result.status == StepStatus.COMPLETED
    assert result.command is not None
    assert result.command[0:2] == ["samtools", "sort"]
    assert result.outputs[0].name == "S1.sorted.bam"


def test_samtools_sort_uses_sample_sam_path_for_tui_scan(tmp_path):
    sam = tmp_path / "hisat2" / "samples" / "S1" / "alignment" / "S1.sam"
    sam.parent.mkdir(parents=True)
    sam.write_text("@HD\n", encoding="utf-8")
    sample = Sample(sample_id="S1", source_path=sam, source_paths=[sam])
    context = RunContext(
        project_id="demo",
        work_dir=tmp_path,
        output_dir=tmp_path / "output",
        config={"samtools_threads": 2},
        dry_run=True,
    )

    result = SamtoolsSortStep().run(sample, context)

    assert result.status == StepStatus.COMPLETED
    assert result.command is not None
    assert str(sam) in result.command


def test_samtools_sort_skips_when_hisat2_direct_sort_created_bam(tmp_path):
    sample = Sample(sample_id="S1", source_path=tmp_path / "S1.fastq")
    alignment_dir = tmp_path / "output" / "samples" / "S1" / "alignment"
    alignment_dir.mkdir(parents=True)
    bam = alignment_dir / "S1.sorted.bam"
    bai = alignment_dir / "S1.sorted.bam.bai"
    bam.write_bytes(b"bam")
    bai.write_bytes(b"bai")
    context = RunContext(
        project_id="demo",
        work_dir=tmp_path,
        output_dir=tmp_path / "output",
        config={"hisat2_sort_bam": True},
        dry_run=False,
    )

    step = SamtoolsSortStep()
    step.validate_inputs(sample, context)
    result = step.run(sample, context)

    assert result.status == StepStatus.SKIPPED
    assert result.extra["skipped_existing"] is True
