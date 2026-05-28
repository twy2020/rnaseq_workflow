from __future__ import annotations

import json

import pytest

from rnaseq_workflow.core.finalize import finalize_project, parse_hisat2_summary_log, write_hisat2_alignment_summary_tsv
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


def _write_stringtie_abundance(path, rows: list[tuple[str, float, float]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["Gene ID\tGene Name\tReference\tStrand\tStart\tEnd\tCoverage\tFPKM\tTPM"]
    for gene_id, fpkm, tpm in rows:
        lines.append(f"{gene_id}\t{gene_id}\tchr1\t+\t1\t10\t1\t{fpkm}\t{tpm}")
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
    _write_hisat2_log(output_dir / "samples" / "S1" / "alignment" / "S1.hisat2.log", total=100, aligned=90)
    _write_hisat2_log(output_dir / "samples" / "S2" / "alignment" / "S2.hisat2.log", total=200, aligned=150)

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
    assert result.hisat2_summary is not None
    assert result.hisat2_summary.read_text(encoding="utf-8").splitlines() == [
        "样本ID\t总reads数\t成功比对reads数\t比对率",
        "S1\t100\t90\t90.00%",
        "S2\t200\t150\t75.00%",
    ]


def test_finalize_project_writes_selected_expression_outputs(tmp_path):
    output_dir = tmp_path / "output"
    samples = [
        Sample(sample_id="S1", source_path=tmp_path / "S1.fastq"),
        Sample(sample_id="S2", source_path=tmp_path / "S2.fastq"),
    ]
    _write_featurecounts(output_dir / "samples" / "S1" / "quantification" / "S1.featureCounts.txt", "S1", [("geneA", 100)])
    _write_featurecounts(output_dir / "samples" / "S2" / "quantification" / "S2.featureCounts.txt", "S2", [("geneA", 50)])
    (output_dir / "progress.json").parent.mkdir(parents=True, exist_ok=True)
    (output_dir / "progress.json").write_text(json.dumps({"samples": {}}), encoding="utf-8")

    result = finalize_project("demo", output_dir, samples, output_formats=["raw_counts", "fpkm", "tpm"])

    assert set(result.expression_matrices or {}) == {"raw_counts", "fpkm", "tpm"}
    assert result.expression_matrices["raw_counts"].name == "raw_counts.tsv"
    assert result.expression_matrices["fpkm"].name == "fpkm.tsv"
    assert result.expression_matrices["tpm"].name == "tpm.tsv"
    assert result.expression_matrices["fpkm"].exists()
    assert result.expression_matrices["tpm"].exists()


def test_finalize_project_writes_stringtie_outputs(tmp_path):
    output_dir = tmp_path / "output"
    samples = [
        Sample(sample_id="S1", source_path=tmp_path / "S1.fastq"),
        Sample(sample_id="S2", source_path=tmp_path / "S2.fastq"),
    ]
    _write_stringtie_abundance(output_dir / "samples" / "S1" / "quantification" / "S1.stringtie.gene_abund.tsv", [("geneA", 3.5, 7.0)])
    _write_stringtie_abundance(output_dir / "samples" / "S2" / "quantification" / "S2.stringtie.gene_abund.tsv", [("geneA", 1.25, 2.5)])
    (output_dir / "progress.json").parent.mkdir(parents=True, exist_ok=True)
    (output_dir / "progress.json").write_text(json.dumps({"samples": {}}), encoding="utf-8")

    result = finalize_project("demo", output_dir, samples, output_formats=["stringtie_fpkm", "stringtie_tpm"])

    assert set(result.expression_matrices or {}) == {"stringtie_fpkm", "stringtie_tpm"}
    assert result.expression_matrices["stringtie_fpkm"].read_text(encoding="utf-8").splitlines() == [
        "Geneid\tS1\tS2",
        "geneA\t3.5\t1.25",
    ]


def test_finalize_project_writes_all_matrices_to_explicit_reports_dir(tmp_path):
    output_dir = tmp_path / "spill-output"
    reports_dir = tmp_path / "task-root" / "reports"
    samples = [Sample(sample_id="S1", source_path=tmp_path / "S1.fastq")]
    _write_featurecounts(output_dir / "samples" / "S1" / "quantification" / "S1.featureCounts.txt", "S1", [("geneA", 100)])
    _write_stringtie_abundance(output_dir / "samples" / "S1" / "quantification" / "S1.stringtie.gene_abund.tsv", [("geneA", 3.5, 7.0)])
    (output_dir / "progress.json").parent.mkdir(parents=True, exist_ok=True)
    (output_dir / "progress.json").write_text(json.dumps({"samples": {}}), encoding="utf-8")

    result = finalize_project(
        "demo",
        output_dir,
        samples,
        reports_dir=reports_dir,
        output_formats=["raw_counts", "stringtie_fpkm"],
    )

    assert result.expression_matrices["raw_counts"] == reports_dir / "raw_counts.tsv"
    assert result.expression_matrices["stringtie_fpkm"] == reports_dir / "stringtie_fpkm.tsv"
    assert (reports_dir / "stringtie_fpkm.tsv").exists()


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


def test_parse_hisat2_summary_log_single_end(tmp_path):
    log = tmp_path / "S1.hisat2.log"
    _write_hisat2_log(log, total=1000, aligned=876)

    row = parse_hisat2_summary_log(log, "S1")

    assert row.sample_id == "S1"
    assert row.total_reads == 1000
    assert row.aligned_reads == 876
    assert row.alignment_rate == 87.6


def test_parse_hisat2_summary_log_paired_end_uses_overall_rate(tmp_path):
    log = tmp_path / "S2.hisat2.log"
    log.write_text(
        "\n".join(
            [
                "1000 reads; of these:",
                "  1000 (100.00%) were paired; of these:",
                "    100 (10.00%) aligned concordantly 0 times",
                "    700 (70.00%) aligned concordantly exactly 1 time",
                "    200 (20.00%) aligned concordantly >1 times",
                "90.00% overall alignment rate",
            ]
        ),
        encoding="utf-8",
    )

    row = parse_hisat2_summary_log(log, "S2")

    assert row.total_reads == 1000
    assert row.aligned_reads == 900
    assert row.alignment_rate == 90.0


def test_write_hisat2_alignment_summary_tsv(tmp_path):
    row = parse_hisat2_summary_log(tmp_path / "missing.log", "S1")
    output = write_hisat2_alignment_summary_tsv([row], tmp_path / "hisat2.tsv")

    assert output.read_text(encoding="utf-8").splitlines() == [
        "样本ID\t总reads数\t成功比对reads数\t比对率",
        "S1\t\t\t",
    ]


def _write_hisat2_log(path, total: int, aligned: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    unaligned = total - aligned
    rate = aligned / total * 100 if total else 0.0
    path.write_text(
        "\n".join(
            [
                f"{total} reads; of these:",
                f"  {total} (100.00%) were unpaired; of these:",
                f"    {unaligned} ({100 - rate:.2f}%) aligned 0 times",
                f"    {aligned} ({rate:.2f}%) aligned exactly 1 time",
                "    0 (0.00%) aligned >1 times",
                f"{rate:.2f}% overall alignment rate",
            ]
        ),
        encoding="utf-8",
    )
