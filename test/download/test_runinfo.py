from __future__ import annotations

import json

from rnaseq_workflow.steps.download.runinfo import (
    SraRunMetadata,
    fetch_sra_metadata,
    fetch_sra_run_size_bytes,
    group_sra_metadata,
    load_sra_metadata_sidecar,
    metadata_has_mixed_groups,
    write_sra_metadata_sidecars,
)


def test_fetch_sra_run_size_bytes(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b"Run,size_MB\nSRR1,2.5\n"

    monkeypatch.setattr("urllib.request.urlopen", lambda url, timeout: Response())

    assert fetch_sra_run_size_bytes("SRR1") == int(2.5 * 1024 * 1024)


def test_fetch_sra_metadata_maps_runinfo_fields(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return (
                b"Run,BioProject,BioSample,TaxID,ScientificName,LibraryStrategy,LibrarySource,LibraryLayout,size_MB\n"
                b"SRR1,PRJ1,SAM1,2697049,Severe acute respiratory syndrome coronavirus 2,RNA-Seq,VIRAL RNA,SINGLE,12\n"
            )

    monkeypatch.setattr("urllib.request.urlopen", lambda url, timeout: Response())

    metadata = fetch_sra_metadata(["SRR1"])

    assert metadata[0].run == "SRR1"
    assert metadata[0].bioproject == "PRJ1"
    assert metadata[0].scientific_name == "Severe acute respiratory syndrome coronavirus 2"
    assert metadata[0].library_layout == "SINGLE"
    assert metadata[0].metadata_source == "ncbi_runinfo"
    assert metadata[0].raw["size_MB"] == "12"


def test_group_sra_metadata_detects_mixed_biological_groups():
    metadata = [
        SraRunMetadata(
            run="SRR19820386",
            bioproject="PRJNA736036",
            taxid="2697049",
            scientific_name="Severe acute respiratory syndrome coronavirus 2",
            library_layout="SINGLE",
            library_source="VIRAL RNA",
        ),
        SraRunMetadata(
            run="SRR19820387",
            bioproject="PRJNA736036",
            taxid="2697049",
            scientific_name="Severe acute respiratory syndrome coronavirus 2",
            library_layout="SINGLE",
            library_source="VIRAL RNA",
        ),
        SraRunMetadata(
            run="SRR19820396",
            bioproject="PRJNA852287",
            taxid="4555",
            scientific_name="Setaria italica",
            library_layout="PAIRED",
            library_source="TRANSCRIPTOMIC",
        ),
    ]

    groups = group_sra_metadata(metadata)

    assert len(groups) == 2
    assert metadata_has_mixed_groups(metadata)
    grouped_runs = sorted(tuple(record.run for record in group.runs) for group in groups)
    assert grouped_runs == [("SRR19820386", "SRR19820387"), ("SRR19820396",)]


def test_write_and_load_sra_metadata_sidecar(tmp_path):
    record = SraRunMetadata(
        run="SRR1",
        bioproject="PRJ1",
        taxid="1",
        scientific_name="Example species",
        raw={"Run": "SRR1"},
    )

    written = write_sra_metadata_sidecars([record], tmp_path)
    loaded = load_sra_metadata_sidecar("SRR1", tmp_path)

    assert written == [tmp_path / "SRR1" / "metadata.json"]
    assert loaded == record
    data = json.loads(written[0].read_text(encoding="utf-8"))
    assert data["metadata_source"] == "ncbi_runinfo"
