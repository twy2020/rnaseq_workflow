from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from rnaseq_workflow.core.command import run_context_command
from rnaseq_workflow.core.models import RunContext, Sample, StepResult, StepStatus
from rnaseq_workflow.core.paths import project_paths


@dataclass(frozen=True, slots=True)
class StringTieOptions:
    annotation_path: Path
    threads: int = 2
    estimate_only: bool = True
    gene_abundance: bool = True


def build_stringtie_command(
    bam_path: str | Path,
    annotation_path: str | Path,
    output_gtf: str | Path,
    gene_abundance_path: str | Path | None = None,
    options: StringTieOptions | None = None,
) -> list[str]:
    opts = options or StringTieOptions(annotation_path=Path(annotation_path))
    command = [
        "stringtie",
        str(bam_path),
        "-p",
        str(opts.threads),
        "-G",
        str(annotation_path),
        "-o",
        str(output_gtf),
    ]
    if opts.estimate_only:
        command.append("-e")
    if opts.gene_abundance and gene_abundance_path is not None:
        command.extend(["-A", str(gene_abundance_path)])
    return command


class StringTieStep:
    step_id = "stringtie"
    name = "StringTie"

    def validate_inputs(self, sample: Sample, context: RunContext) -> None:
        annotation_path = context.config.get("stringtie_annotation") or context.config.get("featurecounts_annotation")
        if not annotation_path:
            raise ValueError("stringtie_annotation or featurecounts_annotation is required")
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
        annotation_path = Path(context.config.get("stringtie_annotation") or context.config["featurecounts_annotation"])
        output_gtf = output_dir / f"{sample.sample_id}.stringtie.gtf"
        abundance_path = output_dir / f"{sample.sample_id}.stringtie.gene_abund.tsv"
        options = StringTieOptions(
            annotation_path=annotation_path,
            threads=int(context.config.get("stringtie_threads", 2)),
            estimate_only=bool(context.config.get("stringtie_estimate_only", True)),
            gene_abundance=bool(context.config.get("stringtie_gene_abundance", True)),
        )
        command = build_stringtie_command(bam_path, annotation_path, output_gtf, abundance_path, options)
        result = run_context_command(command, context)
        status = StepStatus.COMPLETED if result.ok else StepStatus.FAILED
        message = "StringTie completed" if result.ok else result.stderr
        return StepResult(
            sample_id=sample.sample_id,
            step_id=self.step_id,
            status=status,
            message=message,
            command=result.command,
            return_code=result.return_code,
            inputs=[bam_path, annotation_path],
            outputs=[output_gtf, abundance_path],
            extra={
                "stdout": result.stdout,
                "stderr": result.stderr,
                "duration_seconds": result.duration_seconds,
                "dry_run": result.dry_run,
            },
        )


def _bam_path(sample: Sample, context: RunContext) -> Path:
    configured = context.config.get("stringtie_bam")
    if configured:
        return Path(configured)
    if sample.source_path and str(sample.source_path).lower().endswith(".bam"):
        return Path(sample.source_path)
    return project_paths(context.output_dir).alignment_dir(sample) / f"{sample.sample_id}.sorted.bam"
