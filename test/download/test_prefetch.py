from __future__ import annotations

import threading
import time

import pytest

from rnaseq_workflow.core.cancellation import CancellationToken
from rnaseq_workflow.core.models import StepStatus
from rnaseq_workflow.steps.download.models import DownloadRequest, DownloadResult
from rnaseq_workflow.steps.download.prefetch import (
    PrefetchDownloader,
    _validation_marker_path,
    build_prefetch_command,
    validate_sra_accession,
)


def test_build_prefetch_command():
    command = build_prefetch_command("SRR001", "downloads", max_size="1G", transport="https", force=True)

    assert command == [
        "prefetch",
        "SRR001",
        "--output-directory",
        "downloads",
        "--max-size",
        "1G",
        "--transport",
        "https",
        "--force",
        "yes",
    ]


def test_validate_sra_accession():
    validate_sra_accession("SRR000001")
    validate_sra_accession("ERR000001")
    validate_sra_accession("DRR000001")

    with pytest.raises(ValueError):
        validate_sra_accession("GSE123")


def test_prefetch_dry_run(tmp_path):
    request = DownloadRequest(accession="SRR000001", output_dir=tmp_path)
    progress = []

    result = PrefetchDownloader(max_size="1G").download(request, dry_run=True, progress_callback=progress.append)

    assert result.status == StepStatus.COMPLETED
    assert (tmp_path / "SRR000001").exists()
    assert result.command is not None
    assert result.command[:2] == ["prefetch", "SRR000001"]
    assert progress[-1].status == StepStatus.COMPLETED


def test_prefetch_docker_dry_run_builds_docker_command(tmp_path):
    request = DownloadRequest(accession="SRR000001", output_dir=tmp_path)

    result = PrefetchDownloader(
        execution_mode="docker",
        docker_image="rnaseq-workflow:tools",
        docker_workspace=tmp_path,
    ).download(request, dry_run=True)

    assert result.status == StepStatus.COMPLETED
    assert result.command is not None
    assert result.command[:3] == ["docker", "run", "--rm"]
    assert "rnaseq-workflow:tools" in result.command
    assert "prefetch" in result.command


def test_prefetch_missing_command_returns_failed_result(tmp_path):
    request = DownloadRequest(accession="SRR000001", output_dir=tmp_path)
    downloader = PrefetchDownloader(poll_interval_seconds=0.01)

    result = downloader._run_prefetch(["definitely_missing_prefetch_binary"], request, None, None, attempt=1)

    assert result.status == StepStatus.FAILED
    assert "command not found" in result.message


def test_prefetch_failure_with_completed_sra_is_completed(tmp_path):
    request = DownloadRequest(accession="SRR000001", output_dir=tmp_path)
    nested = tmp_path / "SRR000001"
    nested.mkdir()
    (nested / "SRR000001.sra").write_bytes(b"complete")
    downloader = PrefetchDownloader(poll_interval_seconds=0.01)
    command = ["python", "-c", "import sys; print('verify failed', file=sys.stderr); sys.exit(1)"]

    result = downloader._run_prefetch(command, request, None, None, attempt=1)

    assert result.status == StepStatus.COMPLETED
    assert "completed SRA file was found" in result.message


def test_prefetch_validation_failure_marks_download_failed(tmp_path, monkeypatch):
    request = DownloadRequest(accession="SRR000001", output_dir=tmp_path)
    nested = tmp_path / "SRR000001"
    nested.mkdir()
    sra = nested / "SRR000001.sra"
    command = [
        "python",
        "-c",
        f"from pathlib import Path; Path(r'{sra}').write_bytes(b'bad')",
    ]
    downloader = PrefetchDownloader(poll_interval_seconds=0.01, cleanup_on_fail=True)
    monkeypatch.setattr(downloader, "_validate_sra_file", lambda path, **kwargs: type("R", (), {"ok": False, "command": ["vdb-validate", str(path)], "return_code": 3, "message": "corrupt blob"})())

    result = downloader._run_prefetch(command, request, None, None, attempt=1)

    assert result.status == StepStatus.FAILED
    assert "validation failed" in result.message
    assert not nested.exists()


def test_prefetch_uses_cache(tmp_path):
    nested = tmp_path / "SRR000001"
    nested.mkdir()
    cached = nested / "SRR000001.sra"
    cached.write_bytes(b"abc")

    request = DownloadRequest(accession="SRR000001", output_dir=tmp_path)
    result = PrefetchDownloader().download(request, dry_run=False)

    assert result.status == StepStatus.SKIPPED
    assert result.cached
    assert result.local_path == cached


def test_prefetch_cached_validation_failure_removes_cache_and_redownloads(tmp_path, monkeypatch):
    nested = tmp_path / "SRR000001"
    nested.mkdir()
    cached = nested / "SRR000001.sra"
    cached.write_bytes(b"abc")
    downloader = PrefetchDownloader(cleanup_on_fail=True, validate_after_download=False, poll_interval_seconds=0.01)
    monkeypatch.setattr(downloader, "_validate_sra_file", lambda path, **kwargs: type("R", (), {"ok": False, "command": ["vdb-validate", str(path)], "return_code": 3, "message": "corrupt blob"})())
    monkeypatch.setattr(
        downloader,
        "_run_with_retries",
        lambda command, request, progress_callback, cancellation_token: DownloadResult(
            accession=request.accession,
            status=StepStatus.COMPLETED,
            message="redownloaded",
        ),
    )

    result = downloader.download(DownloadRequest(accession="SRR000001", output_dir=tmp_path), dry_run=False)

    assert result.status == StepStatus.COMPLETED
    assert result.message == "redownloaded"
    assert not nested.exists()


def test_prefetch_validation_skip_when_vdb_validate_missing(tmp_path, monkeypatch):
    nested = tmp_path / "SRR000001"
    nested.mkdir()
    cached = nested / "SRR000001.sra"
    cached.write_bytes(b"abc")
    monkeypatch.setattr("rnaseq_workflow.steps.download.prefetch.shutil.which", lambda name: None)

    result = PrefetchDownloader().download(DownloadRequest(accession="SRR000001", output_dir=tmp_path), dry_run=False)

    assert result.status == StepStatus.SKIPPED
    assert cached.exists()


def test_prefetch_cached_validation_reports_progress(tmp_path, monkeypatch):
    nested = tmp_path / "SRR000001"
    nested.mkdir()
    cached = nested / "SRR000001.sra"
    cached.write_bytes(b"abc")
    monkeypatch.setattr("rnaseq_workflow.steps.download.prefetch.shutil.which", lambda name: None)
    progress = []

    result = PrefetchDownloader().download(
        DownloadRequest(accession="SRR000001", output_dir=tmp_path),
        dry_run=False,
        progress_callback=progress.append,
    )

    assert result.status == StepStatus.SKIPPED
    assert any("验证 SRA 完整性" in item.message for item in progress)


def test_prefetch_uses_validation_marker_for_cached_sra(tmp_path, monkeypatch):
    nested = tmp_path / "SRR000001"
    nested.mkdir()
    cached = nested / "SRR000001.sra"
    cached.write_bytes(b"abc")
    downloader = PrefetchDownloader()
    downloader._write_validation_marker(cached, type("R", (), {"ok": True, "return_code": 0, "message": "ok"})())
    monkeypatch.setattr(downloader, "_validate_sra_file", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not validate")))

    result = downloader.download(DownloadRequest(accession="SRR000001", output_dir=tmp_path), dry_run=False)

    assert result.status == StepStatus.SKIPPED
    assert "validation already passed" in result.message


def test_prefetch_uses_in_process_validation_cache(tmp_path, monkeypatch):
    nested = tmp_path / "SRR000001"
    nested.mkdir()
    cached = nested / "SRR000001.sra"
    cached.write_bytes(b"abc")
    downloader = PrefetchDownloader()
    downloader._remember_validated_path(cached)
    monkeypatch.setattr(downloader, "_validate_sra_file", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not validate")))

    result = downloader.download(DownloadRequest(accession="SRR000001", output_dir=tmp_path), dry_run=False)

    assert result.status == StepStatus.SKIPPED
    assert "validation already passed" in result.message


def test_prefetch_clean_before_download_removes_cache(tmp_path):
    nested = tmp_path / "SRR000001"
    nested.mkdir()
    cached = nested / "SRR000001.sra"
    cached.write_bytes(b"abc")
    request = DownloadRequest(accession="SRR000001", output_dir=tmp_path)
    token = CancellationToken()
    token.cancel()

    result = PrefetchDownloader(clean_before_download=True).download(request, dry_run=False, cancellation_token=token)

    assert result.status == StepStatus.CANCELLED
    assert not cached.exists()


def test_prefetch_cancel_before_download(tmp_path):
    request = DownloadRequest(accession="SRR000001", output_dir=tmp_path)
    token = CancellationToken()
    token.cancel()

    result = PrefetchDownloader().download(request, dry_run=False, cancellation_token=token)

    assert result.status == StepStatus.CANCELLED


def test_prefetch_timeout_cleans_artifacts(tmp_path):
    request = DownloadRequest(accession="SRR000001", output_dir=tmp_path)
    downloader = PrefetchDownloader(
        timeout_seconds=0.1,
        poll_interval_seconds=0.01,
        cleanup_on_fail=True,
        resume_partial=False,
    )
    command = [
        "python",
        "-c",
        (
            "import pathlib,time;"
            f"p=pathlib.Path(r'{tmp_path}')/'SRR000001';"
            "p.mkdir(parents=True, exist_ok=True);"
            "(p/'partial.tmp').write_text('x');"
            "time.sleep(5)"
        ),
    ]

    result = downloader._run_prefetch(command, request, None, None, attempt=1)

    assert result.status == StepStatus.FAILED
    assert "timed out" in result.message
    assert not (tmp_path / "SRR000001").exists()


def test_prefetch_cancellation_during_download(tmp_path):
    request = DownloadRequest(accession="SRR000001", output_dir=tmp_path)
    token = CancellationToken()
    downloader = PrefetchDownloader(poll_interval_seconds=0.01, cleanup_on_fail=True)
    command = ["python", "-c", "import time; time.sleep(5)"]

    def cancel_soon():
        time.sleep(0.05)
        token.cancel()

    thread = threading.Thread(target=cancel_soon)
    thread.start()
    result = downloader._run_prefetch(command, request, None, token, attempt=1)
    thread.join()

    assert result.status == StepStatus.CANCELLED
    assert not (tmp_path / "SRR000001").exists()


def test_prefetch_cancellation_keeps_partial_when_resume_enabled(tmp_path):
    request = DownloadRequest(accession="SRR000001", output_dir=tmp_path)
    partial_dir = tmp_path / "SRR000001"
    partial_dir.mkdir()
    partial_file = partial_dir / "partial.tmp"
    partial_file.write_text("x", encoding="utf-8")
    token = CancellationToken()
    downloader = PrefetchDownloader(poll_interval_seconds=0.01, cleanup_on_fail=True, resume_partial=True)
    command = [
        "python",
        "-c",
        "import time; time.sleep(5)",
    ]

    def cancel_soon():
        time.sleep(0.05)
        token.cancel()

    thread = threading.Thread(target=cancel_soon)
    thread.start()
    result = downloader._run_prefetch(command, request, None, token, attempt=1)
    thread.join()

    assert result.status == StepStatus.CANCELLED
    assert partial_file.exists()


def test_prefetch_failure_with_partial_is_resumable_cancelled(tmp_path):
    request = DownloadRequest(accession="SRR000001", output_dir=tmp_path)
    nested = tmp_path / "SRR000001"
    nested.mkdir()
    command = [
        "python",
        "-c",
        (
            "from pathlib import Path; import sys;"
            f"p=Path(r'{nested}')/'SRR000001.sra.tmp';"
            "p.write_bytes(b'x' * 1024);"
            "print('Current preference is set to retrieve SRA Normalized Format files with full base quality scores.', file=sys.stderr);"
            "sys.exit(3)"
        ),
    ]

    result = PrefetchDownloader(poll_interval_seconds=0.01, resume_partial=True)._run_prefetch(command, request, None, None, attempt=1)

    assert result.status == StepStatus.CANCELLED
    assert result.downloaded_bytes == 1024
    assert "kept partial" in result.message


def test_vdb_validate_can_be_cancelled(tmp_path, monkeypatch):
    sra = tmp_path / "SRR000001.sra"
    sra.write_bytes(b"abc")
    token = CancellationToken()
    downloader = PrefetchDownloader(poll_interval_seconds=0.01)
    monkeypatch.setattr("rnaseq_workflow.steps.download.prefetch.shutil.which", lambda name: name)
    monkeypatch.setattr(downloader, "_runtime_command", lambda command: ["python", "-c", "import time; time.sleep(5)"])

    def cancel_soon():
        time.sleep(0.05)
        token.cancel()

    thread = threading.Thread(target=cancel_soon)
    thread.start()
    result = downloader._validate_sra_file(
        sra,
        request=DownloadRequest(accession="SRR000001", output_dir=tmp_path),
        cancellation_token=token,
    )
    thread.join()

    assert result is not None
    assert not result.ok
    assert result.return_code == 130
    assert "cancelled" in result.message


def test_prefetch_validation_cancel_preserves_downloaded_sra(tmp_path, monkeypatch):
    request = DownloadRequest(accession="SRR000001", output_dir=tmp_path)
    nested = tmp_path / "SRR000001"
    nested.mkdir()
    sra = nested / "SRR000001.sra"
    command = [
        "python",
        "-c",
        f"from pathlib import Path; Path(r'{sra}').write_bytes(b'good')",
    ]
    downloader = PrefetchDownloader(poll_interval_seconds=0.01, cleanup_on_fail=True)
    monkeypatch.setattr(
        downloader,
        "_validate_sra_file",
        lambda path, **kwargs: type("R", (), {"ok": False, "command": ["vdb-validate", str(path)], "return_code": 130, "message": "vdb-validate cancelled"})(),
    )

    result = downloader._run_prefetch(command, request, None, None, attempt=1)

    assert result.status == StepStatus.CANCELLED
    assert sra.exists()
    assert "preserved" in result.message


def test_skipped_vdb_validation_does_not_create_pass_marker(tmp_path, monkeypatch):
    sra = tmp_path / "SRR000001.sra"
    sra.write_bytes(b"abc")
    downloader = PrefetchDownloader(poll_interval_seconds=0.01)
    monkeypatch.setattr("rnaseq_workflow.steps.download.prefetch.shutil.which", lambda name: None)

    result = downloader._validate_sra_file(sra)

    assert result is not None
    assert result.ok
    downloader._write_validation_marker(sra, result)
    assert not downloader._validation_marker_is_current(sra)
    assert _validation_marker_path(sra).exists()
