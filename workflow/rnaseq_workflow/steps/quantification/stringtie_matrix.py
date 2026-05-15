from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class StringTieAbundanceTable:
    sample_id: str
    source_path: Path
    fpkm: dict[str, float]
    tpm: dict[str, float]


@dataclass(frozen=True, slots=True)
class StringTieExpressionMatrix:
    sample_ids: list[str]
    gene_ids: list[str]
    values: dict[str, dict[str, float]]


def infer_sample_id_from_stringtie_path(path: str | Path) -> str:
    name = Path(path).name
    for suffix in (".stringtie.gene_abund.tsv", ".gene_abund.tsv", ".tsv", ".tab"):
        if name.lower().endswith(suffix):
            return name[: -len(suffix)]
    return Path(name).stem


def read_stringtie_gene_abundance(path: str | Path, sample_id: str | None = None) -> StringTieAbundanceTable:
    table_path = Path(path)
    inferred_sample_id = sample_id or infer_sample_id_from_stringtie_path(table_path)
    fpkm: dict[str, float] = {}
    tpm: dict[str, float] = {}
    with table_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if not reader.fieldnames:
            raise ValueError(f"StringTie abundance file is empty: {table_path}")
        gene_key = _first_existing(reader.fieldnames, ["Gene ID", "Gene_ID", "gene_id", "Geneid"])
        fpkm_key = _first_existing(reader.fieldnames, ["FPKM", "fpkm"])
        tpm_key = _first_existing(reader.fieldnames, ["TPM", "tpm"])
        if not gene_key or not fpkm_key or not tpm_key:
            raise ValueError(f"invalid StringTie abundance header: {table_path}")
        for row in reader:
            gene_id = str(row.get(gene_key) or "").strip()
            if not gene_id:
                continue
            fpkm[gene_id] = _float_value(row.get(fpkm_key))
            tpm[gene_id] = _float_value(row.get(tpm_key))
    return StringTieAbundanceTable(sample_id=inferred_sample_id, source_path=table_path, fpkm=fpkm, tpm=tpm)


def merge_stringtie_abundance_files(paths: list[str | Path], value: str) -> StringTieExpressionMatrix:
    return merge_stringtie_abundance_tables([read_stringtie_gene_abundance(path) for path in paths], value=value)


def merge_stringtie_abundance_tables(tables: list[StringTieAbundanceTable], value: str) -> StringTieExpressionMatrix:
    if not tables:
        raise ValueError("at least one StringTie abundance table is required")
    value_key = value.strip().lower()
    if value_key not in {"fpkm", "tpm"}:
        raise ValueError(f"unsupported StringTie value: {value}")
    sample_ids: list[str] = []
    seen_samples: set[str] = set()
    gene_ids: list[str] = []
    seen_genes: set[str] = set()
    values: dict[str, dict[str, float]] = {}
    for table in tables:
        if table.sample_id in seen_samples:
            raise ValueError(f"duplicate sample id: {table.sample_id}")
        seen_samples.add(table.sample_id)
        sample_ids.append(table.sample_id)
        source = table.fpkm if value_key == "fpkm" else table.tpm
        for gene_id, expression in source.items():
            if gene_id not in seen_genes:
                seen_genes.add(gene_id)
                gene_ids.append(gene_id)
            values.setdefault(gene_id, {})[table.sample_id] = expression
    return StringTieExpressionMatrix(sample_ids=sample_ids, gene_ids=gene_ids, values=values)


def write_stringtie_matrix_tsv(matrix: StringTieExpressionMatrix, output_path: str | Path) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(["Geneid", *matrix.sample_ids])
        for gene_id in matrix.gene_ids:
            writer.writerow([gene_id, *[_format_float(matrix.values.get(gene_id, {}).get(sample_id, 0.0)) for sample_id in matrix.sample_ids]])


def _first_existing(fieldnames: list[str], candidates: list[str]) -> str | None:
    fields = {field.strip(): field for field in fieldnames}
    for candidate in candidates:
        if candidate in fields:
            return fields[candidate]
    return None


def _float_value(value: object) -> float:
    try:
        return float(str(value or "0").strip())
    except ValueError:
        return 0.0


def _format_float(value: float) -> str:
    if value == 0:
        return "0"
    return f"{value:.6f}".rstrip("0").rstrip(".")
