from __future__ import annotations

import csv
import hashlib
import socket
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import Callable

from rnaseq_workflow.core.models import StepStatus
from rnaseq_workflow.core.cancellation import CancellationToken
from rnaseq_workflow.steps.download.models import DownloadProgress, DownloadRequest, DownloadResult

ProgressCallback = Callable[[DownloadProgress], None]


@dataclass(frozen=True, slots=True)
class EnaFastqFile:
    url: str
    md5: str | None
    size_bytes: int | None
    filename: str


@dataclass(frozen=True, slots=True)
class EnaRunFiles:
    accession: str
    layout: str
    files: list[EnaFastqFile]


def fetch_ena_fastq_files(accession: str, timeout_seconds: float = 30.0) -> EnaRunFiles | None:
    params = urllib.parse.urlencode(
        {
            "accession": accession,
            "result": "read_run",
            "fields": "run_accession,fastq_ftp,fastq_md5,fastq_bytes,library_layout",
            "format": "tsv",
            "download": "false",
        }
    )
    url = f"https://www.ebi.ac.uk/ena/portal/api/filereport?{params}"
    try:
        with urllib.request.urlopen(url, timeout=timeout_seconds) as response:
            text = response.read().decode("utf-8", errors="replace")
    except OSError:
        return None
    rows = list(csv.DictReader(StringIO(text), delimiter="\t"))
    if not rows:
        return None
    row = rows[0]
    ftp_raw = (row.get("fastq_ftp") or "").strip()
    if not ftp_raw:
        return None
    md5s = _split_field(row.get("fastq_md5"))
    sizes = _split_field(row.get("fastq_bytes"))
    files = []
    for index, raw_url in enumerate(_split_field(ftp_raw)):
        url = raw_url if raw_url.startswith(("http://", "https://", "ftp://")) else f"https://{raw_url}"
        filename = Path(urllib.parse.urlparse(url).path).name
        files.append(
            EnaFastqFile(
                url=url,
                md5=md5s[index] if index < len(md5s) and md5s[index] else None,
                size_bytes=int(sizes[index]) if index < len(sizes) and sizes[index].isdigit() else None,
                filename=filename,
            )
        )
    return EnaRunFiles(accession=accession, layout=row.get("library_layout") or "", files=files)


class EnaFastqDownloader:
    def __init__(
        self,
        verify_md5: bool = True,
        chunk_size: int = 1024 * 1024,
        stall_timeout_seconds: float = 30.0,
        retries: int = 3,
        retry_delay_seconds: float = 3.0,
        proxy: str = "",
    ) -> None:
        self.verify_md5 = verify_md5
        self.chunk_size = chunk_size
        self.stall_timeout_seconds = stall_timeout_seconds
        self.retries = retries
        self.retry_delay_seconds = retry_delay_seconds
        self.proxy = proxy.strip()

    def download(
        self,
        request: DownloadRequest,
        dry_run: bool = False,
        progress_callback: ProgressCallback | None = None,
        cancellation_token: CancellationToken | None = None,
    ) -> DownloadResult:
        if cancellation_token and cancellation_token.is_cancelled():
            return DownloadResult(accession=request.accession, status=StepStatus.CANCELLED, message="download cancelled")
        run_files = fetch_ena_fastq_files(request.accession)
        if run_files is None or not run_files.files:
            return DownloadResult(
                accession=request.accession,
                status=StepStatus.FAILED,
                message="ENA FASTQ links not found",
            )
        request.output_dir.mkdir(parents=True, exist_ok=True)
        output_dir = request.output_dir / request.accession
        output_dir.mkdir(parents=True, exist_ok=True)
        command = _download_command(run_files)
        if dry_run:
            return DownloadResult(
                accession=request.accession,
                status=StepStatus.COMPLETED,
                command=command,
                return_code=0,
                message="dry-run ENA FASTQ download command built",
            )

        start = time.monotonic()
        total_size = request.expected_size_bytes or sum(file.size_bytes or 0 for file in run_files.files) or None
        downloaded = 0
        outputs: list[Path] = []
        for file in run_files.files:
            output = output_dir / file.filename
            if output.exists() and file.size_bytes and output.stat().st_size == file.size_bytes:
                downloaded += output.stat().st_size
                outputs.append(output)
                continue
            partial = output.with_suffix(output.suffix + ".part")
            try:
                downloaded = self._download_file_with_retries(
                    file=file,
                    partial=partial,
                    downloaded_before=downloaded,
                    total_size=total_size,
                    accession=request.accession,
                    start_time=start,
                    progress_callback=progress_callback,
                    cancellation_token=cancellation_token,
                )
                if file.size_bytes and partial.stat().st_size != file.size_bytes:
                    return _failed(request, command, f"incomplete ENA download: {partial}")
                if self.verify_md5 and file.md5 and _md5(partial) != file.md5:
                    return _failed(request, command, f"md5 mismatch: {partial}")
                partial.replace(output)
            except OSError as exc:
                if cancellation_token and cancellation_token.is_cancelled():
                    return DownloadResult(
                        accession=request.accession,
                        status=StepStatus.CANCELLED,
                        command=command,
                        message="download cancelled",
                    )
                return _failed(request, command, str(exc))
            outputs.append(output)
            if progress_callback:
                elapsed = max(time.monotonic() - start, 0.001)
                progress_callback(
                    DownloadProgress(
                        accession=request.accession,
                        status=StepStatus.RUNNING,
                        downloaded_bytes=downloaded,
                        expected_size_bytes=total_size,
                        speed_bps=downloaded / elapsed,
                        percent=None if not total_size else min(downloaded / total_size * 100, 100.0),
                        message=f"downloaded {output.name}",
                        local_path=output,
                    )
                )
        elapsed = max(time.monotonic() - start, 0.001)
        if progress_callback:
            progress_callback(
                DownloadProgress(
                    accession=request.accession,
                    status=StepStatus.COMPLETED,
                    downloaded_bytes=downloaded,
                    expected_size_bytes=total_size,
                    speed_bps=downloaded / elapsed,
                    percent=100.0,
                    message="ENA FASTQ download completed",
                    local_path=outputs[0] if outputs else None,
                )
            )
        return DownloadResult(
            accession=request.accession,
            status=StepStatus.COMPLETED,
            local_path=outputs[0] if outputs else None,
            command=command,
            return_code=0,
            message="ENA FASTQ download completed",
            downloaded_bytes=downloaded,
            speed_bps=downloaded / elapsed,
        )

    def _download_file_with_retries(
        self,
        file: EnaFastqFile,
        partial: Path,
        downloaded_before: int,
        total_size: int | None,
        accession: str,
        start_time: float,
        progress_callback: ProgressCallback | None,
        cancellation_token: CancellationToken | None = None,
    ) -> int:
        last_error: BaseException | None = None
        attempts = max(self.retries, 0) + 1
        for attempt in range(1, attempts + 1):
            if cancellation_token and cancellation_token.is_cancelled():
                raise OSError("download cancelled")
            existing = partial.stat().st_size if partial.exists() else 0
            try:
                if shutil.which("curl.exe") or shutil.which("curl"):
                    return _download_url_with_curl(
                        file.url,
                        partial,
                        downloaded_before=downloaded_before,
                        total_size=total_size,
                        accession=accession,
                        start_time=start_time,
                        progress_callback=progress_callback,
                        stall_timeout_seconds=self.stall_timeout_seconds,
                        cancellation_token=cancellation_token,
                        proxy=self.proxy,
                    )
                if progress_callback and attempt > 1:
                    elapsed = max(time.monotonic() - start_time, 0.001)
                    progress_callback(
                        DownloadProgress(
                            accession=accession,
                            status=StepStatus.RUNNING,
                            downloaded_bytes=downloaded_before + existing,
                            expected_size_bytes=total_size,
                            speed_bps=(downloaded_before + existing) / elapsed,
                            percent=None if not total_size else min((downloaded_before + existing) / total_size * 100, 100.0),
                            message=f"retry {attempt}/{attempts}: {file.filename}",
                            local_path=partial,
                        )
                    )
                return _download_url(
                    file.url,
                    partial,
                    start_byte=existing,
                    chunk_size=self.chunk_size,
                    downloaded_before=downloaded_before,
                    total_size=total_size,
                    accession=accession,
                    start_time=start_time,
                    progress_callback=progress_callback,
                    stall_timeout_seconds=self.stall_timeout_seconds,
                    cancellation_token=cancellation_token,
                    proxy=self.proxy,
                )
            except (OSError, TimeoutError, socket.timeout) as exc:
                last_error = exc
                if progress_callback:
                    current = partial.stat().st_size if partial.exists() else existing
                    elapsed = max(time.monotonic() - start_time, 0.001)
                    progress_callback(
                        DownloadProgress(
                            accession=accession,
                            status=StepStatus.RUNNING,
                            downloaded_bytes=downloaded_before + current,
                            expected_size_bytes=total_size,
                            speed_bps=(downloaded_before + current) / elapsed,
                            percent=None if not total_size else min((downloaded_before + current) / total_size * 100, 100.0),
                            message=f"stalled after {self.stall_timeout_seconds:g}s; retrying {attempt}/{attempts}",
                            local_path=partial,
                        )
                    )
                if attempt == attempts:
                    break
                time.sleep(self.retry_delay_seconds)
        raise OSError(f"ENA download stalled after {attempts} attempts: {last_error}")


def _split_field(value: str | None) -> list[str]:
    return [] if not value else [item.strip() for item in value.split(";")]


def _download_command(run_files: EnaRunFiles) -> list[str]:
    curl = shutil.which("curl.exe") or shutil.which("curl")
    if curl:
        return [curl, "-L", "-C", "-", "--retry", "5", "--retry-delay", "5", "..."] + [file.url for file in run_files.files]
    return ["ena-download", run_files.accession]


def _download_url_with_curl(
    url: str,
    output: Path,
    downloaded_before: int,
    total_size: int | None,
    accession: str,
    start_time: float,
    progress_callback: ProgressCallback | None,
    stall_timeout_seconds: float,
    cancellation_token: CancellationToken | None,
    proxy: str = "",
) -> int:
    curl = shutil.which("curl.exe") or shutil.which("curl")
    if not curl:
        raise OSError("curl not found")
    output.parent.mkdir(parents=True, exist_ok=True)
    jitter = _stable_jitter_seconds(accession, output.name)
    speed_time = max(10, int(stall_timeout_seconds)) + jitter
    retry_delay = max(3, int(3 + jitter))
    command = [
        curl,
        "-L",
        "-C",
        "-",
        "--fail",
        "--retry",
        "5",
        "--retry-delay",
        str(retry_delay),
        "--retry-connrefused",
        "--connect-timeout",
        str(max(5, int(stall_timeout_seconds)) + jitter),
        "--speed-time",
        str(speed_time),
        "--speed-limit",
        "1024",
    ]
    if proxy:
        command.extend(["--proxy", proxy])
    command.extend([
        "-o",
        str(output),
        url,
    ])
    process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    stderr_chunks: list[str] = []
    last_size = output.stat().st_size if output.exists() else 0
    last_change = time.monotonic()
    last_report_size = last_size
    last_report_time = time.monotonic()
    try:
        while process.poll() is None:
            if cancellation_token and cancellation_token.is_cancelled():
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                raise OSError("download cancelled")
            current = output.stat().st_size if output.exists() else 0
            if current != last_size:
                last_size = current
                last_change = time.monotonic()
            if progress_callback:
                total_downloaded = downloaded_before + current
                now = time.monotonic()
                speed_elapsed = max(now - last_report_time, 0.001)
                current_speed = max(0.0, (current - last_report_size) / speed_elapsed)
                idle_seconds = max(0.0, now - last_change)
                last_report_size = current
                last_report_time = now
                message = f"curl downloading {output.name}"
                if current_speed <= 0 and idle_seconds >= 3:
                    message = f"等待数据/重连中 {int(idle_seconds)}s {output.name}"
                progress_callback(
                    DownloadProgress(
                        accession=accession,
                        status=StepStatus.RUNNING,
                        downloaded_bytes=total_downloaded,
                        expected_size_bytes=total_size,
                        speed_bps=current_speed,
                        percent=None if not total_size else min(total_downloaded / total_size * 100, 100.0),
                        message=message,
                        local_path=output,
                    )
                )
            if time.monotonic() - last_change > max((stall_timeout_seconds + jitter) * 2, 60):
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                raise OSError(f"curl stalled: {output}")
            time.sleep(1)
        stderr = process.stderr.read() if process.stderr else ""
        if stderr:
            stderr_chunks.append(stderr)
        if process.returncode != 0:
            raise OSError("curl failed: " + ("".join(stderr_chunks).strip() or f"exit code {process.returncode}"))
        return downloaded_before + (output.stat().st_size if output.exists() else 0)
    finally:
        if process.stderr:
            process.stderr.close()


def _stable_jitter_seconds(*parts: str) -> int:
    raw = "|".join(parts).encode("utf-8", errors="ignore")
    return hashlib.sha1(raw).digest()[0] % 11


def _download_url(
    url: str,
    output: Path,
    start_byte: int,
    chunk_size: int,
    downloaded_before: int,
    total_size: int | None,
    accession: str,
    start_time: float,
    progress_callback: ProgressCallback | None,
    stall_timeout_seconds: float,
    cancellation_token: CancellationToken | None = None,
    proxy: str = "",
) -> int:
    headers = {"Range": f"bytes={start_byte}-"} if start_byte else {}
    request = urllib.request.Request(url, headers=headers)
    mode = "ab" if start_byte else "wb"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy, "https": proxy})) if proxy else None
    open_url = opener.open if opener else urllib.request.urlopen
    with open_url(request, timeout=stall_timeout_seconds) as response, output.open(mode + "") as handle:
        downloaded_current = start_byte
        last_report_bytes = start_byte
        last_report_time = time.monotonic()
        while True:
            if cancellation_token and cancellation_token.is_cancelled():
                raise OSError("download cancelled")
            chunk = response.read(chunk_size)
            if not chunk:
                break
            handle.write(chunk)
            downloaded_current += len(chunk)
            total_downloaded = downloaded_before + downloaded_current
            if progress_callback:
                now = time.monotonic()
                speed_elapsed = max(now - last_report_time, 0.001)
                current_speed = max(0.0, (downloaded_current - last_report_bytes) / speed_elapsed)
                last_report_bytes = downloaded_current
                last_report_time = now
                progress_callback(
                    DownloadProgress(
                        accession=accession,
                        status=StepStatus.RUNNING,
                        downloaded_bytes=total_downloaded,
                        expected_size_bytes=total_size,
                        speed_bps=current_speed,
                        percent=None if not total_size else min(total_downloaded / total_size * 100, 100.0),
                        message=f"downloading {output.name}",
                        local_path=output,
                    )
                )
    return downloaded_before + downloaded_current


def _md5(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _failed(request: DownloadRequest, command: list[str], message: str) -> DownloadResult:
    return DownloadResult(accession=request.accession, status=StepStatus.FAILED, command=command, message=message)
