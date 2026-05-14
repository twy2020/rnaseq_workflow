from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from filelock import FileLock

from rnaseq_workflow.core.cancellation import CancellationToken
from rnaseq_workflow.core.command import build_docker_command
from rnaseq_workflow.core.models import StepStatus
from rnaseq_workflow.steps.download.cache import (
    accession_size,
    accession_dir,
    cleanup_accession_artifacts,
    cleanup_stale_sra_locks,
    directory_size,
    find_cached_sra,
    lock_path,
)
from rnaseq_workflow.steps.download.models import DownloadProgress, DownloadRequest, DownloadResult

ProgressCallback = Callable[[DownloadProgress], None]


@dataclass(frozen=True, slots=True)
class _ValidationResult:
    ok: bool
    command: list[str]
    return_code: int | None
    message: str


def build_prefetch_command(
    accession: str,
    output_dir: str | Path,
    max_size: str | None = None,
    transport: str | None = None,
    force: bool = False,
) -> list[str]:
    command = ["prefetch", accession, "--output-directory", str(output_dir)]
    if max_size:
        command.extend(["--max-size", max_size])
    if transport:
        command.extend(["--transport", transport])
    if force:
        command.extend(["--force", "yes"])
    return command


def validate_sra_accession(accession: str) -> None:
    if not re.fullmatch(r"[SED]RR\d+", accession, flags=re.IGNORECASE):
        raise ValueError(f"unsupported SRA run accession: {accession}")


class PrefetchDownloader:
    def __init__(
        self,
        max_size: str | None = None,
        transport: str | None = None,
        force: bool = False,
        execution_mode: str = "local",
        docker_image: str = "rnaseq-workflow:tools",
        docker_workspace: str | Path = ".",
        resume_partial: bool = True,
        poll_interval_seconds: float = 1.0,
        retries: int = 0,
        retry_delay_seconds: float = 5.0,
        timeout_seconds: float | None = None,
        cleanup_on_fail: bool = True,
        clean_before_download: bool = False,
        validate_after_download: bool = True,
    ) -> None:
        self.max_size = max_size
        self.transport = transport
        self.force = force
        self.execution_mode = execution_mode
        self.docker_image = docker_image
        self.docker_workspace = Path(docker_workspace)
        self.resume_partial = resume_partial
        self.poll_interval_seconds = poll_interval_seconds
        self.retries = retries
        self.retry_delay_seconds = retry_delay_seconds
        self.timeout_seconds = timeout_seconds
        self.cleanup_on_fail = cleanup_on_fail
        self.clean_before_download = clean_before_download
        self.validate_after_download = validate_after_download
        self._validated_paths: set[tuple[str, int, float]] = set()

    def download(
        self,
        request: DownloadRequest,
        dry_run: bool = False,
        progress_callback: ProgressCallback | None = None,
        cancellation_token: CancellationToken | None = None,
    ) -> DownloadResult:
        validate_sra_accession(request.accession)
        request.output_dir.mkdir(parents=True, exist_ok=True)
        accession_dir(request.accession, request.output_dir).mkdir(parents=True, exist_ok=True)

        native_command = build_prefetch_command(
            request.accession,
            request.output_dir,
            max_size=self.max_size,
            transport=self.transport,
            force=self.force,
        )
        command = self._runtime_command(native_command)
        if dry_run:
            if progress_callback:
                progress_callback(
                    DownloadProgress(
                        accession=request.accession,
                        status=StepStatus.COMPLETED,
                        message="dry-run prefetch command built",
                    )
                )
            return DownloadResult(
                accession=request.accession,
                status=StepStatus.COMPLETED,
                command=command,
                return_code=0,
                message="dry-run prefetch command built",
            )

        with FileLock(str(lock_path(request.accession, request.output_dir))):
            if self.clean_before_download:
                cleanup_accession_artifacts(request.accession, request.output_dir)
            elif self.resume_partial:
                cleanup_stale_sra_locks(request.accession, request.output_dir)

            cached = None if self.force else find_cached_sra(request.accession, request.output_dir)
            if cached:
                if self._validation_marker_is_current(cached) or self._validation_seen_in_process(cached):
                    progress = DownloadProgress(
                        accession=request.accession,
                        status=StepStatus.SKIPPED,
                        downloaded_bytes=cached.stat().st_size,
                        expected_size_bytes=request.expected_size_bytes,
                        percent=100.0,
                        message="cached SRA file found; validation already passed",
                        local_path=cached,
                    )
                    if progress_callback:
                        progress_callback(progress)
                    return DownloadResult(
                        accession=request.accession,
                        status=StepStatus.SKIPPED,
                        local_path=cached,
                        command=command,
                        message="cached SRA file found; validation already passed",
                        cached=True,
                        downloaded_bytes=cached.stat().st_size,
                    )
                if progress_callback:
                    progress_callback(
                        DownloadProgress(
                            accession=request.accession,
                            status=StepStatus.RUNNING,
                            downloaded_bytes=cached.stat().st_size,
                            expected_size_bytes=request.expected_size_bytes,
                            percent=100.0,
                            message="验证 SRA 完整性",
                            local_path=cached,
                        )
                    )
                validation = self._validate_sra_file(cached, progress_callback=progress_callback, request=request, cancellation_token=cancellation_token)
                if validation is not None and not validation.ok:
                    if _validation_cancelled(validation):
                        return DownloadResult(
                            accession=request.accession,
                            status=StepStatus.CANCELLED,
                            local_path=cached,
                            command=validation.command,
                            return_code=validation.return_code,
                            message="SRA validation cancelled; cached SRA preserved",
                            cached=True,
                            downloaded_bytes=cached.stat().st_size,
                        )
                    if self.cleanup_on_fail:
                        cleanup_accession_artifacts(request.accession, request.output_dir)
                    if progress_callback:
                        progress_callback(
                            DownloadProgress(
                                accession=request.accession,
                                status=StepStatus.RUNNING,
                                downloaded_bytes=0,
                                expected_size_bytes=request.expected_size_bytes,
                                percent=0.0,
                                message=f"缓存校验失败，已清理，重新下载: {_compact_message(validation.message)}",
                            )
                    )
                    return self._run_with_retries(command, request, progress_callback, cancellation_token)
                if validation is not None and _validation_passed(validation):
                    self._write_validation_marker(cached, validation)
                    self._remember_validated_path(cached)
                progress = DownloadProgress(
                    accession=request.accession,
                    status=StepStatus.SKIPPED,
                    downloaded_bytes=cached.stat().st_size,
                    expected_size_bytes=request.expected_size_bytes,
                    percent=100.0,
                    message="cached SRA file found",
                    local_path=cached,
                )
                if progress_callback:
                    progress_callback(progress)
                return DownloadResult(
                    accession=request.accession,
                    status=StepStatus.SKIPPED,
                    local_path=cached,
                    command=command,
                    message="cached SRA file found",
                    cached=True,
                    downloaded_bytes=cached.stat().st_size,
                )

            return self._run_with_retries(command, request, progress_callback, cancellation_token)

    def _runtime_command(self, command: list[str]) -> list[str]:
        if self.execution_mode.lower() in {"docker", "container"}:
            return build_docker_command(command, image=self.docker_image, workspace=self.docker_workspace)
        return command

    def _run_with_retries(
        self,
        command: list[str],
        request: DownloadRequest,
        progress_callback: ProgressCallback | None,
        cancellation_token: CancellationToken | None,
    ) -> DownloadResult:
        last_result: DownloadResult | None = None
        attempts = max(self.retries, 0) + 1
        for attempt in range(1, attempts + 1):
            if cancellation_token and cancellation_token.is_cancelled():
                return _cancelled_result(request, command, "cancelled before download")
            result = self._run_prefetch(command, request, progress_callback, cancellation_token, attempt)
            if result.status == StepStatus.COMPLETED or result.status == StepStatus.CANCELLED:
                return result
            last_result = result
            if attempt < attempts:
                time.sleep(self.retry_delay_seconds)
        return last_result or DownloadResult(
            accession=request.accession,
            status=StepStatus.FAILED,
            command=command,
            message="download failed without result",
        )

    def _run_prefetch(
        self,
        command: list[str],
        request: DownloadRequest,
        progress_callback: ProgressCallback | None,
        cancellation_token: CancellationToken | None,
        attempt: int,
    ) -> DownloadResult:
        start_time = time.monotonic()
        previous_time = start_time
        previous_size = accession_size(request.accession, request.output_dir)
        try:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        except FileNotFoundError as exc:
            message = _missing_command_message(command, self.execution_mode, exc)
            if progress_callback:
                progress_callback(
                    DownloadProgress(
                        accession=request.accession,
                        status=StepStatus.FAILED,
                        downloaded_bytes=accession_size(request.accession, request.output_dir),
                        expected_size_bytes=request.expected_size_bytes,
                        message=message,
                    )
                )
            return DownloadResult(
                accession=request.accession,
                status=StepStatus.FAILED,
                command=command,
                return_code=None,
                message=message,
                downloaded_bytes=accession_size(request.accession, request.output_dir),
            )

        while process.poll() is None:
            time.sleep(self.poll_interval_seconds)
            now = time.monotonic()
            if cancellation_token and cancellation_token.is_cancelled():
                process.terminate()
                try:
                    stdout, stderr = process.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    stdout, stderr = process.communicate()
                if self.cleanup_on_fail and not self.resume_partial:
                    cleanup_accession_artifacts(request.accession, request.output_dir)
                return self._finalize_interrupted(
                    request,
                    command,
                    StepStatus.CANCELLED,
                    "download cancelled",
                    stdout,
                    stderr,
                    start_time,
                    progress_callback,
                )
            if self.timeout_seconds is not None and now - start_time > self.timeout_seconds:
                process.terminate()
                stdout, stderr = process.communicate()
                if self.cleanup_on_fail and not self.resume_partial:
                    cleanup_accession_artifacts(request.accession, request.output_dir)
                return self._finalize_interrupted(
                    request,
                    command,
                    StepStatus.FAILED,
                    f"download timed out after {self.timeout_seconds} seconds",
                    stdout,
                    stderr,
                    start_time,
                    progress_callback,
                )
            current_size = accession_size(request.accession, request.output_dir)
            delta_time = max(now - previous_time, 0.001)
            speed = max(current_size - previous_size, 0) / delta_time
            percent = _percent(current_size, request.expected_size_bytes)
            if progress_callback:
                progress_callback(
                    DownloadProgress(
                        accession=request.accession,
                        status=StepStatus.RUNNING,
                        downloaded_bytes=current_size,
                        expected_size_bytes=request.expected_size_bytes,
                        speed_bps=speed,
                        percent=percent,
                        message=f"downloading attempt {attempt}",
                    )
                )
            previous_time = now
            previous_size = current_size

        stdout, stderr = process.communicate()
        local_path = find_cached_sra(request.accession, request.output_dir)
        final_size = local_path.stat().st_size if local_path else accession_size(request.accession, request.output_dir)
        total_time = max(time.monotonic() - start_time, 0.001)
        status = StepStatus.COMPLETED if process.returncode == 0 and local_path else StepStatus.FAILED
        raw_message = stderr.strip() or stdout.strip()
        message = "prefetch completed" if status == StepStatus.COMPLETED else _prefetch_error_summary(raw_message, process.returncode)
        if status == StepStatus.FAILED and local_path:
            status = StepStatus.COMPLETED
            message = "prefetch returned an error, but a completed SRA file was found"
        elif status == StepStatus.FAILED and final_size > 0 and self.resume_partial:
            status = StepStatus.CANCELLED
            message = f"prefetch interrupted; kept partial download for resume ({_format_bytes(final_size)})"
        validation = None
        if status == StepStatus.COMPLETED and local_path:
            if progress_callback:
                progress_callback(
                    DownloadProgress(
                        accession=request.accession,
                        status=StepStatus.RUNNING,
                        downloaded_bytes=final_size,
                        expected_size_bytes=request.expected_size_bytes,
                        percent=100.0,
                        message="验证 SRA 完整性",
                        local_path=local_path,
                    )
                )
            validation = self._validate_sra_file(local_path, progress_callback=progress_callback, request=request, cancellation_token=cancellation_token)
            if validation is not None and not validation.ok:
                if _validation_cancelled(validation):
                    status = StepStatus.CANCELLED
                    message = "SRA validation cancelled; downloaded SRA preserved"
                else:
                    status = StepStatus.FAILED
                    message = f"SRA validation failed: {_compact_message(validation.message)}"
                if status == StepStatus.FAILED and self.cleanup_on_fail:
                    cleanup_accession_artifacts(request.accession, request.output_dir)
                    local_path = None
                    final_size = accession_size(request.accession, request.output_dir)
            elif validation is not None and _validation_passed(validation):
                self._write_validation_marker(local_path, validation)
                self._remember_validated_path(local_path)
        if status == StepStatus.FAILED and self.cleanup_on_fail and not self.resume_partial:
            cleanup_accession_artifacts(request.accession, request.output_dir)
        if progress_callback:
            progress_callback(
                DownloadProgress(
                    accession=request.accession,
                    status=status,
                    downloaded_bytes=final_size,
                    expected_size_bytes=request.expected_size_bytes,
                    speed_bps=final_size / total_time,
                    percent=100.0 if status == StepStatus.COMPLETED else _percent(final_size, request.expected_size_bytes),
                    message=message,
                    local_path=local_path,
                )
            )
        return DownloadResult(
            accession=request.accession,
            status=status,
            local_path=local_path,
            command=command,
            return_code=validation.return_code if validation is not None and not validation.ok else process.returncode,
            message=message,
            downloaded_bytes=final_size,
            speed_bps=final_size / total_time,
        )

    def _validate_sra_file(
        self,
        path: Path,
        progress_callback: ProgressCallback | None = None,
        request: DownloadRequest | None = None,
        cancellation_token: CancellationToken | None = None,
    ) -> "_ValidationResult | None":
        if not self.validate_after_download:
            return None
        native_command = ["vdb-validate", str(path)]
        command = self._runtime_command(native_command)
        executable = command[0] if command else ""
        if self.execution_mode.lower() not in {"docker", "container"} and shutil.which(executable) is None:
            return _ValidationResult(ok=True, command=command, return_code=0, message="vdb-validate not found; validation skipped")
        started_at = time.monotonic()
        try:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        except FileNotFoundError:
            return _ValidationResult(ok=True, command=command, return_code=0, message="vdb-validate not found; validation skipped")
        while process.poll() is None:
            if cancellation_token is not None and cancellation_token.is_cancelled():
                process.terminate()
                try:
                    stdout, stderr = process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    stdout, stderr = process.communicate()
                return _ValidationResult(
                    ok=False,
                    command=command,
                    return_code=130,
                    message=f"vdb-validate cancelled: {stderr.strip() or stdout.strip()}",
                )
            if self.timeout_seconds is not None and time.monotonic() - started_at > self.timeout_seconds:
                process.terminate()
                stdout, stderr = process.communicate()
                return _ValidationResult(
                    ok=False,
                    command=command,
                    return_code=None,
                    message=f"vdb-validate timed out: {stderr.strip() or stdout.strip()}",
                )
            if progress_callback and request is not None:
                progress_callback(
                    DownloadProgress(
                        accession=request.accession,
                        status=StepStatus.RUNNING,
                        downloaded_bytes=path.stat().st_size if path.exists() else 0,
                        expected_size_bytes=request.expected_size_bytes,
                        percent=100.0,
                        message=f"验证 SRA 完整性 {int(time.monotonic() - started_at)}s",
                        local_path=path,
                    )
                )
            time.sleep(max(0.5, min(self.poll_interval_seconds, 2.0)))
        stdout, stderr = process.communicate()
        message = _compact_validation_message(stderr.strip() or stdout.strip() or "vdb-validate completed")
        return _ValidationResult(ok=process.returncode == 0, command=command, return_code=process.returncode, message=message)

    def _validation_marker_is_current(self, path: Path) -> bool:
        if not self.validate_after_download:
            return False
        marker = _validation_marker_path(path)
        if not marker.exists() or not path.exists():
            return False
        try:
            data = json.loads(marker.read_text(encoding="utf-8"))
            stat = path.stat()
            return (
                data.get("ok") is True
                and _validation_passed(
                    _ValidationResult(
                        ok=bool(data.get("ok")),
                        command=[],
                        return_code=data.get("return_code"),
                        message=str(data.get("message") or ""),
                    )
                )
                and int(data.get("size_bytes") or -1) == stat.st_size
                and float(data.get("mtime") or -1) == stat.st_mtime
            )
        except (OSError, ValueError, json.JSONDecodeError):
            return False

    def _validation_seen_in_process(self, path: Path) -> bool:
        try:
            stat = path.stat()
            return (str(path.resolve()), stat.st_size, stat.st_mtime) in self._validated_paths
        except OSError:
            return False

    def _remember_validated_path(self, path: Path) -> None:
        try:
            stat = path.stat()
            self._validated_paths.add((str(path.resolve()), stat.st_size, stat.st_mtime))
        except OSError:
            return

    def _write_validation_marker(self, path: Path, validation: _ValidationResult) -> None:
        if not path.exists():
            return
        marker = _validation_marker_path(path)
        stat = path.stat()
        marker.write_text(
            json.dumps(
                {
                    "ok": validation.ok,
                    "size_bytes": stat.st_size,
                    "mtime": stat.st_mtime,
                    "validated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "return_code": validation.return_code,
                    "message": _compact_message(validation.message),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def _finalize_interrupted(
        self,
        request: DownloadRequest,
        command: list[str],
        status: StepStatus,
        message: str,
        stdout: str,
        stderr: str,
        start_time: float,
        progress_callback: ProgressCallback | None,
    ) -> DownloadResult:
        final_size = accession_size(request.accession, request.output_dir)
        total_time = max(time.monotonic() - start_time, 0.001)
        if progress_callback:
            progress_callback(
                DownloadProgress(
                    accession=request.accession,
                    status=status,
                    downloaded_bytes=final_size,
                    expected_size_bytes=request.expected_size_bytes,
                    speed_bps=final_size / total_time,
                    percent=_percent(final_size, request.expected_size_bytes),
                    message=message,
                )
            )
        return DownloadResult(
            accession=request.accession,
            status=status,
            command=command,
            return_code=None,
            message=message or stderr.strip() or stdout.strip(),
            downloaded_bytes=final_size,
            speed_bps=final_size / total_time,
        )


def _percent(downloaded: int, expected: int | None) -> float | None:
    if not expected or expected <= 0:
        return None
    return min(downloaded / expected * 100, 100.0)


def _cancelled_result(request: DownloadRequest, command: list[str], message: str) -> DownloadResult:
    return DownloadResult(
        accession=request.accession,
        status=StepStatus.CANCELLED,
        command=command,
        message=message,
    )


def _missing_command_message(command: list[str], execution_mode: str, exc: FileNotFoundError) -> str:
    executable = command[0] if command else str(exc)
    if execution_mode.lower() in {"docker", "container"}:
        return f"command not found: {executable}. Please install Docker or make sure docker is in PATH."
    if executable == "prefetch" and shutil.which("docker"):
        return "command not found: prefetch. Use execution_mode=docker or install SRA Toolkit on the host."
    return f"command not found: {executable}"


def _compact_message(message: str, limit: int = 180) -> str:
    text = " ".join(line.strip() for line in str(message or "").splitlines() if line.strip())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _prefetch_error_summary(message: str, return_code: int | None) -> str:
    text = " ".join(line.strip() for line in str(message or "").splitlines() if line.strip())
    if not text:
        return f"prefetch failed with return code {return_code}" if return_code is not None else "prefetch failed"
    lower = text.lower()
    if "current preference is set to retrieve sra normalized format" in lower and not any(
        token in lower for token in ("failed", "cannot", "error", "not found", "timed out", "timeout")
    ):
        return f"prefetch exited early after starting download (return code {return_code})"
    return _compact_message(text)


def _format_bytes(value: int) -> str:
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f}{unit}" if unit != "B" else f"{int(size)}B"
        size /= 1024
    return f"{int(value)}B"


def _validation_marker_path(path: Path) -> Path:
    return path.with_name(path.name + ".vdb_validate.json")


def _validation_passed(validation: _ValidationResult) -> bool:
    message = validation.message.lower()
    return validation.ok and validation.return_code == 0 and "validation skipped" not in message and "not found" not in message


def _validation_cancelled(validation: _ValidationResult) -> bool:
    return validation.return_code == 130 or "cancelled" in validation.message.lower()


def _compact_validation_message(message: str, max_length: int = 240) -> str:
    text = " ".join(str(message or "").split())
    if not text:
        return ""
    if len(text) <= max_length:
        return text
    head = text[: max_length - 20].rstrip()
    tail = text[-16:].lstrip()
    return f"{head} ... {tail}"
