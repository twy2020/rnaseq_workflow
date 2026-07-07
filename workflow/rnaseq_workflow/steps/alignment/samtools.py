from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from rnaseq_workflow.core.command import run_context_command
from rnaseq_workflow.core.models import RunContext, Sample, StepResult, StepStatus
from rnaseq_workflow.core.paths import project_paths


@dataclass(frozen=True, slots=True)
class SamtoolsSortOptions:
    threads: int = 2


def build_samtools_sort_command(
    sam_input: str | Path,
    bam_output: str | Path,
    options: SamtoolsSortOptions | None = None,
) -> list[str]:
    opts = options or SamtoolsSortOptions()
    return ["samtools", "sort", "-@", str(opts.threads), "-o", str(bam_output), str(sam_input)]


def build_samtools_index_command(bam_input: str | Path) -> list[str]:
    return ["samtools", "index", str(bam_input)]


class SamtoolsSortStep:
    step_id = "samtools_sort"
    name = "samtools sort"

    def validate_inputs(self, sample: Sample, context: RunContext) -> None:
        sam_input = _sam_path(sample, context)
        bam_output = project_paths(context.output_dir).alignment_dir(sample) / f"{sample.sample_id}.sorted.bam"
        index_output = Path(str(bam_output) + ".bai")
        if (
            bool(context.config.get("hisat2_sort_bam", False))
            and not context.dry_run
            and bam_output.exists()
            and bam_output.stat().st_size > 0
            and (not context.config.get("samtools_index", True) or (index_output.exists() and index_output.stat().st_size > 0))
        ):
            return
        if not context.dry_run and not sam_input.exists():
            raise FileNotFoundError(f"SAM file not found: {sam_input}")

    def run(self, sample: Sample, context: RunContext) -> StepResult:
        output_dir = project_paths(context.output_dir).alignment_dir(sample)
        output_dir.mkdir(parents=True, exist_ok=True)
        sam_input = _sam_path(sample, context)
        bam_output = output_dir / f"{sample.sample_id}.sorted.bam"
        index_output = Path(str(bam_output) + ".bai")
        if (
            context.config.get("skip_completed", True)
            and not context.dry_run
            and bam_output.exists()
            and bam_output.stat().st_size > 0
            and (not context.config.get("samtools_index", True) or (index_output.exists() and index_output.stat().st_size > 0))
        ):
            return StepResult(
                sample_id=sample.sample_id,
                step_id=self.step_id,
                status=StepStatus.SKIPPED,
                message="samtools outputs already exist",
                inputs=[sam_input],
                outputs=[bam_output, index_output],
                extra={"dry_run": False, "skipped_existing": True},
            )
        options = SamtoolsSortOptions(threads=int(context.config.get("samtools_threads", 2)))
        command = build_samtools_sort_command(sam_input, bam_output, options)
        result = run_context_command(command, context)
        index_result = None
        if result.ok and not result.dry_run and context.config.get("samtools_index", True):
            index_result = run_context_command(build_samtools_index_command(bam_output), context)
            if not index_result.ok:
                result = index_result
        status = StepStatus.COMPLETED if result.ok else StepStatus.FAILED
        message = "samtools sort completed" if result.ok else result.stderr
        return StepResult(
            sample_id=sample.sample_id,
            step_id=self.step_id,
            status=status,
            message=message,
            command=result.command,
            return_code=result.return_code,
            inputs=[sam_input],
            outputs=[bam_output, index_output],
            extra={
                "stdout": result.stdout,
                "stderr": result.stderr,
                "duration_seconds": result.duration_seconds,
                "dry_run": result.dry_run,
                "index_command": index_result.command if index_result else None,
                "index_return_code": index_result.return_code if index_result else None,
            },
        )

    def apply_cached_result(self, sample: Sample, context: RunContext, record) -> None:
        outputs = [Path(path) for path in getattr(record, "outputs", []) if path]
        bam = next((path for path in outputs if path.name.lower().endswith(".bam")), None)
        if bam is None:
            bam = project_paths(context.output_dir).alignment_dir(sample) / f"{sample.sample_id}.sorted.bam"
        if bam.exists():
            sample.source_path = bam
            sample.source_paths = [bam]


def _sam_path(sample: Sample, context: RunContext) -> Path:
    configured = context.config.get("sam_input")
    if configured:
        return Path(configured)
    for path in sample.source_paths:
        if path.name.lower().endswith(".sam"):
            return path
    if sample.source_path.name.lower().endswith(".sam"):
        return sample.source_path
    return project_paths(context.output_dir).alignment_dir(sample) / f"{sample.sample_id}.sam"
