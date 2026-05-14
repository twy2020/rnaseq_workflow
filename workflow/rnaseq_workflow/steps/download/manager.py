from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from threading import Lock

from rnaseq_workflow.core.cancellation import CancellationToken
from rnaseq_workflow.core.models import StepStatus
from rnaseq_workflow.steps.download.models import BatchDownloadSummary, DownloadProgress, DownloadRequest, DownloadResult
from rnaseq_workflow.steps.download.prefetch import PrefetchDownloader


@dataclass(slots=True)
class OverallDownloadProgress:
    total: int
    completed: int
    failed: int
    cancelled: int
    skipped: int
    running: int
    downloaded_bytes: int
    speed_bps: float


class DownloadManager:
    def __init__(self, downloader: PrefetchDownloader | None = None, max_workers: int = 2) -> None:
        self.downloader = downloader or PrefetchDownloader()
        self.max_workers = max_workers
        self._progress: dict[str, DownloadProgress] = {}
        self._lock = Lock()
        self._cancel_token = CancellationToken()

    def download_many(self, requests: list[DownloadRequest], dry_run: bool = False) -> BatchDownloadSummary:
        with self._lock:
            self._progress = {
                request.accession: DownloadProgress(
                    accession=request.accession,
                    status=StepStatus.PENDING,
                    expected_size_bytes=request.expected_size_bytes,
                )
                for request in requests
            }

        results: list[DownloadResult] = []
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = [
                executor.submit(self.downloader.download, request, dry_run, self._update_progress, self._cancel_token)
                for request in requests
            ]
            for future in as_completed(futures):
                results.append(future.result())
        results.sort(key=lambda result: result.accession)
        return BatchDownloadSummary(results=results)

    def get_progress(self, accession: str) -> DownloadProgress | None:
        with self._lock:
            return self._progress.get(accession)

    def cancel_all(self) -> None:
        self._cancel_token.cancel()

    def overall_progress(self) -> OverallDownloadProgress:
        with self._lock:
            rows = list(self._progress.values())
        return OverallDownloadProgress(
            total=len(rows),
            completed=sum(1 for row in rows if row.status == StepStatus.COMPLETED),
            failed=sum(1 for row in rows if row.status == StepStatus.FAILED),
            cancelled=sum(1 for row in rows if row.status == StepStatus.CANCELLED),
            skipped=sum(1 for row in rows if row.status == StepStatus.SKIPPED),
            running=sum(1 for row in rows if row.status == StepStatus.RUNNING),
            downloaded_bytes=sum(row.downloaded_bytes for row in rows),
            speed_bps=sum(row.speed_bps for row in rows),
        )

    def _update_progress(self, progress: DownloadProgress) -> None:
        with self._lock:
            self._progress[progress.accession] = progress
