from __future__ import annotations

import json

from rnaseq_workflow.core.models import Sample, SampleLayout
from rnaseq_workflow.steps.data_ingestion.manifest import write_manifest_csv, write_manifest_json


def test_write_manifest_json(tmp_path):
    sample = Sample(
        sample_id="S1",
        source_path=tmp_path / "S1_R1.fastq.gz",
        source_paths=[tmp_path / "S1_R1.fastq.gz", tmp_path / "S1_R2.fastq.gz"],
        layout=SampleLayout.PAIRED,
        metadata={"input_type": "fastq"},
    )

    output = write_manifest_json([sample], tmp_path / "manifest.json")
    data = json.loads(output.read_text(encoding="utf-8"))

    assert data[0]["sample_id"] == "S1"
    assert data[0]["layout"] == "paired"
    assert len(data[0]["source_paths"]) == 2


def test_write_manifest_csv(tmp_path):
    sample = Sample(
        sample_id="S1",
        source_path=tmp_path / "S1.sra",
        layout=SampleLayout.UNKNOWN,
        metadata={"input_type": "sra"},
    )

    output = write_manifest_csv([sample], tmp_path / "manifest.csv")
    text = output.read_text(encoding="utf-8")

    assert "sample_id,layout,input_type,source_paths" in text
    assert "S1,unknown,sra" in text
