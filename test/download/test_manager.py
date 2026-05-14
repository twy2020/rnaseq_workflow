from __future__ import annotations

from rnaseq_workflow.core.models import StepStatus
from rnaseq_workflow.steps.download.manager import DownloadManager
from rnaseq_workflow.steps.download.models import DownloadRequest
from rnaseq_workflow.steps.download.prefetch import PrefetchDownloader


def test_download_manager_dry_run(tmp_path):
    requests = [
        DownloadRequest(accession="SRR000001", output_dir=tmp_path),
        DownloadRequest(accession="SRR000002", output_dir=tmp_path),
    ]
    manager = DownloadManager(downloader=PrefetchDownloader(), max_workers=2)

    summary = manager.download_many(requests, dry_run=True)
    overall = manager.overall_progress()

    assert summary.total == 2
    assert summary.completed == 2
    assert overall.completed == 2
    assert manager.get_progress("SRR000001").status == StepStatus.COMPLETED


def test_download_manager_cancel_all():
    manager = DownloadManager()

    manager.cancel_all()

    assert manager._cancel_token.is_cancelled()


def test_accession_size_is_scoped_per_accession(tmp_path):
    from rnaseq_workflow.steps.download.cache import accession_size

    root = tmp_path / "downloads"
    (root / "SRR1").mkdir(parents=True)
    (root / "SRR2").mkdir(parents=True)
    (root / "SRR1" / "SRR1.sra.tmp").write_bytes(b"a" * 10)
    (root / "SRR2" / "SRR2.sra.tmp").write_bytes(b"b" * 20)

    assert accession_size("SRR1", root) == 10
    assert accession_size("SRR2", root) == 20
