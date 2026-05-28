from __future__ import annotations

import re
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
    hisat2_summary: Path | None = None


def finalize_project(
    project_id: str,
    output_dir: str | Path,
    samples: list[Sample],
    reports_dir: str | Path | None = None,
    counts_matrix: str | Path | None = None,
    report_json: str | Path | None = None,
    report_markdown: str | Path | None = None,
    state_path: str | Path | None = None,
    output_formats: list[str] | None = None,
) -> FinalizeResult:
    paths = project_paths(Path(output_dir))
    report_root = Path(reports_dir) if reports_dir else paths.reports_dir
    report_root.mkdir(parents=True, exist_ok=True)

    formats = normalize_expression_output_formats(output_formats)
    count_tables = _default_featurecounts_tables(paths.root, samples)
    needs_featurecounts = any(item in formats for item in {"raw_counts", "cpm", "fpkm", "tpm"})
    missing = [path for path in count_tables if not path.exists()] if needs_featurecounts else []
    if missing:
        missing_text = ", ".join(str(path) for path in missing)
        raise FileNotFoundError(f"featureCounts output not found: {missing_text}")

    counts_matrix_path = Path(counts_matrix) if counts_matrix else report_root / "raw_counts.tsv"
    report_json_path = Path(report_json) if report_json else report_root / "report.json"
    report_markdown_path = Path(report_markdown) if report_markdown else report_root / "report.md"
    hisat2_summary_path = report_root / "hisat2_alignment_summary.tsv"

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
            output_path = report_root / f"{method}.tsv"
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
            output_path = report_root / f"{method}.tsv"
            write_stringtie_matrix_tsv(merge_stringtie_abundance_files(abundance_tables, value=value), output_path)
            expression_matrices[method] = output_path

    hisat2_rows = summarize_hisat2_logs(paths.root, samples)
    write_hisat2_alignment_summary_tsv(hisat2_rows, hisat2_summary_path)
    primary_matrix = expression_matrices.get("raw_counts") or next(iter(expression_matrices.values()))
    artifacts = [*expression_matrices.values(), hisat2_summary_path, *count_tables]
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
        hisat2_summary=hisat2_summary_path,
    )


def _default_featurecounts_tables(output_dir: Path, samples: list[Sample]) -> list[Path]:
    paths = project_paths(output_dir)
    return [paths.quantification_dir(sample) / f"{sample.sample_id}.featureCounts.txt" for sample in samples]


def _default_stringtie_abundance_tables(output_dir: Path, samples: list[Sample]) -> list[Path]:
    paths = project_paths(output_dir)
    return [paths.quantification_dir(sample) / f"{sample.sample_id}.stringtie.gene_abund.tsv" for sample in samples]


@dataclass(frozen=True, slots=True)
class Hisat2AlignmentSummary:
    sample_id: str
    total_reads: int | None
    aligned_reads: int | None
    alignment_rate: float | None
    log_path: Path


def summarize_hisat2_logs(output_dir: str | Path, samples: list[Sample]) -> list[Hisat2AlignmentSummary]:
    paths = project_paths(Path(output_dir))
    rows: list[Hisat2AlignmentSummary] = []
    for sample in samples:
        log_path = paths.alignment_dir(sample) / f"{sample.sample_id}.hisat2.log"
        rows.append(parse_hisat2_summary_log(log_path, sample.sample_id))
    return rows


def parse_hisat2_summary_log(path: str | Path, sample_id: str) -> Hisat2AlignmentSummary:
    log_path = Path(path)
    if not log_path.exists():
        return Hisat2AlignmentSummary(sample_id, None, None, None, log_path)
    text = log_path.read_text(encoding="utf-8", errors="replace")
    total_reads = _parse_hisat2_total_reads(text)
    alignment_rate = _parse_hisat2_alignment_rate(text)
    aligned_reads = _parse_hisat2_aligned_reads(text, total_reads)
    return Hisat2AlignmentSummary(sample_id, total_reads, aligned_reads, alignment_rate, log_path)


def write_hisat2_alignment_summary_tsv(rows: list[Hisat2AlignmentSummary], path: str | Path) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = ["样本ID\t总reads数\t成功比对reads数\t比对率"]
    for row in rows:
        total = "" if row.total_reads is None else str(row.total_reads)
        aligned = "" if row.aligned_reads is None else str(row.aligned_reads)
        rate = "" if row.alignment_rate is None else f"{row.alignment_rate:.2f}%"
        lines.append(f"{row.sample_id}\t{total}\t{aligned}\t{rate}")
    output.write_text("\n".join(lines), encoding="utf-8")
    return output


def _parse_hisat2_total_reads(text: str) -> int | None:
    match = re.search(r"^\s*(\d+)\s+reads;\s+of these:", text, flags=re.MULTILINE)
    return int(match.group(1)) if match else None


def _parse_hisat2_alignment_rate(text: str) -> float | None:
    match = re.search(r"^\s*([0-9]+(?:\.[0-9]+)?)%\s+overall alignment rate", text, flags=re.MULTILINE)
    return float(match.group(1)) if match else None


def _parse_hisat2_aligned_reads(text: str, total_reads: int | None) -> int | None:
    # Single-end summary lines report read counts directly.
    if " were unpaired; of these:" in text:
        single_matches = [
            int(value)
            for value in re.findall(
                r"^\s*(\d+)\s+\([^)]+\)\s+aligned\s+(?:0 times|exactly 1 time|>1 times)$",
                text,
                flags=re.MULTILINE,
            )
        ]
        if single_matches:
            return sum(single_matches[1:]) if len(single_matches) >= 3 else sum(single_matches)

    if total_reads is not None:
        aligned = _parse_pair_aligned_by_rate(text, total_reads)
        if aligned is not None:
            return aligned
    return None


def _parse_pair_aligned_by_rate(text: str, total_reads: int) -> int | None:
    rate = _parse_hisat2_alignment_rate(text)
    if rate is None:
        return None
    return round(total_reads * rate / 100)


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
