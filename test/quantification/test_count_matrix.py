from __future__ import annotations

import pytest

from rnaseq_workflow.steps.quantification.count_matrix import (
    SampleCountTable,
    infer_sample_id_from_featurecounts_path,
    merge_count_tables,
    read_featurecounts_table,
    write_count_matrix_tsv,
    write_normalized_matrix_tsv,
)


def test_infer_sample_id_from_featurecounts_path():
    assert infer_sample_id_from_featurecounts_path("S1.featureCounts.txt") == "S1"
    assert infer_sample_id_from_featurecounts_path("S2.counts.txt") == "S2"


def test_read_featurecounts_table(tmp_path):
    table_path = tmp_path / "S1.featureCounts.txt"
    table_path.write_text(
        "\n".join(
            [
                '# Program:featureCounts v2.0.6; Command:"featureCounts"',
                "Geneid\tChr\tStart\tEnd\tStrand\tLength\tS1.sorted.bam",
                "geneA\tchr1\t1\t10\t+\t10\t3",
                "geneB\tchr1\t20\t30\t+\t11\t0",
            ]
        ),
        encoding="utf-8",
    )

    table = read_featurecounts_table(table_path)

    assert table.sample_id == "S1"
    assert table.counts == {"geneA": 3, "geneB": 0}
    assert table.lengths == {"geneA": 10, "geneB": 11}


def test_merge_count_tables_fills_missing_genes():
    matrix = merge_count_tables(
        [
            SampleCountTable(sample_id="S1", source_path="S1.txt", counts={"geneA": 3}),
            SampleCountTable(sample_id="S2", source_path="S2.txt", counts={"geneB": 5}),
        ]
    )

    assert matrix.sample_ids == ["S1", "S2"]
    assert matrix.gene_ids == ["geneA", "geneB"]
    assert matrix.counts["geneA"]["S1"] == 3
    assert "S2" not in matrix.counts["geneA"]


def test_write_count_matrix_tsv(tmp_path):
    matrix = merge_count_tables(
        [
            SampleCountTable(sample_id="S1", source_path="S1.txt", counts={"geneA": 3}),
            SampleCountTable(sample_id="S2", source_path="S2.txt", counts={"geneA": 1, "geneB": 5}),
        ]
    )
    output = tmp_path / "matrix.tsv"

    write_count_matrix_tsv(matrix, output)

    assert output.read_text(encoding="utf-8").splitlines() == [
        "Geneid\tS1\tS2",
        "geneA\t3\t1",
        "geneB\t0\t5",
    ]


def test_write_normalized_matrices_from_featurecounts_lengths(tmp_path):
    table1 = SampleCountTable(
        sample_id="S1",
        source_path="S1.txt",
        counts={"geneA": 100, "geneB": 300},
        lengths={"geneA": 1000, "geneB": 3000},
    )
    table2 = SampleCountTable(
        sample_id="S2",
        source_path="S2.txt",
        counts={"geneA": 50, "geneB": 50},
        lengths={"geneA": 1000, "geneB": 3000},
    )
    matrix = merge_count_tables([table1, table2])

    fpkm = tmp_path / "fpkm.tsv"
    tpm = tmp_path / "tpm.tsv"
    cpm = tmp_path / "cpm.tsv"
    write_normalized_matrix_tsv(matrix, fpkm, "fpkm")
    write_normalized_matrix_tsv(matrix, tpm, "tpm")
    write_normalized_matrix_tsv(matrix, cpm, "cpm")

    assert fpkm.read_text(encoding="utf-8").splitlines() == [
        "Geneid\tS1\tS2",
        "geneA\t250000\t500000",
        "geneB\t250000\t166666.666667",
    ]
    assert tpm.read_text(encoding="utf-8").splitlines() == [
        "Geneid\tS1\tS2",
        "geneA\t500000\t750000",
        "geneB\t500000\t250000",
    ]
    assert cpm.read_text(encoding="utf-8").splitlines() == [
        "Geneid\tS1\tS2",
        "geneA\t250000\t500000",
        "geneB\t750000\t500000",
    ]


def test_merge_count_tables_rejects_duplicate_sample_ids():
    with pytest.raises(ValueError, match="duplicate sample id"):
        merge_count_tables(
            [
                SampleCountTable(sample_id="S1", source_path="a.txt", counts={}),
                SampleCountTable(sample_id="S1", source_path="b.txt", counts={}),
            ]
        )


def test_read_featurecounts_rejects_invalid_header(tmp_path):
    table_path = tmp_path / "bad.txt"
    table_path.write_text("bad\theader\n", encoding="utf-8")

    with pytest.raises(ValueError, match="invalid featureCounts header"):
        read_featurecounts_table(table_path)
