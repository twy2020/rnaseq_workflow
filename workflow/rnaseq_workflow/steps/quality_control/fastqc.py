from __future__ import annotations

import time
import zipfile
from dataclasses import dataclass
from pathlib import Path

from rnaseq_workflow.core.command import run_context_command
from rnaseq_workflow.core.models import RunContext, Sample, StepResult, StepStatus
from rnaseq_workflow.core.paths import project_paths
from rnaseq_workflow.core.step_state import (
    acquire_lock,
    cleanup_incomplete_output,
    is_step_done,
    release_lock,
    skipped_done_result,
    write_done_marker,
)


@dataclass(frozen=True, slots=True)
class FastQCOptions:
    threads: int = 2
    quiet: bool = True
    extract: bool = False
    completion_grace_seconds: float = 15.0


def build_fastqc_command(
    fastq_paths: list[str | Path],
    output_dir: str | Path,
    options: FastQCOptions | None = None,
) -> list[str]:
    opts = options or FastQCOptions()
    command = ["fastqc", "--threads", str(opts.threads), "--outdir", str(output_dir)]
    if opts.quiet:
        command.append("--quiet")
    if opts.extract:
        command.append("--extract")
    command.extend(str(path) for path in fastq_paths)
    return command


class FastQCStep:
    step_id = "fastqc"
    name = "FastQC"

    def validate_inputs(self, sample: Sample, context: RunContext) -> None:
        fastq_paths = _fastq_paths(sample)
        if not fastq_paths:
            raise ValueError(f"sample has no FASTQ input: {sample.sample_id}")
        if not context.dry_run:
            for path in fastq_paths:
                if not path.exists():
                    raise FileNotFoundError(f"FASTQ file not found: {path}")

    def run(self, sample: Sample, context: RunContext) -> StepResult:
        output_dir = project_paths(context.output_dir).raw_qc_dir(sample)
        if is_step_done(output_dir) and not bool(context.config.get("force", False)):
            return skipped_done_result(sample, self.step_id, output_dir)
        fastq_paths = _fastq_paths(sample)
        options = FastQCOptions(
            threads=int(context.config.get("fastqc_threads", 2)),
            quiet=bool(context.config.get("fastqc_quiet", True)),
            extract=bool(context.config.get("fastqc_extract", False)),
        )
        lock = acquire_lock(output_dir)
        try:
            command = build_fastqc_command(fastq_paths, output_dir, options)
            result = run_context_command(
                command,
                context,
                completion_check=lambda: _fastqc_outputs_complete(
                    fastq_paths,
                    output_dir,
                    stable_seconds=float(context.config.get("fastqc_completion_grace_seconds", options.completion_grace_seconds)),
                ),
                completion_message="fastqc outputs complete; command stopped after output verification",
            )
            status = StepStatus.COMPLETED if result.ok else StepStatus.CANCELLED if result.return_code == 130 else StepStatus.FAILED
            message = "fastqc completed" if result.ok else result.stderr
            step_result = StepResult(
                sample_id=sample.sample_id,
                step_id=self.step_id,
                status=status,
                message=message,
                command=result.command,
                return_code=result.return_code,
                inputs=fastq_paths,
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
            elif bool(context.config.get("cleanup_on_fail", True)):
                cleanup_incomplete_output(output_dir)
            return step_result
        finally:
            release_lock(lock)


def _fastq_paths(sample: Sample) -> list[Path]:
    return [path for path in sample.source_paths if _is_fastq(path)]


def _is_fastq(path: Path) -> bool:
    lower = path.name.lower()
    return lower.endswith((".fastq", ".fq", ".fastq.gz", ".fq.gz"))


def _fastqc_outputs_complete(fastq_paths: list[Path], output_dir: Path, stable_seconds: float = 15.0) -> bool:
    if not fastq_paths or not output_dir.exists():
        return False
    now = time.time()
    for fastq in fastq_paths:
        stem = _fastqc_output_stem(fastq)
        html = output_dir / f"{stem}_fastqc.html"
        archive = output_dir / f"{stem}_fastqc.zip"
        if not html.is_file() or not archive.is_file():
            return False
        try:
            if html.stat().st_size <= 0 or archive.stat().st_size <= 0:
                return False
            newest = max(html.stat().st_mtime, archive.stat().st_mtime)
        except OSError:
            return False
        if stable_seconds > 0 and now - newest < stable_seconds:
            return False
        try:
            with zipfile.ZipFile(archive) as handle:
                if handle.testzip() is not None:
                    return False
        except zipfile.BadZipFile:
            return False
    return True


def _fastqc_output_stem(path: Path) -> str:
    name = path.name
    for suffix in (".fastq.gz", ".fq.gz", ".fastq", ".fq"):
        if name.lower().endswith(suffix):
            return name[: -len(suffix)]
    return path.stem
