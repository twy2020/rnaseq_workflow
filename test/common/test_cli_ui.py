from __future__ import annotations

from io import StringIO

from rich.console import Console

from rnaseq_workflow.cli.ui import run_download_manager_with_progress
from rnaseq_workflow.cli.ui import _format_bytes
from rnaseq_workflow.steps.download.manager import DownloadManager
from rnaseq_workflow.steps.download.models import DownloadRequest
from rnaseq_workflow.steps.download.prefetch import PrefetchDownloader


def test_run_download_manager_with_progress_dry_run(tmp_path):
    console = Console(file=StringIO(), force_terminal=False)
    manager = DownloadManager(downloader=PrefetchDownloader(), max_workers=2)
    requests = [
        DownloadRequest(accession="SRR000001", output_dir=tmp_path),
        DownloadRequest(accession="SRR000002", output_dir=tmp_path),
    ]

    summary = run_download_manager_with_progress(console, manager, requests, dry_run=True)

    assert summary.completed == 2
    assert manager.overall_progress().completed == 2


def test_format_bytes():
    assert _format_bytes(0) == "0B"
    assert _format_bytes(1024) == "1.0KB"
    assert _format_bytes(1024 * 1024) == "1.0MB"
