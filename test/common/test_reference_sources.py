from __future__ import annotations

import gzip

import pytest

from rnaseq_workflow.core.models import RunContext
from rnaseq_workflow.core.reference_sources import (
    build_reference_download_plan,
    normalize_species_name,
    prepare_reference_from_urls,
)


def test_normalize_species_name():
    assert normalize_species_name("Arabidopsis thaliana") == "arabidopsis_thaliana"
    assert normalize_species_name("homo_sapiens") == "homo_sapiens"


def test_build_reference_download_plan_strips_gzip_suffix(tmp_path):
    plan = build_reference_download_plan(
        "demo",
        "https://example.org/genome.fa.gz",
        "https://example.org/genes.gtf.gz",
        tmp_path / "downloads",
    )

    assert plan.fasta_download.name == "genome.fa.gz"
    assert plan.fasta_ready.name == "genome.fa"
    assert plan.annotation_ready.name == "genes.gtf"


def test_prepare_reference_from_file_urls_dry_run_index(tmp_path):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    fasta_gz = source_dir / "genome.fa.gz"
    gtf_gz = source_dir / "genes.gtf.gz"
    with gzip.open(fasta_gz, "wt", encoding="utf-8") as handle:
        handle.write(">chr1\nACGTACGT\n")
    with gzip.open(gtf_gz, "wt", encoding="utf-8") as handle:
        handle.write('chr1\t.\texon\t1\t4\t.\t+\t.\tgene_id "geneA";\n')
    context = RunContext(
        project_id="demo",
        work_dir=tmp_path,
        output_dir=tmp_path / "references" / "demo",
        config={"execution_mode": "local"},
        dry_run=True,
    )

    prepared = prepare_reference_from_urls(
        "demo",
        fasta_url=fasta_gz.as_uri(),
        annotation_url=gtf_gz.as_uri(),
        reference_dir=tmp_path / "references",
        download_dir=tmp_path / "downloads",
        context=context,
        force=True,
        provider="ensembl",
        annotation_provider="ensembl",
        species="glycine_max",
    )

    assert prepared.asset.fasta.exists()
    assert prepared.asset.annotation is not None
    assert prepared.asset.annotation.exists()
    assert prepared.asset.provider == "ensembl"
    assert prepared.asset.species == "glycine_max"
    assert prepared.index_command is not None
    assert prepared.index_command[:3] == ["hisat2-build", "-p", "4"]
    assert prepared.dry_run


def test_prepare_reference_requires_context_when_building(tmp_path):
    source = tmp_path / "genome.fa"
    source.write_text(">chr1\nACGT\n", encoding="utf-8")
    gtf = tmp_path / "genes.gtf"
    gtf.write_text('chr1\t.\texon\t1\t4\t.\t+\t.\tgene_id "geneA";\n', encoding="utf-8")

    with pytest.raises(ValueError, match="context"):
        prepare_reference_from_urls(
            "demo",
            fasta_url=source.as_uri(),
            annotation_url=gtf.as_uri(),
            reference_dir=tmp_path / "references",
            download_dir=tmp_path / "downloads",
            context=None,
        )
