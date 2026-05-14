from __future__ import annotations

import json

import pytest

from rnaseq_workflow.core.models import SampleLayout
from rnaseq_workflow.steps.data_ingestion.scanner import (
    infer_sample_id_from_fastq,
    scan_inputs,
)


def test_scan_sra_files(tmp_path):
    (tmp_path / "SRR001.sra").write_text("", encoding="utf-8")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "SRR002.sra").write_text("", encoding="utf-8")

    result = scan_inputs(tmp_path, project_id="P1")

    assert result.sample_count == 2
    assert [sample.sample_id for sample in result.samples] == ["SRR001", "SRR002"]
    assert all(sample.metadata["input_type"] == "sra" for sample in result.samples)


def test_scan_paired_fastq_files(tmp_path):
    (tmp_path / "sampleA_R1.fastq.gz").write_text("", encoding="utf-8")
    (tmp_path / "sampleA_R2.fastq.gz").write_text("", encoding="utf-8")

    result = scan_inputs(tmp_path)

    assert result.sample_count == 1
    sample = result.samples[0]
    assert sample.sample_id == "sampleA"
    assert sample.layout == SampleLayout.PAIRED
    assert len(sample.source_paths) == 2


def test_scan_single_fastq_file(tmp_path):
    (tmp_path / "sampleB.fastq").write_text("", encoding="utf-8")

    result = scan_inputs(tmp_path)

    assert result.sample_count == 1
    assert result.samples[0].sample_id == "sampleB"
    assert result.samples[0].layout == SampleLayout.SINGLE


def test_scan_fastq_attaches_sra_metadata_sidecar(tmp_path):
    sample_dir = tmp_path / "SRR001"
    sample_dir.mkdir()
    (sample_dir / "SRR001_1.fastq.gz").write_text("", encoding="utf-8")
    (sample_dir / "metadata.json").write_text(
        json.dumps(
            {
                "run": "SRR001",
                "bioproject": "PRJ1",
                "biosample": "SAM1",
                "taxid": "4555",
                "scientific_name": "Setaria italica",
                "library_strategy": "RNA-Seq",
                "library_source": "TRANSCRIPTOMIC",
                "library_layout": "SINGLE",
                "metadata_source": "ncbi_runinfo",
            }
        ),
        encoding="utf-8",
    )

    result = scan_inputs(tmp_path)

    assert result.samples[0].metadata["sra_run"] == "SRR001"
    assert result.samples[0].metadata["bioproject"] == "PRJ1"
    assert result.samples[0].metadata["scientific_name"] == "Setaria italica"
    assert result.samples[0].metadata["library_layout"] == "SINGLE"


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("SRR001_1.fastq.gz", "SRR001"),
        ("SRR001_2.fastq.gz", "SRR001"),
        ("sample-R1.fq.gz", "sample"),
        ("sample.R2.fastq", "sample"),
        ("single.fastq.gz", "single"),
    ],
)
def test_infer_sample_id_from_fastq(filename, expected):
    assert infer_sample_id_from_fastq(filename) == expected


def test_scan_inputs_requires_directory(tmp_path):
    with pytest.raises(FileNotFoundError):
        scan_inputs(tmp_path / "missing")
