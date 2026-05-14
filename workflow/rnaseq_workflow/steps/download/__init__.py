"""Download step package."""

from rnaseq_workflow.steps.download.cache import find_cached_sra
from rnaseq_workflow.steps.download.auto import AutoDownloader
from rnaseq_workflow.steps.download.ena import EnaFastqDownloader, fetch_ena_fastq_files
from rnaseq_workflow.steps.download.manager import DownloadManager, OverallDownloadProgress
from rnaseq_workflow.steps.download.manifest import (
    read_download_requests,
    write_download_results_csv,
    write_download_results_json,
)
from rnaseq_workflow.steps.download.models import (
    BatchDownloadSummary,
    DownloadProgress,
    DownloadRequest,
    DownloadResult,
)
from rnaseq_workflow.steps.download.prefetch import PrefetchDownloader, build_prefetch_command
from rnaseq_workflow.steps.download.runinfo import (
    SraMetadataGroup,
    SraRunMetadata,
    fetch_sra_metadata,
    fetch_sra_runinfo_rows,
    fetch_sra_run_size_bytes,
    group_sra_metadata,
    load_sra_metadata_sidecar,
    metadata_has_mixed_groups,
    write_sra_metadata_sidecars,
)
from rnaseq_workflow.steps.download.smart import (
    build_smart_download_requests,
    looks_like_sra_accession,
    smart_download,
    split_sra_targets,
)

__all__ = [
    "BatchDownloadSummary",
    "DownloadManager",
    "DownloadProgress",
    "DownloadRequest",
    "DownloadResult",
    "AutoDownloader",
    "EnaFastqDownloader",
    "OverallDownloadProgress",
    "PrefetchDownloader",
    "SraMetadataGroup",
    "SraRunMetadata",
    "build_prefetch_command",
    "build_smart_download_requests",
    "find_cached_sra",
    "fetch_ena_fastq_files",
    "fetch_sra_metadata",
    "fetch_sra_runinfo_rows",
    "fetch_sra_run_size_bytes",
    "group_sra_metadata",
    "load_sra_metadata_sidecar",
    "looks_like_sra_accession",
    "metadata_has_mixed_groups",
    "read_download_requests",
    "smart_download",
    "split_sra_targets",
    "write_download_results_csv",
    "write_download_results_json",
    "write_sra_metadata_sidecars",
]
