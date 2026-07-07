from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from rnaseq_workflow.core.command import run_context_command
from rnaseq_workflow.core.models import RunContext, Sample, SampleLayout, StepResult, StepStatus
from rnaseq_workflow.core.paths import project_paths


@dataclass(frozen=True, slots=True)
class Hisat2Options:
    index_prefix: Path
    threads: int = 4
    known_splicesite_infile: Path | None = None


def build_hisat2_command(
    fastq_paths: list[str | Path],
    index_prefix: str | Path,
    sam_output: str | Path,
    log_output: str | Path,
    options: Hisat2Options | None = None,
) -> list[str]:
    opts = options or Hisat2Options(index_prefix=Path(index_prefix))
    command = ["hisat2", "-p", str(opts.threads), "-x", str(index_prefix)]
    if len(fastq_paths) == 1:
        command.extend(["-U", str(fastq_paths[0])])
    elif len(fastq_paths) == 2:
        command.extend(["-1", str(fastq_paths[0]), "-2", str(fastq_paths[1])])
    else:
        raise ValueError("HISAT2 requires one FASTQ for single-end or two FASTQ files for paired-end")
    if opts.known_splicesite_infile:
        command.extend(["--known-splicesite-infile", str(opts.known_splicesite_infile)])
    command.extend(["-S", str(sam_output)])
    command.extend(["--summary-file", str(log_output)])
    return command


def build_hisat2_sort_command(
    fastq_paths: list[str | Path],
    index_prefix: str | Path,
    bam_output: str | Path,
    log_output: str | Path,
    options: Hisat2Options | None = None,
    samtools_threads: int = 2,
    index_bam: bool = True,
) -> list[str]:
    opts = options or Hisat2Options(index_prefix=Path(index_prefix))
    if len(fastq_paths) == 1:
        read_args = ' -U "$3"'
        args: list[str | Path] = [opts.threads, index_prefix, fastq_paths[0], log_output, samtools_threads, bam_output]
        splice_offset = 7
    elif len(fastq_paths) == 2:
        read_args = ' -1 "$3" -2 "$4"'
        args = [opts.threads, index_prefix, fastq_paths[0], fastq_paths[1], log_output, samtools_threads, bam_output]
        splice_offset = 8
    else:
        raise ValueError("HISAT2 requires one FASTQ for single-end or two FASTQ files for paired-end")
    splice_arg = ""
    if opts.known_splicesite_infile:
        splice_arg = f' --known-splicesite-infile "${splice_offset}"'
        args.append(opts.known_splicesite_infile)
    log_arg = "$4" if len(fastq_paths) == 1 else "$5"
    sort_threads_arg = "$5" if len(fastq_paths) == 1 else "$6"
    bam_arg = "$6" if len(fastq_paths) == 1 else "$7"
    index_command = f' && samtools index "{bam_arg}"' if index_bam else ""
    script = (
        f'set -o pipefail; hisat2 -p "$1" -x "$2"{read_args}{splice_arg} '
        f'--summary-file "{log_arg}" | samtools sort -@ "{sort_threads_arg}" -o "{bam_arg}" -{index_command}'
    )
    return ["bash", "-lc", script, "rnaseq-hisat2-sort", *[str(arg) for arg in args]]


def hisat2_index_exists(index_prefix: str | Path) -> bool:
    prefix = Path(index_prefix)
    parent = prefix.parent if str(prefix.parent) else Path(".")
    name = prefix.name
    suffixes = [".1.ht2", ".2.ht2", ".3.ht2", ".4.ht2", ".5.ht2", ".6.ht2", ".7.ht2", ".8.ht2"]
    large_suffixes = [suffix + "l" for suffix in suffixes]
    return all((parent / f"{name}{suffix}").exists() for suffix in suffixes) or all(
        (parent / f"{name}{suffix}").exists() for suffix in large_suffixes
    )


class Hisat2AlignStep:
    step_id = "hisat2"
    name = "HISAT2 alignment"

    def validate_inputs(self, sample: Sample, context: RunContext) -> None:
        fastq_paths = _fastq_paths(sample)
        if sample.layout == SampleLayout.PAIRED and len(fastq_paths) != 2:
            raise ValueError(f"paired sample requires exactly two FASTQ files: {sample.sample_id}")
        if len(fastq_paths) not in (1, 2):
            raise ValueError(f"HISAT2 requires one or two FASTQ files: {sample.sample_id}")
        index_prefix = context.config.get("hisat2_index")
        if not index_prefix:
            raise ValueError("hisat2_index is required")
        if not context.dry_run and not hisat2_index_exists(index_prefix):
            raise FileNotFoundError(f"HISAT2 index not found: {index_prefix}")
        if not context.dry_run:
            for path in fastq_paths:
                if not path.exists():
                    raise FileNotFoundError(f"FASTQ file not found: {path}")

    def run(self, sample: Sample, context: RunContext) -> StepResult:
        output_dir = project_paths(context.output_dir).alignment_dir(sample)
        output_dir.mkdir(parents=True, exist_ok=True)
        sam_output = output_dir / f"{sample.sample_id}.sam"
        bam_output = output_dir / f"{sample.sample_id}.sorted.bam"
        index_output = Path(str(bam_output) + ".bai")
        log_output = output_dir / f"{sample.sample_id}.hisat2.log"
        fastq_paths = _fastq_paths(sample)
        index_prefix = Path(context.config["hisat2_index"])
        options = Hisat2Options(
            index_prefix=index_prefix,
            threads=int(context.config.get("hisat2_threads", 4)),
            known_splicesite_infile=Path(context.config["hisat2_splicesites"])
            if context.config.get("hisat2_splicesites")
            else None,
        )
        direct_sort = bool(context.config.get("hisat2_sort_bam", False))
        if direct_sort:
            command = build_hisat2_sort_command(
                fastq_paths,
                index_prefix,
                bam_output,
                log_output,
                options,
                samtools_threads=int(context.config.get("samtools_threads", 2)),
                index_bam=bool(context.config.get("samtools_index", True)),
            )
        else:
            command = build_hisat2_command(fastq_paths, index_prefix, sam_output, log_output, options)
        result = run_context_command(command, context)
        status = StepStatus.COMPLETED if result.ok else StepStatus.FAILED
        message = "hisat2 completed with direct BAM sorting" if result.ok and direct_sort else "hisat2 completed" if result.ok else result.stderr
        outputs = [bam_output, log_output, index_output] if direct_sort else [sam_output, log_output]
        return StepResult(
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
                "direct_bam_sort": direct_sort,
            },
        )


def _fastq_paths(sample: Sample) -> list[Path]:
    return [path for path in sample.source_paths if _is_fastq(path)]


def _is_fastq(path: Path) -> bool:
    lower = path.name.lower()
    return lower.endswith((".fastq", ".fq", ".fastq.gz", ".fq.gz"))
