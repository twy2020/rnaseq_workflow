from __future__ import annotations

import json

from rnaseq_workflow.core.models import StepStatus
from rnaseq_workflow.steps.download.manifest import (
    read_download_requests,
    write_download_results_csv,
    write_download_results_json,
)
from rnaseq_workflow.steps.download.models import DownloadResult


def test_read_txt_manifest(tmp_path):
    manifest = tmp_path / "accessions.txt"
    manifest.write_text("# comment\nSRR000001\nSRR000002\n", encoding="utf-8")

    requests = read_download_requests(manifest, tmp_path / "downloads")

    assert [request.accession for request in requests] == ["SRR000001", "SRR000002"]


def test_read_csv_manifest(tmp_path):
    manifest = tmp_path / "accessions.csv"
    manifest.write_text("accession,source,expected_size_bytes\nSRR000001,sra,100\n", encoding="utf-8")

    requests = read_download_requests(manifest, tmp_path / "downloads")

    assert requests[0].accession == "SRR000001"
    assert requests[0].expected_size_bytes == 100


def test_read_json_manifest(tmp_path):
    manifest = tmp_path / "accessions.json"
    manifest.write_text(json.dumps({"accessions": ["SRR000001"]}), encoding="utf-8")

    requests = read_download_requests(manifest, tmp_path / "downloads")

    assert requests[0].accession == "SRR000001"


def test_write_download_results(tmp_path):
    result = DownloadResult(accession="SRR000001", status=StepStatus.COMPLETED, message="ok")

    json_path = write_download_results_json([result], tmp_path / "results.json")
    csv_path = write_download_results_csv([result], tmp_path / "results.csv")

    assert "SRR000001" in json_path.read_text(encoding="utf-8")
    assert "SRR000001" in csv_path.read_text(encoding="utf-8")
