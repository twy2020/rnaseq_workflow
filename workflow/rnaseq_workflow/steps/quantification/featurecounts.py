from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from rnaseq_workflow.core.command import run_context_command
from rnaseq_workflow.core.models import RunContext, Sample, StepResult, StepStatus
from rnaseq_workflow.core.paths import project_paths


@dataclass(frozen=True, slots=True)
class FeatureCountsOptions:
    annotation_path: Path
    threads: int = 2
    feature_type: str = "exon"
    attribute_type: str = "gene_id"
    paired: bool = False
    strandness: int = 0


def build_featurecounts_command(
    bam_paths: list[str | Path],
    annotation_path: str | Path,
    output_path: str | Path,
    options: FeatureCountsOptions | None = None,
) -> list[str]:
    opts = options or FeatureCountsOptions(annotation_path=Path(annotation_path))
    if not bam_paths:
        raise ValueError("featureCounts requires at least one BAM file")
    command = [
        "featureCounts",
        "-T",
        str(opts.threads),
        "-a",
        str(annotation_path),
        "-o",
        str(output_path),
        "-t",
        opts.feature_type,
        "-g",
        opts.attribute_type,
        "-s",
        str(opts.strandness),
    ]
    if opts.paired:
        command.append("-p")
    command.extend(str(path) for path in bam_paths)
    return command


class FeatureCountsStep:
    step_id = "featurecounts"
    name = "featureCounts"

    def validate_inputs(self, sample: Sample, context: RunContext) -> None:
        annotation_path = context.config.get("featurecounts_annotation")
        if not annotation_path:
            raise ValueError("featurecounts_annotation is required")
        bam_path = _bam_path(sample, context)
        if not context.dry_run:
            if not Path(annotation_path).exists():
                raise FileNotFoundError(f"annotation file not found: {annotation_path}")
            if not bam_path.exists():
                raise FileNotFoundError(f"BAM file not found: {bam_path}")

    def run(self, sample: Sample, context: RunContext) -> StepResult:
        output_dir = project_paths(context.output_dir).quantification_dir(sample)
        output_dir.mkdir(parents=True, exist_ok=True)
        bam_path = _bam_path(sample, context)
        output_path = output_dir / f"{sample.sample_id}.featureCounts.txt"
        annotation_path = Path(context.config["featurecounts_annotation"])
        options = FeatureCountsOptions(
            annotation_path=annotation_path,
            threads=int(context.config.get("featurecounts_threads", 2)),
            feature_type=str(context.config.get("featurecounts_feature_type", "exon")),
            attribute_type=str(context.config.get("featurecounts_attribute_type", "gene_id")),
            paired=bool(context.config.get("featurecounts_paired", False)),
            strandness=int(context.config.get("featurecounts_strandness", 0)),
        )
        command = build_featurecounts_command([bam_path], annotation_path, output_path, options)
        result = run_context_command(command, context)
        status = StepStatus.COMPLETED if result.ok else StepStatus.FAILED
        message = "featureCounts completed" if result.ok else result.stderr
        return StepResult(
            sample_id=sample.sample_id,
            step_id=self.step_id,
            status=status,
            message=message,
            command=result.command,
            return_code=result.return_code,
            inputs=[bam_path, annotation_path],
            outputs=[output_path, Path(f"{output_path}.summary")],
            extra={
                "stdout": result.stdout,
                "stderr": result.stderr,
                "duration_seconds": result.duration_seconds,
                "dry_run": result.dry_run,
            },
        )


def _bam_path(sample: Sample, context: RunContext) -> Path:
    configured = context.config.get("featurecounts_bam")
    if configured:
        return Path(configured)
    if sample.source_path and str(sample.source_path).lower().endswith(".bam"):
        return Path(sample.source_path)
    return project_paths(context.output_dir).alignment_dir(sample) / f"{sample.sample_id}.sorted.bam"
