from __future__ import annotations

import pytest

from rnaseq_workflow.core.errors import ConfigError
from rnaseq_workflow.core.step_registry import build_pipeline_steps


def test_build_pipeline_steps_expands_stages():
    steps = build_pipeline_steps(["quality_control", "alignment", "quantification"])

    assert [step.step_id for step in steps] == ["fastqc", "hisat2", "samtools_sort", "featurecounts"]


def test_default_pipeline_includes_trimmed_fastqc():
    steps = build_pipeline_steps(None)

    assert [step.step_id for step in steps] == ["fastqc", "trim_galore", "fastqc_trimmed", "hisat2", "samtools_sort", "featurecounts"]


def test_build_pipeline_steps_accepts_concrete_steps():
    steps = build_pipeline_steps(["fastqc", "trim_galore", "fastqc_trimmed", "stringtie"])

    assert [step.step_id for step in steps] == ["fastqc", "trim_galore", "fastqc_trimmed", "stringtie"]


def test_build_pipeline_steps_rejects_unknown_step():
    with pytest.raises(ConfigError, match="unknown pipeline step"):
        build_pipeline_steps(["not_a_step"])


def test_build_pipeline_steps_can_allow_placeholder():
    steps = build_pipeline_steps(["future_step"], allow_placeholder=True)

    assert steps[0].step_id == "future_step"
