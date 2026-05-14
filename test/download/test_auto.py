from __future__ import annotations

from rnaseq_workflow.core.models import StepStatus
from rnaseq_workflow.steps.download.auto import AutoDownloader
from rnaseq_workflow.steps.download.models import DownloadRequest, DownloadResult


class DummyDownloader:
    def __init__(self, message):
        self.message = message

    def download(self, request, dry_run=False, progress_callback=None, cancellation_token=None):
        return DownloadResult(accession=request.accession, status=StepStatus.COMPLETED, message=self.message)


def test_auto_downloader_uses_ena_when_links_exist(monkeypatch, tmp_path):
    monkeypatch.setattr("rnaseq_workflow.steps.download.auto.fetch_ena_fastq_files", lambda accession: object())
    downloader = AutoDownloader(ena_downloader=DummyDownloader("ena"), sra_downloader=DummyDownloader("sra"))

    result = downloader.download(DownloadRequest("SRR1", tmp_path))

    assert result.message == "ena"


def test_auto_downloader_falls_back_to_sra(monkeypatch, tmp_path):
    monkeypatch.setattr("rnaseq_workflow.steps.download.auto.fetch_ena_fastq_files", lambda accession: None)
    downloader = AutoDownloader(ena_downloader=DummyDownloader("ena"), sra_downloader=DummyDownloader("sra"))

    result = downloader.download(DownloadRequest("SRR1", tmp_path))

    assert result.message == "sra"
