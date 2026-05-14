# SRA metadata grouping notes

## Background

During alignment testing, several SRR accessions with nearby numbers were found to belong to different biological projects:

```text
SRR19820386  SARS-CoV-2  PRJNA736036  single-end Oxford Nanopore
SRR19820387  SARS-CoV-2  PRJNA736036  single-end Oxford Nanopore
SRR19820396  Setaria italica  PRJNA852287  paired-end Illumina RNA-seq
SRR19820397  Setaria italica  PRJNA852287  paired-end Illumina RNA-seq
SRR1982039   Drosophila melanogaster  PRJNA273558  paired-end Illumina RNA-seq
```

The nearby SRR identifiers made the mixed dataset look like one group, but the metadata showed that the accessions require different references and different biological interpretation.

## Optimization To Implement

Add an SRA metadata preflight step before downstream analysis.

The step should:

- Query authoritative run metadata by accession.
- Record `BioProject`, `BioSample`, `ScientificName`, `TaxID`, `LibraryStrategy`, `LibrarySource`, `LibraryLayout`, platform, and center.
- Group runs by compatible analysis units, at minimum:
  - organism / TaxID
  - BioProject
  - library layout
  - library strategy/source
- Warn or block when one workflow run mixes incompatible groups.
- Suggest matching reference assets for each group.

For the current mixed test set, the workflow should separate at least:

```text
SARS-CoV-2 group:
  SRR19820386
  SRR19820387
  reference: sarscov2_refseq_nc045512

Setaria italica group:
  SRR19820396
  SRR19820397
  reference: foxtail_millet / setaria reference

Drosophila group:
  SRR1982039
  reference: drosophila reference
```

## Metadata Consistency Across Download Sources

Downloads may come from SRA Toolkit, ENA FASTQ URLs, or other mirrors, but metadata should be keyed by accession and stored independently from the file transport.

Recommended rule:

```text
download source = how bytes were fetched
accession metadata = what the run biologically is
```

So an accession downloaded from ENA and the same accession downloaded from NCBI SRA should share the same canonical metadata record when the run accession is identical.

Implementation notes:

- Store metadata as sidecar JSON per accession, for example `downloads/{accession}/metadata.json`.
- Include `metadata_source`, `metadata_fetched_at`, and raw provider fields.
- Prefer NCBI RunInfo or ENA API as the canonical accession metadata source.
- If SRA and ENA metadata disagree, show a warning and keep both raw records.
- Never infer organism or BioProject from the directory name or from nearby SRR numbers.

## Acceptance Criteria

- TUI can preview accession groups before analysis.
- Downloaded files from different sources still attach to the same accession metadata.
- Alignment and quantification menus can filter samples by organism/reference-compatible group.
- Mixed groups require explicit confirmation before running together.
