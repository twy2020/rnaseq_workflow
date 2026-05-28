from __future__ import annotations

import time
import zipfile
from dataclasses import dataclass
from enum import Enum
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


class TrimmedFastQCPolicy(str, Enum):
    RUN_KEEP = "run_keep"
    PAUSE_ON_FAIL = "pause_on_fail"
    DISABLED = "disabled"


@dataclass(frozen=True, slots=True)
class FastQCSummaryIssue:
    file: str
    status: str
    module: str
    sequence: str


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
    output_kind = "raw"

    def validate_inputs(self, sample: Sample, context: RunContext) -> None:
        fastq_paths = self._fastq_paths(sample, context)
        if not fastq_paths:
            raise ValueError(f"sample has no FASTQ input: {sample.sample_id}")
        if not context.dry_run:
            for path in fastq_paths:
                if not path.exists():
                    raise FileNotFoundError(f"FASTQ file not found: {path}")

    def run(self, sample: Sample, context: RunContext) -> StepResult:
        output_dir = self._output_dir(sample, context)
        if is_step_done(output_dir) and not bool(context.config.get("force", False)):
            return skipped_done_result(sample, self.step_id, output_dir)
        fastq_paths = self._fastq_paths(sample, context)
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
            issues = summarize_fastqc_issues(fastq_paths, output_dir) if result.ok and not context.dry_run else []
            step_result = StepResult(
                sample_id=sample.sample_id,
                step_id=self.step_id,
                status=self._status_after_quality_check(status, issues),
                message=self._message_after_quality_check(message, issues),
                command=result.command,
                return_code=result.return_code,
                inputs=fastq_paths,
                outputs=[output_dir],
                extra={
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "duration_seconds": result.duration_seconds,
                    "dry_run": result.dry_run,
                    "fastqc_output_kind": self.output_kind,
                    "fastqc_quality_ok": not issues,
                    "fastqc_issues": [_issue_to_dict(issue) for issue in issues],
                    "quality_policy": self._quality_policy(context).value,
                },
            )
            if step_result.status in {StepStatus.COMPLETED, StepStatus.PAUSED} and not context.dry_run:
                write_done_marker(output_dir, step_result)
            elif bool(context.config.get("cleanup_on_fail", True)):
                cleanup_incomplete_output(output_dir)
            return step_result
        finally:
            release_lock(lock)

    def _output_dir(self, sample: Sample, context: RunContext) -> Path:
        return project_paths(context.output_dir).raw_qc_dir(sample)

    def _fastq_paths(self, sample: Sample, context: RunContext) -> list[Path]:
        return _fastq_paths(sample)

    def _quality_policy(self, context: RunContext) -> TrimmedFastQCPolicy:
        return TrimmedFastQCPolicy.RUN_KEEP

    def _status_after_quality_check(self, status: StepStatus, issues: list[FastQCSummaryIssue]) -> StepStatus:
        return status

    def _message_after_quality_check(self, message: str, issues: list[FastQCSummaryIssue]) -> str:
        return message


class TrimmedFastQCStep(FastQCStep):
    step_id = "fastqc_trimmed"
    name = "FastQC after trimming"
    output_kind = "trimmed"

    def validate_inputs(self, sample: Sample, context: RunContext) -> None:
        if self._quality_policy(context) == TrimmedFastQCPolicy.DISABLED:
            return
        super().validate_inputs(sample, context)

    def run(self, sample: Sample, context: RunContext) -> StepResult:
        policy = self._quality_policy(context)
        if policy == TrimmedFastQCPolicy.DISABLED:
            return StepResult(
                sample_id=sample.sample_id,
                step_id=self.step_id,
                status=StepStatus.SKIPPED,
                message="trimmed FastQC disabled by policy",
                inputs=sample.source_paths,
                outputs=[self._output_dir(sample, context)],
                extra={
                    "fastqc_output_kind": self.output_kind,
                    "quality_policy": policy.value,
                    "fastqc_quality_ok": None,
                    "fastqc_issues": [],
                },
            )
        return super().run(sample, context)

    def _output_dir(self, sample: Sample, context: RunContext) -> Path:
        return project_paths(context.output_dir).trimmed_qc_dir(sample)

    def _fastq_paths(self, sample: Sample, context: RunContext) -> list[Path]:
        trimmed_dirs = []
        metadata_dir = sample.metadata.get("trimmed_fastq_dir")
        if metadata_dir:
            trimmed_dirs.append(Path(str(metadata_dir)))
        trimmed_dirs.append(project_paths(context.output_dir).trimmed_fastq_dir(sample))
        for trimmed_dir in trimmed_dirs:
            trimmed = _fastqs_in_dir(trimmed_dir)
            if trimmed:
                return trimmed
        return _fastq_paths(sample)

    def _status_after_quality_check(self, status: StepStatus, issues: list[FastQCSummaryIssue]) -> StepStatus:
        if status == StepStatus.COMPLETED and issues and self._last_policy == TrimmedFastQCPolicy.PAUSE_ON_FAIL:
            return StepStatus.PAUSED
        return status

    def _message_after_quality_check(self, message: str, issues: list[FastQCSummaryIssue]) -> str:
        if not issues:
            return message
        summary = _issue_summary(issues)
        if self._last_policy == TrimmedFastQCPolicy.PAUSE_ON_FAIL:
            return f"trimmed FastQC found quality issues; sample paused for manual review: {summary}"
        return f"trimmed FastQC completed with quality warnings/failures: {summary}"

    @property
    def _last_policy(self) -> TrimmedFastQCPolicy:
        return getattr(self, "__last_policy", TrimmedFastQCPolicy.RUN_KEEP)

    @_last_policy.setter
    def _last_policy(self, value: TrimmedFastQCPolicy) -> None:
        setattr(self, "__last_policy", value)

    def _quality_policy(self, context: RunContext) -> TrimmedFastQCPolicy:
        raw = str(context.config.get("trimmed_fastqc_policy", TrimmedFastQCPolicy.RUN_KEEP.value)).strip().lower()
        aliases = {
            "run": TrimmedFastQCPolicy.RUN_KEEP.value,
            "keep": TrimmedFastQCPolicy.RUN_KEEP.value,
            "run_keep": TrimmedFastQCPolicy.RUN_KEEP.value,
            "pause": TrimmedFastQCPolicy.PAUSE_ON_FAIL.value,
            "pause_on_fail": TrimmedFastQCPolicy.PAUSE_ON_FAIL.value,
            "skip_sample": TrimmedFastQCPolicy.PAUSE_ON_FAIL.value,
            "disabled": TrimmedFastQCPolicy.DISABLED.value,
            "disable": TrimmedFastQCPolicy.DISABLED.value,
            "none": TrimmedFastQCPolicy.DISABLED.value,
            "off": TrimmedFastQCPolicy.DISABLED.value,
        }
        try:
            policy = TrimmedFastQCPolicy(aliases.get(raw, raw))
        except ValueError:
            policy = TrimmedFastQCPolicy.RUN_KEEP
        self._last_policy = policy
        return policy


def _fastq_paths(sample: Sample) -> list[Path]:
    return [path for path in sample.source_paths if _is_fastq(path)]


def _fastqs_in_dir(path: Path) -> list[Path]:
    if not path.exists():
        return []
    return sorted(candidate for candidate in path.glob("*") if candidate.is_file() and _is_fastq(candidate))


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


def summarize_fastqc_issues(fastq_paths: list[Path], output_dir: Path) -> list[FastQCSummaryIssue]:
    issues: list[FastQCSummaryIssue] = []
    for fastq in fastq_paths:
        stem = _fastqc_output_stem(fastq)
        archive_path = output_dir / f"{stem}_fastqc.zip"
        if not archive_path.is_file():
            continue
        try:
            with zipfile.ZipFile(archive_path) as archive:
                summary_name = _summary_member_name(archive, stem)
                if summary_name is None:
                    continue
                raw = archive.read(summary_name).decode("utf-8", errors="replace")
        except (OSError, zipfile.BadZipFile, KeyError):
            continue
        for line in raw.splitlines():
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            status, module, sequence = parts[0].strip(), parts[1].strip(), parts[2].strip()
            if status.upper() in {"WARN", "FAIL"}:
                issues.append(FastQCSummaryIssue(file=archive_path.name, status=status.upper(), module=module, sequence=sequence))
    return issues


def _summary_member_name(archive: zipfile.ZipFile, stem: str) -> str | None:
    preferred = f"{stem}_fastqc/summary.txt"
    names = archive.namelist()
    if preferred in names:
        return preferred
    for name in names:
        if name.endswith("/summary.txt") or name == "summary.txt":
            return name
    return None


def _issue_summary(issues: list[FastQCSummaryIssue], limit: int = 5) -> str:
    shown = issues[:limit]
    text = "; ".join(f"{item.file}:{item.status}:{item.module}" for item in shown)
    remaining = len(issues) - len(shown)
    if remaining > 0:
        text += f"; +{remaining} more"
    return text


def _issue_to_dict(issue: FastQCSummaryIssue) -> dict[str, str]:
    return {
        "file": issue.file,
        "status": issue.status,
        "module": issue.module,
        "sequence": issue.sequence,
    }
