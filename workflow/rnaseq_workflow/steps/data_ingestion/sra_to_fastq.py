from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from rnaseq_workflow.core.command import run_context_command
from rnaseq_workflow.core.models import RunContext, Sample, SampleLayout, StepResult, StepStatus
from rnaseq_workflow.core.paths import project_paths
from rnaseq_workflow.core.step_state import (
    acquire_lock,
    cleanup_incomplete_output,
    is_step_done,
    release_lock,
    skipped_done_result,
    write_done_marker,
)
from rnaseq_workflow.steps.data_ingestion.scanner import infer_fastq_layout


@dataclass(frozen=True, slots=True)
class SraToFastqOptions:
    threads: int = 4
    split_files: bool = True
    include_progress: bool = False
    temp_dir: Path | None = None


def build_fasterq_dump_command(
    sra_path: str | Path,
    output_dir: str | Path,
    options: SraToFastqOptions | None = None,
) -> list[str]:
    opts = options or SraToFastqOptions()
    command = [
        "fasterq-dump",
        str(sra_path),
        "--outdir",
        str(output_dir),
        "--threads",
        str(opts.threads),
    ]
    if opts.split_files:
        command.append("--split-files")
    if opts.include_progress:
        command.append("--progress")
    if opts.temp_dir:
        command.extend(["--temp", str(opts.temp_dir)])
    return command


class SraToFastqStep:
    step_id = "sra_to_fastq"
    name = "SRA to FASTQ"

    def validate_inputs(self, sample: Sample, context: RunContext) -> None:
        for path in sample.source_paths:
            if path.suffix.lower() == ".sra" and not context.dry_run and not path.exists():
                raise FileNotFoundError(f"SRA file not found: {path}")

    def run(self, sample: Sample, context: RunContext) -> StepResult:
        output_dir = project_paths(context.output_dir).raw_fastq_dir(sample)
        if is_step_done(output_dir) and not bool(context.config.get("force", False)):
            _attach_generated_fastqs(sample, output_dir)
            return skipped_done_result(sample, self.step_id, output_dir)

        sra_paths = [path for path in sample.source_paths if path.suffix.lower() == ".sra"]
        if not sra_paths:
            return StepResult(
                sample_id=sample.sample_id,
                step_id=self.step_id,
                status=StepStatus.SKIPPED,
                message="sample has no SRA input",
            )

        lock = acquire_lock(output_dir)
        options = SraToFastqOptions(
            threads=int(context.config.get("fasterq_dump_threads", 4)),
            split_files=bool(context.config.get("fasterq_dump_split_files", True)),
            include_progress=bool(context.config.get("fasterq_dump_progress", False)),
            temp_dir=output_dir / "_fasterq_tmp",
        )
        try:
            command = build_fasterq_dump_command(sra_paths[0], output_dir, options)
            result = run_context_command(command, context)
            status = StepStatus.COMPLETED if result.ok else StepStatus.CANCELLED if result.return_code == 130 else StepStatus.FAILED
            message = "fasterq-dump completed" if result.ok else result.stderr
            step_result = StepResult(
                sample_id=sample.sample_id,
                step_id=self.step_id,
                status=status,
                message=message,
                command=result.command,
                return_code=result.return_code,
                inputs=sra_paths,
                outputs=[output_dir],
                extra={
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "duration_seconds": result.duration_seconds,
                    "dry_run": result.dry_run,
                },
            )
            if step_result.status == StepStatus.COMPLETED and not context.dry_run:
                write_done_marker(output_dir, step_result)
                _attach_generated_fastqs(sample, output_dir)
            elif bool(context.config.get("cleanup_on_fail", True)):
                cleanup_incomplete_output(output_dir)
            return step_result
        finally:
            release_lock(lock)

    def apply_cached_result(self, sample: Sample, context: RunContext, record) -> None:
        outputs = [Path(path) for path in getattr(record, "outputs", []) if path]
        output_dir = outputs[0] if outputs else project_paths(context.output_dir).raw_fastq_dir(sample)
        _attach_generated_fastqs(sample, output_dir)


def _attach_generated_fastqs(sample: Sample, output_dir: Path) -> None:
    fastqs = sorted(path for path in output_dir.rglob("*") if path.is_file() and _is_fastq(path))
    if not fastqs:
        return
    sample.source_path = fastqs[0]
    sample.source_paths = fastqs
    sample.layout = infer_fastq_layout(fastqs)
    sample.metadata["input_type"] = "fastq"
    sample.metadata["converted_from_sra"] = "true"


def _is_fastq(path: Path) -> bool:
    return path.name.lower().endswith((".fastq", ".fq", ".fastq.gz", ".fq.gz"))
