from __future__ import annotations

from rnaseq_workflow.core.errors import ConfigError
from rnaseq_workflow.core.steps import PipelineStep
from rnaseq_workflow.steps.alignment import Hisat2AlignStep, SamtoolsSortStep
from rnaseq_workflow.steps.data_ingestion import SraToFastqStep
from rnaseq_workflow.steps.placeholder import PlaceholderStep
from rnaseq_workflow.steps.quality_control import FastQCStep, TrimmedFastQCStep
from rnaseq_workflow.steps.quantification import FeatureCountsStep, StringTieStep
from rnaseq_workflow.steps.read_trimming import TrimGaloreStep


PIPELINE_STAGE_LABELS = {
    "download": "Download public data",
    "data_ingestion": "Prepare local inputs",
    "quality_control": "Quality control",
    "trimmed_quality_control": "Trimmed read quality control",
    "read_trimming": "Read trimming",
    "alignment": "Alignment",
    "quantification": "Quantification",
    "reporting": "Reporting",
}

DEFAULT_SAMPLE_STEPS = [
    "quality_control",
    "read_trimming",
    "trimmed_quality_control",
    "alignment",
    "quantification",
]


def build_pipeline_steps(step_ids: list[str] | None, allow_placeholder: bool = False) -> list[PipelineStep]:
    selected = step_ids or DEFAULT_SAMPLE_STEPS
    steps: list[PipelineStep] = []
    for step_id in selected:
        if step_id in _STAGE_EXPANSIONS:
            for expanded in _STAGE_EXPANSIONS[step_id]:
                steps.extend(build_pipeline_steps([expanded], allow_placeholder=allow_placeholder))
            continue
        factory = _STEP_FACTORIES.get(step_id)
        if factory is None:
            if allow_placeholder:
                steps.append(PlaceholderStep(step_id=step_id, name=PIPELINE_STAGE_LABELS.get(step_id, step_id)))
                continue
            raise ConfigError(f"unknown pipeline step: {step_id}")
        steps.append(factory())
    return steps


def expand_step_ids(step_ids: list[str] | None) -> list[str]:
    selected = step_ids or DEFAULT_SAMPLE_STEPS
    expanded: list[str] = []
    for step_id in selected:
        if step_id in _STAGE_EXPANSIONS:
            expanded.extend(expand_step_ids(_STAGE_EXPANSIONS[step_id]))
        else:
            if step_id not in _STEP_FACTORIES:
                raise ConfigError(f"unknown pipeline step: {step_id}")
            expanded.append(step_id)
    return expanded


_STEP_FACTORIES = {
    "sra_to_fastq": SraToFastqStep,
    "fastqc": FastQCStep,
    "fastqc_trimmed": TrimmedFastQCStep,
    "trim_galore": TrimGaloreStep,
    "hisat2": Hisat2AlignStep,
    "samtools_sort": SamtoolsSortStep,
    "featurecounts": FeatureCountsStep,
    "stringtie": StringTieStep,
}

_STAGE_EXPANSIONS = {
    "data_ingestion": ["sra_to_fastq"],
    "quality_control": ["fastqc"],
    "read_trimming": ["trim_galore"],
    "trimmed_quality_control": ["fastqc_trimmed"],
    "alignment": ["hisat2", "samtools_sort"],
    "quantification": ["featurecounts"],
}
