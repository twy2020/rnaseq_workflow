from __future__ import annotations

from rnaseq_workflow.core.cancellation import CancellationToken
from rnaseq_workflow.core.models import StepStatus
from rnaseq_workflow.steps.download.ena import EnaFastqDownloader, fetch_ena_fastq_files
from rnaseq_workflow.steps.download.models import DownloadProgress, DownloadRequest, DownloadResult
from rnaseq_workflow.steps.download.prefetch import PrefetchDownloader, ProgressCallback


class AutoDownloader:
    def __init__(
        self,
        ena_downloader: EnaFastqDownloader | None = None,
        sra_downloader: PrefetchDownloader | None = None,
        prefer: str = "ena",
    ) -> None:
        self.ena_downloader = ena_downloader or EnaFastqDownloader()
        self.sra_downloader = sra_downloader or PrefetchDownloader(execution_mode="docker")
        self.prefer = prefer

    def download(
        self,
        request: DownloadRequest,
        dry_run: bool = False,
        progress_callback: ProgressCallback | None = None,
        cancellation_token: CancellationToken | None = None,
    ) -> DownloadResult:
        if self.prefer == "sra":
            return self.sra_downloader.download(request, dry_run, progress_callback, cancellation_token)
        if fetch_ena_fastq_files(request.accession) is not None:
            if progress_callback:
                progress_callback(
                    DownloadProgress(
                        accession=request.accession,
                        status=StepStatus.RUNNING,
                        message="using ENA FASTQ source",
                    )
                )
            return self.ena_downloader.download(request, dry_run, progress_callback, cancellation_token)
        if progress_callback:
            progress_callback(
                DownloadProgress(
                    accession=request.accession,
                    status=StepStatus.RUNNING,
                    message="ENA links not found; falling back to SRA prefetch",
                )
            )
        return self.sra_downloader.download(request, dry_run, progress_callback, cancellation_token)
