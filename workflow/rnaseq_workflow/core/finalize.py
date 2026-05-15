from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from rnaseq_workflow.core.models import Sample
from rnaseq_workflow.core.paths import project_paths
from rnaseq_workflow.steps.quantification import (
    merge_featurecounts_files,
    merge_stringtie_abundance_files,
    write_count_matrix_tsv,
    write_normalized_matrix_tsv,
    write_stringtie_matrix_tsv,
)
from rnaseq_workflow.steps.reporting import build_project_report, write_report_json, write_report_markdown


@dataclass(frozen=True, slots=True)
class FinalizeResult:
    count_tables: list[Path]
    counts_matrix: Path
    report_json: Path
    report_markdown: Path
    sample_count: int
    gene_count: int
    expression_matrices: dict[str, Path] | None = None


def finalize_project(
    project_id: str,
    output_dir: str | Path,
    samples: list[Sample],
    counts_matrix: str | Path | None = None,
    report_json: str | Path | None = None,
    report_markdown: str | Path | None = None,
    state_path: str | Path | None = None,
    output_formats: list[str] | None = None,
) -> FinalizeResult:
    paths = project_paths(Path(output_dir))
    reports_dir = paths.reports_dir
    reports_dir.mkdir(parents=True, exist_ok=True)

    formats = normalize_expression_output_formats(output_formats)
    count_tables = _default_featurecounts_tables(paths.root, samples)
    needs_featurecounts = any(item in formats for item in {"raw_counts", "cpm", "fpkm", "tpm"})
    missing = [path for path in count_tables if not path.exists()] if needs_featurecounts else []
    if missing:
        missing_text = ", ".join(str(path) for path in missing)
        raise FileNotFoundError(f"featureCounts output not found: {missing_text}")

    counts_matrix_path = Path(counts_matrix) if counts_matrix else reports_dir / "raw_counts.tsv"
    report_json_path = Path(report_json) if report_json else reports_dir / "report.json"
    report_markdown_path = Path(report_markdown) if report_markdown else reports_dir / "report.md"

    matrix = merge_featurecounts_files(count_tables) if needs_featurecounts else None
    expression_matrices: dict[str, Path] = {}
    if "raw_counts" in formats:
        if matrix is None:
            raise FileNotFoundError("featureCounts output not found for raw_counts")
        write_count_matrix_tsv(matrix, counts_matrix_path)
        expression_matrices["raw_counts"] = counts_matrix_path
    for method in ("cpm", "fpkm", "tpm"):
        if method in formats:
            if matrix is None:
                raise FileNotFoundError(f"featureCounts output not found for {method}")
            output_path = reports_dir / f"{method}.tsv"
            write_normalized_matrix_tsv(matrix, output_path, method)
            expression_matrices[method] = output_path
    for method in ("stringtie_fpkm", "stringtie_tpm"):
        if method in formats:
            abundance_tables = _default_stringtie_abundance_tables(paths.root, samples)
            missing_stringtie = [path for path in abundance_tables if not path.exists()]
            if missing_stringtie:
                missing_text = ", ".join(str(path) for path in missing_stringtie)
                raise FileNotFoundError(f"StringTie abundance output not found: {missing_text}")
            value = "fpkm" if method == "stringtie_fpkm" else "tpm"
            output_path = reports_dir / f"{method}.tsv"
            write_stringtie_matrix_tsv(merge_stringtie_abundance_files(abundance_tables, value=value), output_path)
            expression_matrices[method] = output_path

    primary_matrix = expression_matrices.get("raw_counts") or next(iter(expression_matrices.values()))
    artifacts = [*expression_matrices.values(), *count_tables]
    report = build_project_report(
        project_id=project_id,
        output_dir=paths.root,
        state_path=state_path or paths.state_file,
        counts_matrix_path=primary_matrix,
        artifact_paths=artifacts,
    )
    write_report_json(report, report_json_path)
    write_report_markdown(report, report_markdown_path)

    return FinalizeResult(
        count_tables=count_tables,
        counts_matrix=primary_matrix,
        report_json=report_json_path,
        report_markdown=report_markdown_path,
        sample_count=_matrix_sample_count(expression_matrices, matrix),
        gene_count=_matrix_gene_count(expression_matrices, matrix),
        expression_matrices=expression_matrices,
    )


def _default_featurecounts_tables(output_dir: Path, samples: list[Sample]) -> list[Path]:
    paths = project_paths(output_dir)
    return [paths.quantification_dir(sample) / f"{sample.sample_id}.featureCounts.txt" for sample in samples]


def _default_stringtie_abundance_tables(output_dir: Path, samples: list[Sample]) -> list[Path]:
    paths = project_paths(output_dir)
    return [paths.quantification_dir(sample) / f"{sample.sample_id}.stringtie.gene_abund.tsv" for sample in samples]


def normalize_expression_output_formats(formats: list[str] | None) -> list[str]:
    requested = formats or ["raw_counts"]
    aliases = {
        "raw": "raw_counts",
        "counts": "raw_counts",
        "count_matrix": "raw_counts",
        "raw_counts": "raw_counts",
        "cpm": "cpm",
        "fpkm": "fpkm",
        "tpm": "tpm",
        "stringtie_fpkm": "stringtie_fpkm",
        "stringtie_tpm": "stringtie_tpm",
    }
    normalized: list[str] = []
    for item in requested:
        key = aliases.get(str(item).strip().lower())
        if key and key not in normalized:
            normalized.append(key)
    if not normalized:
        raise ValueError("at least one expression output format is required")
    return normalized


def _matrix_sample_count(expression_matrices: dict[str, Path], matrix) -> int:
    if matrix is not None:
        return len(matrix.sample_ids)
    first = next(iter(expression_matrices.values()), None)
    if first is None or not first.exists():
        return 0
    with first.open("r", encoding="utf-8") as handle:
        header = handle.readline().rstrip("\n").split("\t")
    return max(0, len(header) - 1)


def _matrix_gene_count(expression_matrices: dict[str, Path], matrix) -> int:
    if matrix is not None:
        return len(matrix.gene_ids)
    first = next(iter(expression_matrices.values()), None)
    if first is None or not first.exists():
        return 0
    with first.open("r", encoding="utf-8") as handle:
        next(handle, None)
        return sum(1 for line in handle if line.strip())
