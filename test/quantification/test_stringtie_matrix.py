from __future__ import annotations

from rnaseq_workflow.steps.quantification.stringtie_matrix import (
    merge_stringtie_abundance_files,
    read_stringtie_gene_abundance,
    write_stringtie_matrix_tsv,
)


def test_read_stringtie_gene_abundance(tmp_path):
    path = tmp_path / "S1.stringtie.gene_abund.tsv"
    path.write_text(
        "\n".join(
            [
                "Gene ID\tGene Name\tReference\tStrand\tStart\tEnd\tCoverage\tFPKM\tTPM",
                "geneA\tA\tchr1\t+\t1\t10\t1\t3.5\t7.2",
                "geneB\tB\tchr1\t+\t20\t30\t0\t0\t0",
            ]
        ),
        encoding="utf-8",
    )

    table = read_stringtie_gene_abundance(path)

    assert table.sample_id == "S1"
    assert table.fpkm == {"geneA": 3.5, "geneB": 0.0}
    assert table.tpm == {"geneA": 7.2, "geneB": 0.0}


def test_merge_and_write_stringtie_matrix(tmp_path):
    s1 = tmp_path / "S1.stringtie.gene_abund.tsv"
    s2 = tmp_path / "S2.stringtie.gene_abund.tsv"
    header = "Gene ID\tGene Name\tReference\tStrand\tStart\tEnd\tCoverage\tFPKM\tTPM"
    s1.write_text(header + "\ngeneA\tA\tchr1\t+\t1\t10\t1\t3.5\t7.2\n", encoding="utf-8")
    s2.write_text(header + "\ngeneA\tA\tchr1\t+\t1\t10\t1\t1.25\t2.5\n", encoding="utf-8")

    matrix = merge_stringtie_abundance_files([s1, s2], value="fpkm")
    output = tmp_path / "stringtie_fpkm.tsv"
    write_stringtie_matrix_tsv(matrix, output)

    assert output.read_text(encoding="utf-8").splitlines() == [
        "Geneid\tS1\tS2",
        "geneA\t3.5\t1.25",
    ]
