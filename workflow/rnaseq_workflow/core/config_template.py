from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ConfigTemplateOptions:
    project_id: str = "rnaseq_project"
    asset_root: str = "workspace"
    user_id: str = ""
    task_id: str = ""
    task_name: str = ""
    output_dir: str = "output"
    sample_id: str = "S1"
    source_path: str = "data/S1.fastq.gz"
    layout: str = "single"
    execution_mode: str = "docker"
    docker_image: str = "rnaseq-workflow:tools"
    docker_workspace: str = "."
    reference_id: str = "demo_reference"
    reference_dir: str = "references"
    hisat2_index: str = "references/demo_reference/hisat2/genome"
    annotation: str = "references/demo_reference/annotation.gtf"


def build_config_template(options: ConfigTemplateOptions | None = None) -> str:
    opts = options or ConfigTemplateOptions()
    return "\n".join(
        [
            "# RNA-seq workflow project config",
            f"project_id: {opts.project_id}",
            f"asset_root: {opts.asset_root}",
            f"user_id: {opts.user_id}",
            f"task_id: {opts.task_id}",
            f"task_name: {opts.task_name}",
            f"work_dir: .",
            f"output_dir: {opts.output_dir}",
            "",
            "samples:",
            f"  - sample_id: {opts.sample_id}",
            f"    source_path: {opts.source_path}",
            f"    layout: {opts.layout}",
            "",
            "steps:",
            "  - quality_control",
            "  - read_trimming",
            "  - trimmed_quality_control",
            "  - alignment",
            "  - quantification",
            "",
            "# Execution mode: local or docker.",
            f"execution_mode: {opts.execution_mode}",
            f"docker_image: {opts.docker_image}",
            f"docker_workspace: {opts.docker_workspace}",
            "",
            "# Managed reference",
            f"reference_id: {opts.reference_id}",
            f"reference_dir: {opts.reference_dir}",
            "",
            "# Quality control",
            "fastqc_threads: 2",
            "fastqc_quiet: true",
            "trimmed_fastqc_policy: run_keep  # run_keep, pause_on_fail, or disabled",
            "",
            "# Read trimming",
            "trim_galore_quality: 20",
            "trim_galore_stringency: 3",
            "trim_galore_cores: 1",
            "trim_galore_gzip: true",
            "",
            "# Alignment",
            f"hisat2_index: {opts.hisat2_index}",
            "hisat2_threads: 4",
            "samtools_threads: 2",
            "",
            "# Quantification",
            f"featurecounts_annotation: {opts.annotation}",
            "featurecounts_threads: 2",
            "featurecounts_feature_type: exon",
            "featurecounts_attribute_type: gene_id",
            "featurecounts_strandness: 0",
            "featurecounts_paired: false",
            "",
        ]
    )


def write_config_template(path: str | Path, options: ConfigTemplateOptions | None = None, overwrite: bool = False) -> Path:
    output_path = Path(path)
    if output_path.exists() and not overwrite:
        raise FileExistsError(f"config file already exists: {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(build_config_template(options), encoding="utf-8")
    return output_path
