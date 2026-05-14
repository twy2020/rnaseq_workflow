from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from rnaseq_workflow.core.command import run_context_command
from rnaseq_workflow.core.models import RunContext, Sample, SampleLayout, StepResult, StepStatus
from rnaseq_workflow.core.paths import project_paths
from rnaseq_workflow.core.step_state import (
    acquire_lock,
    cleanup_incomplete_output_keep_errors,
    is_step_done,
    release_lock,
    skipped_done_result,
    write_done_marker,
    write_error_log,
)


@dataclass(frozen=True, slots=True)
class TrimGaloreOptions:
    quality: int = 20
    phred: str = "33"
    stringency: int = 3
    gzip_output: bool = True
    cores: int = 1
    paired: bool = False


def build_trim_galore_command(
    fastq_paths: list[str | Path],
    output_dir: str | Path,
    options: TrimGaloreOptions | None = None,
) -> list[str]:
    opts = options or TrimGaloreOptions()
    command = [
        "trim_galore",
        "--quality",
        str(opts.quality),
        "--stringency",
        str(opts.stringency),
        "--cores",
        str(opts.cores),
        "--output_dir",
        str(output_dir),
    ]
    command.append(f"--phred{opts.phred}")
    if opts.paired:
        command.append("--paired")
    if opts.gzip_output:
        command.append("--gzip")
    command.extend(str(path) for path in fastq_paths)
    return command


class TrimGaloreStep:
    step_id = "trim_galore"
    name = "Trim Galore"

    def validate_inputs(self, sample: Sample, context: RunContext) -> None:
        fastq_paths = _fastq_paths(sample)
        if not fastq_paths:
            raise ValueError(f"sample has no FASTQ input: {sample.sample_id}")
        if sample.layout == SampleLayout.PAIRED and len(fastq_paths) != 2:
            raise ValueError(f"paired sample requires exactly two FASTQ files: {sample.sample_id}")
        if not context.dry_run:
            for path in fastq_paths:
                if not path.exists():
                    raise FileNotFoundError(f"FASTQ file not found: {path}")

    def run(self, sample: Sample, context: RunContext) -> StepResult:
        output_dir = project_paths(context.output_dir).trimmed_fastq_dir(sample)
        fastq_paths = _fastq_paths(sample)
        paired = sample.layout == SampleLayout.PAIRED or len(fastq_paths) == 2
        if not bool(context.config.get("force", False)):
            if is_step_done(output_dir):
                return skipped_done_result(sample, self.step_id, output_dir)
            if not context.dry_run and is_trim_galore_output_complete(output_dir, paired=paired):
                outputs = [output_dir, *find_trimmed_fastq_outputs(output_dir)]
                recovered = StepResult(
                    sample_id=sample.sample_id,
                    step_id=self.step_id,
                    status=StepStatus.COMPLETED,
                    message="trim_galore output already complete; recovered done marker",
                    return_code=0,
                    inputs=fastq_paths,
                    outputs=outputs,
                )
                write_done_marker(output_dir, recovered)
                release_lock(output_dir / ".lock")
                return recovered
        options = TrimGaloreOptions(
            quality=int(context.config.get("trim_galore_quality", 20)),
            phred=str(context.config.get("trim_galore_phred", "33")),
            stringency=int(context.config.get("trim_galore_stringency", 3)),
            gzip_output=bool(context.config.get("trim_galore_gzip", True)),
            cores=int(context.config.get("trim_galore_cores", 1)),
            paired=paired,
        )
        lock = acquire_lock(output_dir)
        try:
            command = build_trim_galore_command(fastq_paths, output_dir, options)
            result = run_context_command(command, context, cwd=output_dir)
            status = StepStatus.COMPLETED if result.ok else StepStatus.CANCELLED if result.return_code == 130 else StepStatus.FAILED
            message = "trim_galore completed" if result.ok else _trim_galore_error_summary(result.stderr)
            outputs = [output_dir]
            if not context.dry_run and result.ok:
                outputs.extend(find_trimmed_fastq_outputs(output_dir))
            if not result.ok and not context.dry_run and is_trim_galore_output_complete(output_dir, paired=paired):
                status = StepStatus.COMPLETED
                message = "trim_galore completed; recovered complete output after interrupted command"
                outputs.extend(find_trimmed_fastq_outputs(output_dir))
            step_result = StepResult(
                sample_id=sample.sample_id,
                step_id=self.step_id,
                status=status,
                message=message,
                command=result.command,
                return_code=result.return_code,
                inputs=fastq_paths,
                outputs=outputs,
                extra={
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "duration_seconds": result.duration_seconds,
                    "dry_run": result.dry_run,
                },
            )
            if step_result.status == StepStatus.COMPLETED and not context.dry_run:
                write_done_marker(output_dir, step_result)
            elif bool(context.config.get("cleanup_on_fail", True)):
                write_error_log(output_dir, step_result.message)
                cleanup_incomplete_output_keep_errors(output_dir)
            return step_result
        finally:
            release_lock(lock)

    def apply_cached_result(self, sample: Sample, context: RunContext, record) -> None:
        outputs = [Path(path) for path in getattr(record, "outputs", []) if path]
        output_dir = outputs[0] if outputs else project_paths(context.output_dir).trimmed_fastq_dir(sample)
        trimmed = find_trimmed_fastq_outputs(output_dir)
        if not trimmed:
            return
        sample.source_path = trimmed[0]
        sample.source_paths = trimmed
        sample.layout = SampleLayout.PAIRED if len(trimmed) >= 2 else SampleLayout.SINGLE
        sample.metadata["input_type"] = "fastq"


def find_trimmed_fastq_outputs(output_dir: str | Path) -> list[Path]:
    root = Path(output_dir)
    suffixes = (".fq", ".fastq", ".fq.gz", ".fastq.gz")
    return sorted(path for path in root.glob("*") if path.is_file() and path.name.lower().endswith(suffixes))


def is_trim_galore_output_complete(output_dir: str | Path, paired: bool) -> bool:
    root = Path(output_dir)
    trimmed = find_trimmed_fastq_outputs(root)
    reports = sorted(path for path in root.glob("*trimming_report.txt") if path.is_file())
    expected = 2 if paired else 1
    return len(trimmed) >= expected and len(reports) >= expected and all(path.stat().st_size > 0 for path in trimmed)


def _fastq_paths(sample: Sample) -> list[Path]:
    return [path for path in sample.source_paths if _is_fastq(path)]


def _is_fastq(path: Path) -> bool:
    lower = path.name.lower()
    return lower.endswith((".fastq", ".fq", ".fastq.gz", ".fq.gz"))


def _trim_galore_error_summary(stderr: str) -> str:
    lines = [line.strip() for line in str(stderr or "").splitlines() if line.strip()]
    for line in reversed(lines):
        lower = line.lower()
        if any(token in lower for token in ("failed", "error", "no such file", "cannot", "not found")):
            return line
    return lines[-1] if lines else "trim_galore failed"
