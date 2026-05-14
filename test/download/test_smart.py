from __future__ import annotations

import pytest

from rnaseq_workflow.core.models import StepStatus
from rnaseq_workflow.steps.download.prefetch import PrefetchDownloader
from rnaseq_workflow.steps.download.smart import (
    build_smart_download_requests,
    looks_like_sra_accession,
    smart_download,
    split_sra_targets,
)


def test_looks_like_sra_accession():
    assert looks_like_sra_accession("SRR11047173")
    assert looks_like_sra_accession("err123")
    assert not looks_like_sra_accession("GSE123")


def test_build_smart_download_requests_accession(tmp_path):
    requests = build_smart_download_requests("srr11047173", tmp_path)

    assert len(requests) == 1
    assert requests[0].accession == "SRR11047173"
    assert requests[0].output_dir == tmp_path


def test_split_sra_targets_accepts_single_line_separators():
    assert split_sra_targets("srr000001 SRR000002,err000003;DRR000004") == [
        "SRR000001",
        "SRR000002",
        "ERR000003",
        "DRR000004",
    ]


def test_split_sra_targets_rejects_mixed_non_run_values():
    assert split_sra_targets("SRR000001 GSE123") == []


def test_build_smart_download_requests_multiple_accessions(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "rnaseq_workflow.steps.download.smart.fetch_sra_run_size_bytes",
        lambda accession: 1000 if accession == "SRR000001" else 2000,
    )

    requests = build_smart_download_requests("srr000001, SRR000002", tmp_path)

    assert [request.accession for request in requests] == ["SRR000001", "SRR000002"]
    assert [request.output_dir for request in requests] == [tmp_path, tmp_path]
    assert [request.expected_size_bytes for request in requests] == [1000, 2000]


def test_build_smart_download_requests_can_skip_expected_size_lookup(tmp_path, monkeypatch):
    def fail_lookup(accession):
        raise AssertionError("unexpected network lookup")

    monkeypatch.setattr("rnaseq_workflow.steps.download.smart.fetch_sra_run_size_bytes", fail_lookup)

    requests = build_smart_download_requests("srr000001 SRR000002", tmp_path, fetch_expected_sizes=False)

    assert [request.accession for request in requests] == ["SRR000001", "SRR000002"]
    assert [request.expected_size_bytes for request in requests] == [None, None]


def test_build_smart_download_requests_manifest(tmp_path):
    manifest = tmp_path / "accessions.txt"
    manifest.write_text("SRR000001\nSRR000002\n", encoding="utf-8")

    requests = build_smart_download_requests(str(manifest), tmp_path / "downloads")

    assert [request.accession for request in requests] == ["SRR000001", "SRR000002"]


def test_build_smart_download_requests_rejects_unknown(tmp_path):
    with pytest.raises(ValueError):
        build_smart_download_requests("GSE123", tmp_path)


def test_smart_download_single_dry_run(tmp_path):
    summary = smart_download("SRR000001", tmp_path, PrefetchDownloader(), dry_run=True)

    assert summary.completed == 1
    assert summary.results[0].status == StepStatus.COMPLETED
