from __future__ import annotations

import json

import pytest

from rnaseq_workflow.core.finalize import finalize_project
from rnaseq_workflow.core.models import Sample


def _write_featurecounts(path, sample_name: str, rows: list[tuple[str, int]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        '# Program:featureCounts v2.0.6; Command:"featureCounts"',
        f"Geneid\tChr\tStart\tEnd\tStrand\tLength\t{sample_name}.sorted.bam",
    ]
    for gene_id, count in rows:
        lines.append(f"{gene_id}\tchr1\t1\t10\t+\t10\t{count}")
    path.write_text("\n".join(lines), encoding="utf-8")


def test_finalize_project_merges_counts_and_writes_reports(tmp_path):
    output_dir = tmp_path / "output"
    samples = [
        Sample(sample_id="S1", source_path=tmp_path / "S1.fastq"),
        Sample(sample_id="S2", source_path=tmp_path / "S2.fastq"),
    ]
    _write_featurecounts(output_dir / "samples" / "S1" / "quantification" / "S1.featureCounts.txt", "S1", [("geneA", 1)])
    _write_featurecounts(
        output_dir / "samples" / "S2" / "quantification" / "S2.featureCounts.txt",
        "S2",
        [("geneA", 2), ("geneB", 5)],
    )
    (output_dir / "progress.json").parent.mkdir(parents=True, exist_ok=True)
    (output_dir / "progress.json").write_text(json.dumps({"samples": {}}), encoding="utf-8")

    result = finalize_project("demo", output_dir, samples)

    assert result.sample_count == 2
    assert result.gene_count == 2
    assert result.counts_matrix.read_text(encoding="utf-8").splitlines() == [
        "Geneid\tS1\tS2",
        "geneA\t1\t2",
        "geneB\t0\t5",
    ]
    assert json.loads(result.report_json.read_text(encoding="utf-8"))["project_id"] == "demo"
    assert "# RNA-seq Workflow Report: demo" in result.report_markdown.read_text(encoding="utf-8")


def test_finalize_project_requires_featurecounts_outputs(tmp_path):
    samples = [Sample(sample_id="S1", source_path=tmp_path / "S1.fastq")]

    with pytest.raises(FileNotFoundError, match="featureCounts output not found"):
        finalize_project("demo", tmp_path / "output", samples)


def test_finalize_project_uses_explicit_state_path(tmp_path):
    output_dir = tmp_path / "spill-output"
    state_path = tmp_path / "task-root" / "progress.json"
    samples = [Sample(sample_id="S1", source_path=tmp_path / "S1.fastq")]
    _write_featurecounts(output_dir / "samples" / "S1" / "quantification" / "S1.featureCounts.txt", "S1", [("geneA", 3)])
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {
                "samples": {
                    "S1": {
                        "steps": {
                            "featurecounts": {"status": "COMPLETED"},
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    result = finalize_project("demo", output_dir, samples, state_path=state_path)
    report = json.loads(result.report_json.read_text(encoding="utf-8"))

    assert report["sample_count"] == 1
    assert report["step_status"]["completed"] == 1
    assert report["state_path"] == str(state_path)
