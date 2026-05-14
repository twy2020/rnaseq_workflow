from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from rnaseq_workflow.core.models import Sample
from rnaseq_workflow.core.paths import project_paths
from rnaseq_workflow.steps.quantification import merge_featurecounts_files, write_count_matrix_tsv
from rnaseq_workflow.steps.reporting import build_project_report, write_report_json, write_report_markdown


@dataclass(frozen=True, slots=True)
class FinalizeResult:
    count_tables: list[Path]
    counts_matrix: Path
    report_json: Path
    report_markdown: Path
    sample_count: int
    gene_count: int


def finalize_project(
    project_id: str,
    output_dir: str | Path,
    samples: list[Sample],
    counts_matrix: str | Path | None = None,
    report_json: str | Path | None = None,
    report_markdown: str | Path | None = None,
    state_path: str | Path | None = None,
) -> FinalizeResult:
    paths = project_paths(Path(output_dir))
    reports_dir = paths.reports_dir
    reports_dir.mkdir(parents=True, exist_ok=True)

    count_tables = _default_featurecounts_tables(paths.root, samples)
    missing = [path for path in count_tables if not path.exists()]
    if missing:
        missing_text = ", ".join(str(path) for path in missing)
        raise FileNotFoundError(f"featureCounts output not found: {missing_text}")

    counts_matrix_path = Path(counts_matrix) if counts_matrix else reports_dir / "raw_counts.tsv"
    report_json_path = Path(report_json) if report_json else reports_dir / "report.json"
    report_markdown_path = Path(report_markdown) if report_markdown else reports_dir / "report.md"

    matrix = merge_featurecounts_files(count_tables)
    write_count_matrix_tsv(matrix, counts_matrix_path)

    artifacts = [counts_matrix_path, *count_tables]
    report = build_project_report(
        project_id=project_id,
        output_dir=paths.root,
        state_path=state_path or paths.state_file,
        counts_matrix_path=counts_matrix_path,
        artifact_paths=artifacts,
    )
    write_report_json(report, report_json_path)
    write_report_markdown(report, report_markdown_path)

    return FinalizeResult(
        count_tables=count_tables,
        counts_matrix=counts_matrix_path,
        report_json=report_json_path,
        report_markdown=report_markdown_path,
        sample_count=len(matrix.sample_ids),
        gene_count=len(matrix.gene_ids),
    )


def _default_featurecounts_tables(output_dir: Path, samples: list[Sample]) -> list[Path]:
    paths = project_paths(output_dir)
    return [paths.quantification_dir(sample) / f"{sample.sample_id}.featureCounts.txt" for sample in samples]
