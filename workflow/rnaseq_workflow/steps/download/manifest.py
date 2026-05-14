from __future__ import annotations

import csv
import json
from pathlib import Path

from rnaseq_workflow.core.models import StepStatus
from rnaseq_workflow.steps.download.models import DownloadRequest, DownloadResult


def read_download_requests(path: str | Path, output_dir: str | Path) -> list[DownloadRequest]:
    manifest = Path(path)
    suffix = manifest.suffix.lower()
    if suffix == ".csv":
        return _read_csv(manifest, Path(output_dir))
    if suffix == ".json":
        return _read_json(manifest, Path(output_dir))
    return _read_txt(manifest, Path(output_dir))


def write_download_results_json(results: list[DownloadResult], output_path: str | Path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [_result_to_dict(result) for result in results]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_download_results_csv(results: list[DownloadResult], output_path: str | Path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["accession", "status", "local_path", "cached", "downloaded_bytes", "speed_bps", "message"],
        )
        writer.writeheader()
        for result in results:
            writer.writerow(_result_to_dict(result))
    return path


def _read_txt(path: Path, output_dir: Path) -> list[DownloadRequest]:
    requests = []
    for line in path.read_text(encoding="utf-8").splitlines():
        accession = line.strip()
        if not accession or accession.startswith("#"):
            continue
        requests.append(DownloadRequest(accession=accession, output_dir=output_dir))
    return requests


def _read_csv(path: Path, output_dir: Path) -> list[DownloadRequest]:
    requests = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            accession = (row.get("accession") or "").strip()
            if not accession:
                continue
            expected_raw = (row.get("expected_size_bytes") or "").strip()
            requests.append(
                DownloadRequest(
                    accession=accession,
                    output_dir=Path(row.get("output_dir") or output_dir),
                    source=row.get("source") or "sra",
                    expected_size_bytes=int(expected_raw) if expected_raw else None,
                )
            )
    return requests


def _read_json(path: Path, output_dir: Path) -> list[DownloadRequest]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        rows = raw
    else:
        rows = raw.get("accessions") or raw.get("requests") or []
    requests = []
    for row in rows:
        if isinstance(row, str):
            requests.append(DownloadRequest(accession=row, output_dir=output_dir))
        else:
            requests.append(
                DownloadRequest(
                    accession=row["accession"],
                    output_dir=Path(row.get("output_dir") or output_dir),
                    source=row.get("source", "sra"),
                    expected_size_bytes=row.get("expected_size_bytes"),
                )
            )
    return requests


def _result_to_dict(result: DownloadResult) -> dict:
    return {
        "accession": result.accession,
        "status": result.status.value if isinstance(result.status, StepStatus) else str(result.status),
        "local_path": "" if result.local_path is None else str(result.local_path),
        "cached": result.cached,
        "downloaded_bytes": result.downloaded_bytes,
        "speed_bps": result.speed_bps,
        "message": result.message,
    }
