from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, slots=True)
class GeneCount:
    gene_id: str
    count: int


@dataclass(frozen=True, slots=True)
class SampleCountTable:
    sample_id: str
    source_path: Path
    counts: dict[str, int]
    lengths: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CountMatrix:
    sample_ids: list[str]
    gene_ids: list[str]
    counts: dict[str, dict[str, int]]
    lengths: dict[str, int] = field(default_factory=dict)


def infer_sample_id_from_featurecounts_path(path: str | Path) -> str:
    name = Path(path).name
    for suffix in (".featureCounts.txt", ".featurecounts.txt", ".counts.txt", ".txt"):
        if name.lower().endswith(suffix.lower()):
            return name[: -len(suffix)]
    return Path(name).stem


def read_featurecounts_table(path: str | Path, sample_id: str | None = None) -> SampleCountTable:
    table_path = Path(path)
    inferred_sample_id = sample_id or infer_sample_id_from_featurecounts_path(table_path)
    counts: dict[str, int] = {}
    lengths: dict[str, int] = {}

    with table_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader((line for line in handle if not line.startswith("#")), delimiter="\t")
        header = next(reader, None)
        if header is None:
            raise ValueError(f"featureCounts file is empty: {table_path}")
        if len(header) < 7 or header[0] != "Geneid":
            raise ValueError(f"invalid featureCounts header: {table_path}")

        for row in reader:
            if not row:
                continue
            if len(row) < len(header):
                raise ValueError(f"invalid featureCounts row in {table_path}: {row}")
            gene_id = row[0]
            try:
                lengths[gene_id] = int(float(row[5]))
            except ValueError:
                lengths[gene_id] = 0
            try:
                counts[gene_id] = int(row[-1])
            except ValueError as exc:
                raise ValueError(f"invalid count for gene {gene_id} in {table_path}: {row[-1]}") from exc

    return SampleCountTable(sample_id=inferred_sample_id, source_path=table_path, counts=counts, lengths=lengths)


def merge_count_tables(tables: list[SampleCountTable]) -> CountMatrix:
    if not tables:
        raise ValueError("at least one featureCounts table is required")

    sample_ids: list[str] = []
    seen_samples: set[str] = set()
    gene_ids: list[str] = []
    seen_genes: set[str] = set()
    counts: dict[str, dict[str, int]] = {}
    lengths: dict[str, int] = {}

    for table in tables:
        if table.sample_id in seen_samples:
            raise ValueError(f"duplicate sample id: {table.sample_id}")
        seen_samples.add(table.sample_id)
        sample_ids.append(table.sample_id)

        for gene_id, count in table.counts.items():
            if gene_id not in seen_genes:
                seen_genes.add(gene_id)
                gene_ids.append(gene_id)
                lengths[gene_id] = int(table.lengths.get(gene_id, 0))
            counts.setdefault(gene_id, {})[table.sample_id] = count

    return CountMatrix(sample_ids=sample_ids, gene_ids=gene_ids, counts=counts, lengths=lengths)


def merge_featurecounts_files(paths: list[str | Path]) -> CountMatrix:
    return merge_count_tables([read_featurecounts_table(path) for path in paths])


def write_count_matrix_tsv(matrix: CountMatrix, output_path: str | Path) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(["Geneid", *matrix.sample_ids])
        for gene_id in matrix.gene_ids:
            writer.writerow([gene_id, *[matrix.counts.get(gene_id, {}).get(sample_id, 0) for sample_id in matrix.sample_ids]])


def write_normalized_matrix_tsv(matrix: CountMatrix, output_path: str | Path, method: str) -> None:
    method_key = method.strip().lower()
    if method_key not in {"cpm", "fpkm", "tpm"}:
        raise ValueError(f"unsupported normalized matrix method: {method}")
    values = _normalized_values(matrix, method_key)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(["Geneid", *matrix.sample_ids])
        for gene_id in matrix.gene_ids:
            writer.writerow([gene_id, *[_format_expression(values.get(gene_id, {}).get(sample_id, 0.0)) for sample_id in matrix.sample_ids]])


def _normalized_values(matrix: CountMatrix, method: str) -> dict[str, dict[str, float]]:
    library_sizes = {
        sample_id: sum(matrix.counts.get(gene_id, {}).get(sample_id, 0) for gene_id in matrix.gene_ids)
        for sample_id in matrix.sample_ids
    }
    values: dict[str, dict[str, float]] = {gene_id: {} for gene_id in matrix.gene_ids}
    if method == "cpm":
        for gene_id in matrix.gene_ids:
            for sample_id in matrix.sample_ids:
                library = library_sizes.get(sample_id, 0)
                count = matrix.counts.get(gene_id, {}).get(sample_id, 0)
                values[gene_id][sample_id] = (count / library * 1_000_000) if library else 0.0
        return values

    rpk: dict[str, dict[str, float]] = {gene_id: {} for gene_id in matrix.gene_ids}
    for gene_id in matrix.gene_ids:
        length_kb = matrix.lengths.get(gene_id, 0) / 1000
        for sample_id in matrix.sample_ids:
            count = matrix.counts.get(gene_id, {}).get(sample_id, 0)
            rpk[gene_id][sample_id] = (count / length_kb) if length_kb > 0 else 0.0
    if method == "fpkm":
        for gene_id in matrix.gene_ids:
            for sample_id in matrix.sample_ids:
                library_m = library_sizes.get(sample_id, 0) / 1_000_000
                values[gene_id][sample_id] = (rpk[gene_id][sample_id] / library_m) if library_m else 0.0
        return values
    for sample_id in matrix.sample_ids:
        scaling = sum(rpk[gene_id][sample_id] for gene_id in matrix.gene_ids) / 1_000_000
        for gene_id in matrix.gene_ids:
            values[gene_id][sample_id] = (rpk[gene_id][sample_id] / scaling) if scaling else 0.0
    return values


def _format_expression(value: float) -> str:
    if value == 0:
        return "0"
    return f"{value:.6f}".rstrip("0").rstrip(".")
