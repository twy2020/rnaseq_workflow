from __future__ import annotations

import json

from rnaseq_workflow.core.task_manifest import parse_task_manifest


def test_parse_sra_accession_manifest():
    parsed = parse_task_manifest("SRR000001 SRR000002")

    assert parsed.ok
    assert parsed.accessions == ["SRR000001", "SRR000002"]


def test_parse_custom_url_manifest_multiple_base_urls():
    parsed = parse_task_manifest(
        json.dumps(
            {
                "url_groups": [
                    {"base_url": "https://example.org/data", "filenames": ["A_1.fastq.gz", "A_2.fastq.gz"]},
                    {"base_url": "ftp://ftp.example.org/runs", "filenames": ["B.sra"]},
                ]
            }
        )
    )

    assert parsed.ok
    assert parsed.urls == [
        "https://example.org/data/A_1.fastq.gz",
        "https://example.org/data/A_2.fastq.gz",
        "ftp://ftp.example.org/runs/B.sra",
    ]


def test_custom_url_manifest_rejects_unsafe_filename():
    parsed = parse_task_manifest(json.dumps({"url_groups": [{"base_url": "https://example.org", "filenames": ["../x.sh"]}]}))

    assert not parsed.ok
    assert "not allowed" in parsed.errors[0]


def test_custom_url_manifest_rejects_unsupported_protocol():
    parsed = parse_task_manifest(json.dumps({"url_groups": [{"base_url": "file:///tmp", "filenames": ["A.fastq.gz"]}]}))

    assert not parsed.ok
    assert "scheme" in parsed.errors[0]
