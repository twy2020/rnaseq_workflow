from __future__ import annotations

import pytest

from rnaseq_workflow.core.models import RunContext, Sample, StepStatus
from rnaseq_workflow.steps.quantification.featurecounts import (
    FeatureCountsOptions,
    FeatureCountsStep,
    build_featurecounts_command,
)


def test_build_featurecounts_command_single_bam():
    command = build_featurecounts_command(
        ["S1.sorted.bam"],
        "genes.gtf",
        "S1.counts.txt",
        FeatureCountsOptions(annotation_path="genes.gtf", threads=4, strandness=1),
    )

    assert command == [
        "featureCounts",
        "-T",
        "4",
        "-a",
        "genes.gtf",
        "-o",
        "S1.counts.txt",
        "-t",
        "exon",
        "-g",
        "gene_id",
        "-s",
        "1",
        "S1.sorted.bam",
    ]


def test_build_featurecounts_command_paired():
    command = build_featurecounts_command(
        ["S1.sorted.bam"],
        "genes.gtf",
        "S1.counts.txt",
        FeatureCountsOptions(annotation_path="genes.gtf", paired=True),
    )

    assert "-p" in command
    assert command[-1] == "S1.sorted.bam"


def test_featurecounts_rejects_empty_bam_list():
    with pytest.raises(ValueError, match="at least one BAM"):
        build_featurecounts_command([], "genes.gtf", "counts.txt")


def test_featurecounts_step_dry_run(tmp_path):
    sample = Sample(sample_id="S1", source_path=tmp_path / "S1.sorted.bam")
    context = RunContext(
        project_id="demo",
        work_dir=tmp_path,
        output_dir=tmp_path / "output",
        config={
            "featurecounts_annotation": str(tmp_path / "genes.gtf"),
            "featurecounts_bam": str(tmp_path / "S1.sorted.bam"),
            "featurecounts_threads": 2,
        },
        dry_run=True,
    )

    step = FeatureCountsStep()
    step.validate_inputs(sample, context)
    result = step.run(sample, context)

    assert result.status == StepStatus.COMPLETED
    assert result.command is not None
    assert result.command[0] == "featureCounts"
    assert result.outputs[0].name == "S1.featureCounts.txt"
    assert result.outputs[1].name == "S1.featureCounts.txt.summary"


def test_featurecounts_step_uses_sample_bam_path(tmp_path):
    bam = tmp_path / "custom.sorted.bam"
    sample = Sample(sample_id="S1", source_path=bam)
    context = RunContext(
        project_id="demo",
        work_dir=tmp_path,
        output_dir=tmp_path / "output",
        config={
            "featurecounts_annotation": str(tmp_path / "genes.gtf"),
            "featurecounts_threads": 2,
        },
        dry_run=True,
    )

    result = FeatureCountsStep().run(sample, context)

    assert str(bam) in result.command


def test_featurecounts_requires_annotation(tmp_path):
    sample = Sample(sample_id="S1", source_path=tmp_path / "S1.sorted.bam")
    context = RunContext(project_id="demo", work_dir=tmp_path, output_dir=tmp_path / "output", dry_run=True)

    with pytest.raises(ValueError, match="featurecounts_annotation"):
        FeatureCountsStep().validate_inputs(sample, context)


def test_featurecounts_requires_existing_inputs_when_not_dry_run(tmp_path):
    sample = Sample(sample_id="S1", source_path=tmp_path / "S1.sorted.bam")
    context = RunContext(
        project_id="demo",
        work_dir=tmp_path,
        output_dir=tmp_path / "output",
        config={"featurecounts_annotation": str(tmp_path / "missing.gtf")},
        dry_run=False,
    )

    with pytest.raises(FileNotFoundError):
        FeatureCountsStep().validate_inputs(sample, context)
