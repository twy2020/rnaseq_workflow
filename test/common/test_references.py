from __future__ import annotations

import json

import pytest

from rnaseq_workflow.core.models import RunContext
from rnaseq_workflow.core.references import (
    build_hisat2_index_command,
    build_hisat2_index_for_reference,
    check_reference_asset,
    list_references,
    load_reference,
    reference_config_values,
    register_reference,
)


def test_register_reference_copies_fasta_and_annotation(tmp_path):
    fasta = tmp_path / "source.fa"
    fasta.write_text(">chr1\nACGT\n", encoding="utf-8")
    gtf = tmp_path / "source.gtf"
    gtf.write_text('chr1\t.\texon\t1\t4\t.\t+\t.\tgene_id "geneA";\n', encoding="utf-8")

    asset = register_reference("demo ref", fasta=fasta, annotation=gtf, reference_dir=tmp_path / "references")

    assert asset.reference_id == "demo_ref"
    assert asset.fasta.exists()
    assert asset.fasta.name == "genome.fa"
    assert asset.annotation is not None
    assert asset.annotation.name == "annotation.gtf"
    assert asset.provider == "custom"
    assert asset.build_status == "registered"
    assert (asset.root / "reference.json").exists()
    assert json.loads((asset.root / "reference.json").read_text(encoding="utf-8"))["reference_id"] == "demo_ref"


def test_register_reference_refuses_existing_without_overwrite(tmp_path):
    fasta = tmp_path / "genome.fa"
    fasta.write_text(">chr1\nACGT\n", encoding="utf-8")
    register_reference("demo", fasta=fasta, reference_dir=tmp_path / "references")

    with pytest.raises(FileExistsError):
        register_reference("demo", fasta=fasta, reference_dir=tmp_path / "references")


def test_list_and_load_references(tmp_path):
    fasta = tmp_path / "genome.fa"
    fasta.write_text(">chr1\nACGT\n", encoding="utf-8")
    register_reference("demo", fasta=fasta, reference_dir=tmp_path / "references")

    loaded = load_reference("demo", tmp_path / "references")
    listed = list_references(tmp_path / "references")

    assert loaded.reference_id == "demo"
    assert [asset.reference_id for asset in listed] == ["demo"]


def test_build_hisat2_index_command():
    assert build_hisat2_index_command("genome.fa", "hisat2/genome", threads=8) == [
        "hisat2-build",
        "-p",
        "8",
        "genome.fa",
        "hisat2/genome",
    ]


def test_build_hisat2_index_for_reference_dry_run(tmp_path):
    fasta = tmp_path / "genome.fa"
    fasta.write_text(">chr1\nACGT\n", encoding="utf-8")
    register_reference("demo", fasta=fasta, reference_dir=tmp_path / "references")
    context = RunContext(
        project_id="demo",
        work_dir=tmp_path,
        output_dir=tmp_path / "references" / "demo",
        config={"execution_mode": "local"},
        dry_run=True,
    )

    asset, result = build_hisat2_index_for_reference("demo", tmp_path / "references", context, threads=2)

    assert result.dry_run
    assert result.command == ["hisat2-build", "-p", "2", str(asset.fasta), str(asset.hisat2_index)]
    assert asset.build_status == "dry_run"


def test_reference_config_values_include_annotation(tmp_path):
    fasta = tmp_path / "genome.fa"
    fasta.write_text(">chr1\nACGT\n", encoding="utf-8")
    gtf = tmp_path / "genes.gtf"
    gtf.write_text('chr1\t.\texon\t1\t4\t.\t+\t.\tgene_id "geneA";\n', encoding="utf-8")
    asset = register_reference("demo", fasta=fasta, annotation=gtf, reference_dir=tmp_path / "references")

    values = reference_config_values(asset)

    assert values["reference_id"] == "demo"
    assert values["reference_dir"] == str(tmp_path / "references")
    assert values["hisat2_index"].endswith("hisat2/genome") or values["hisat2_index"].endswith("hisat2\\genome")
    assert values["featurecounts_annotation"].endswith("annotation.gtf")


def test_register_reference_with_existing_hisat2_index(tmp_path):
    fasta = tmp_path / "genome.fa"
    fasta.write_text(">chr1\nACGT\n", encoding="utf-8")
    prefix = tmp_path / "existing_index" / "genome"
    prefix.parent.mkdir()
    for index in range(1, 9):
        (prefix.parent / f"genome.{index}.ht2").write_text("", encoding="utf-8")

    asset = register_reference("demo", fasta=fasta, hisat2_index=prefix, reference_dir=tmp_path / "references")

    assert asset.hisat2_index == prefix
    assert load_reference("demo", tmp_path / "references").hisat2_index == prefix


def test_register_reference_records_source_metadata_and_rejects_mixed_provider(tmp_path):
    fasta = tmp_path / "genome.fa"
    fasta.write_text(">chr1\nACGT\n", encoding="utf-8")
    gtf = tmp_path / "genes.gtf"
    gtf.write_text('chr1\t.\texon\t1\t4\t.\t+\t.\tgene_id "geneA";\n', encoding="utf-8")

    asset = register_reference(
        "demo",
        fasta=fasta,
        annotation=gtf,
        reference_dir=tmp_path / "references",
        provider="ensembl",
        annotation_provider="ensembl",
        source_urls=["https://example.org/genome.fa.gz", "https://example.org/genes.gtf.gz"],
        species="glycine_max",
        assembly="GCF_demo",
        release="current",
        taxon_id="3847",
        annotation_format="gtf",
        created_by="download",
    )

    assert asset.provider == "ensembl"
    assert asset.annotation_provider == "ensembl"
    assert asset.source_urls[0].startswith("https://")
    assert asset.species == "glycine_max"

    with pytest.raises(ValueError, match="provider"):
        register_reference(
            "mixed",
            fasta=fasta,
            annotation=gtf,
            reference_dir=tmp_path / "references",
            provider="ensembl",
            annotation_provider="refseq",
        )


def test_check_reference_asset_reports_ok_for_complete_reference(tmp_path):
    fasta = tmp_path / "genome.fa"
    fasta.write_text(">chr1\nACGT\n", encoding="utf-8")
    gtf = tmp_path / "genes.gtf"
    gtf.write_text('chr1\t.\texon\t1\t4\t.\t+\t.\tgene_id "geneA";\n', encoding="utf-8")
    prefix = tmp_path / "references" / "demo" / "hisat2" / "genome"
    prefix.parent.mkdir(parents=True)
    for index in range(1, 9):
        (prefix.parent / f"genome.{index}.ht2").write_text("index", encoding="utf-8")

    asset = register_reference("demo", fasta=fasta, annotation=gtf, hisat2_index=prefix, reference_dir=tmp_path / "references")
    report = check_reference_asset(asset)

    assert report.ok
    assert report.issues == []


def test_load_reference_backward_compatibility(tmp_path):
    ref_root = tmp_path / "references" / "legacy"
    ref_root.mkdir(parents=True)
    fasta = ref_root / "genome.fa"
    fasta.write_text(">chr1\nACGT\n", encoding="utf-8")
    prefix = ref_root / "hisat2" / "genome"
    prefix.parent.mkdir(parents=True)
    for index in range(1, 9):
        (prefix.parent / f"genome.{index}.ht2").write_text("index", encoding="utf-8")
    legacy = {
        "reference_id": "legacy",
        "root": str(ref_root),
        "fasta": str(fasta),
        "annotation": None,
        "hisat2_index": str(prefix),
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:00:00",
        "notes": "old format",
    }
    (ref_root / "reference.json").write_text(json.dumps(legacy), encoding="utf-8")

    asset = load_reference("legacy", tmp_path / "references")

    assert asset.provider == "custom"
    assert asset.annotation_provider == "custom"
    assert asset.build_status == "unknown"


def test_register_reference_rejects_missing_hisat2_index(tmp_path):
    fasta = tmp_path / "genome.fa"
    fasta.write_text(">chr1\nACGT\n", encoding="utf-8")

    with pytest.raises(FileNotFoundError, match="HISAT2 index"):
        register_reference("demo", fasta=fasta, hisat2_index=tmp_path / "missing" / "genome", reference_dir=tmp_path / "references")
