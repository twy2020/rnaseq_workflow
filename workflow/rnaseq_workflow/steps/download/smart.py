from __future__ import annotations

import re
from pathlib import Path

from rnaseq_workflow.steps.download.manager import DownloadManager
from rnaseq_workflow.steps.download.manifest import read_download_requests
from rnaseq_workflow.steps.download.models import BatchDownloadSummary, DownloadRequest, DownloadResult
from rnaseq_workflow.steps.download.prefetch import PrefetchDownloader
from rnaseq_workflow.steps.download.runinfo import fetch_sra_run_size_bytes


def looks_like_sra_accession(target: str) -> bool:
    upper = target.strip().upper()
    return len(upper) > 3 and upper[:3] in {"SRR", "ERR", "DRR"} and upper[3:].isdigit()


def build_smart_download_requests(
    target: str,
    output_dir: str | Path,
    fetch_expected_sizes: bool = True,
) -> list[DownloadRequest]:
    cleaned = target.strip()
    if looks_like_sra_accession(cleaned):
        accession = cleaned.upper()
        return [
            DownloadRequest(
                accession=accession,
                output_dir=Path(output_dir),
                expected_size_bytes=_expected_size(accession, fetch_expected_sizes),
            )
        ]
    accessions = split_sra_targets(cleaned)
    if accessions:
        return [
            DownloadRequest(
                accession=accession,
                output_dir=Path(output_dir),
                expected_size_bytes=_expected_size(accession, fetch_expected_sizes),
            )
            for accession in accessions
        ]
    path = Path(cleaned)
    if path.exists():
        return read_download_requests(path, output_dir)
    raise ValueError(
        "target must be an SRA run accession, multiple accessions separated by comma/space/semicolon, "
        "or a TXT/CSV/JSON manifest path"
    )


def split_sra_targets(target: str) -> list[str]:
    parts = [part.strip().upper() for part in re.split(r"[\s,;]+", target.strip()) if part.strip()]
    if len(parts) <= 1:
        return []
    if all(looks_like_sra_accession(part) for part in parts):
        return parts
    return []


def _expected_size(accession: str, fetch_expected_sizes: bool) -> int | None:
    if not fetch_expected_sizes:
        return None
    return fetch_sra_run_size_bytes(accession)


def smart_download(
    target: str,
    output_dir: str | Path,
    downloader: PrefetchDownloader,
    dry_run: bool = False,
    max_workers: int = 2,
) -> BatchDownloadSummary:
    requests = build_smart_download_requests(target, output_dir)
    manager = DownloadManager(downloader=downloader, max_workers=max_workers)
    return manager.download_many(requests, dry_run=dry_run)
