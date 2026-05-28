from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class ArtifactSummary:
    path: str
    exists: bool
    size_bytes: int | None = None


@dataclass(frozen=True, slots=True)
class CountsMatrixSummary:
    path: str
    exists: bool
    sample_count: int = 0
    gene_count: int = 0
    sample_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class StepStatusSummary:
    total: int = 0
    completed: int = 0
    failed: int = 0
    running: int = 0
    skipped: int = 0
    cancelled: int = 0
    paused: int = 0
    pending: int = 0


@dataclass(frozen=True, slots=True)
class ProjectReport:
    project_id: str
    generated_at: str
    output_dir: str
    sample_count: int
    step_status: StepStatusSummary
    counts_matrix: CountsMatrixSummary | None = None
    artifacts: list[ArtifactSummary] = field(default_factory=list)
    tool_versions: dict[str, str] = field(default_factory=dict)
    quality_notes: list[dict[str, Any]] = field(default_factory=list)
    state_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def summarize_progress_state(path: str | Path | None) -> tuple[int, StepStatusSummary]:
    if path is None:
        return 0, StepStatusSummary()

    state_path = Path(path)
    if not state_path.exists():
        return 0, StepStatusSummary()

    with state_path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)

    samples = data.get("samples", {})
    counts = {
        "total": 0,
        "completed": 0,
        "failed": 0,
        "running": 0,
        "skipped": 0,
        "cancelled": 0,
        "paused": 0,
        "pending": 0,
    }
    for sample in samples.values():
        for step in sample.get("steps", {}).values():
            counts["total"] += 1
            status = str(step.get("status", "")).lower()
            if status in counts:
                counts[status] += 1

    return len(samples), StepStatusSummary(**counts)


def summarize_quality_notes(path: str | Path | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    state_path = Path(path)
    if not state_path.exists():
        return []
    with state_path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    notes: list[dict[str, Any]] = []
    for sample_id, sample in data.get("samples", {}).items():
        step = sample.get("steps", {}).get("fastqc_trimmed")
        if not isinstance(step, dict):
            continue
        extra = step.get("extra", {}) if isinstance(step.get("extra"), dict) else {}
        issues = extra.get("fastqc_issues") or []
        status = str(step.get("status") or "")
        if not issues and status != "PAUSED":
            continue
        notes.append(
            {
                "sample_id": sample_id,
                "step_id": "fastqc_trimmed",
                "status": status,
                "message": step.get("message", ""),
                "policy": extra.get("quality_policy"),
                "issue_count": len(issues) if isinstance(issues, list) else 0,
                "issues": issues if isinstance(issues, list) else [],
            }
        )
    return notes


def summarize_counts_matrix(path: str | Path | None) -> CountsMatrixSummary | None:
    if path is None:
        return None

    matrix_path = Path(path)
    if not matrix_path.exists():
        return CountsMatrixSummary(path=str(matrix_path), exists=False)

    with matrix_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle, delimiter="\t")
        header = next(reader, None)
        if header is None or not header or header[0] != "Geneid":
            raise ValueError(f"invalid counts matrix header: {matrix_path}")
        sample_ids = header[1:]
        gene_count = sum(1 for row in reader if row)

    return CountsMatrixSummary(
        path=str(matrix_path),
        exists=True,
        sample_count=len(sample_ids),
        gene_count=gene_count,
        sample_ids=sample_ids,
    )


def summarize_artifacts(paths: list[str | Path]) -> list[ArtifactSummary]:
    artifacts: list[ArtifactSummary] = []
    for raw_path in paths:
        path = Path(raw_path)
        artifacts.append(
            ArtifactSummary(
                path=str(path),
                exists=path.exists(),
                size_bytes=path.stat().st_size if path.exists() and path.is_file() else None,
            )
        )
    return artifacts


def build_project_report(
    project_id: str,
    output_dir: str | Path,
    state_path: str | Path | None = None,
    counts_matrix_path: str | Path | None = None,
    artifact_paths: list[str | Path] | None = None,
    tool_versions: dict[str, str] | None = None,
) -> ProjectReport:
    sample_count, step_status = summarize_progress_state(state_path)
    counts_matrix = summarize_counts_matrix(counts_matrix_path)
    return ProjectReport(
        project_id=project_id,
        generated_at=datetime.now().isoformat(timespec="seconds"),
        output_dir=str(output_dir),
        sample_count=sample_count,
        step_status=step_status,
        counts_matrix=counts_matrix,
        artifacts=summarize_artifacts(artifact_paths or []),
        tool_versions=tool_versions or {},
        quality_notes=summarize_quality_notes(state_path),
        state_path=str(state_path) if state_path is not None else None,
    )


def write_report_json(report: ProjectReport, output_path: str | Path) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(report.to_dict(), handle, ensure_ascii=False, indent=2)


def write_report_markdown(report: ProjectReport, output_path: str | Path) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# RNA-seq Workflow Report: {report.project_id}",
        "",
        f"- Generated at: {report.generated_at}",
        f"- Output dir: `{report.output_dir}`",
        f"- Samples in state: {report.sample_count}",
        "",
        "## Step Status",
        "",
        "| Status | Count |",
        "|---|---:|",
        f"| Total | {report.step_status.total} |",
        f"| Completed | {report.step_status.completed} |",
        f"| Failed | {report.step_status.failed} |",
        f"| Running | {report.step_status.running} |",
        f"| Skipped | {report.step_status.skipped} |",
        f"| Cancelled | {report.step_status.cancelled} |",
        f"| Paused | {report.step_status.paused} |",
        f"| Pending | {report.step_status.pending} |",
        "",
    ]

    if report.counts_matrix is not None:
        lines.extend(
            [
                "## Counts Matrix",
                "",
                f"- Path: `{report.counts_matrix.path}`",
                f"- Exists: {report.counts_matrix.exists}",
                f"- Samples: {report.counts_matrix.sample_count}",
                f"- Genes: {report.counts_matrix.gene_count}",
                f"- Sample IDs: {', '.join(report.counts_matrix.sample_ids)}",
                "",
            ]
        )

    if report.tool_versions:
        lines.extend(["## Tool Versions", "", "| Tool | Version |", "|---|---|"])
        for tool, version in report.tool_versions.items():
            lines.append(f"| {tool} | {version} |")
        lines.append("")

    if report.quality_notes:
        lines.extend(["## Quality Notes", "", "| Sample | Step | Status | Policy | Issues | Message |", "|---|---|---|---|---:|---|"])
        for note in report.quality_notes:
            lines.append(
                f"| {note.get('sample_id', '')} | {note.get('step_id', '')} | {note.get('status', '')} | "
                f"{note.get('policy', '')} | {note.get('issue_count', 0)} | {note.get('message', '')} |"
            )
        lines.append("")

    if report.artifacts:
        lines.extend(["## Artifacts", "", "| Path | Exists | Size bytes |", "|---|---:|---:|"])
        for artifact in report.artifacts:
            size = "" if artifact.size_bytes is None else str(artifact.size_bytes)
            lines.append(f"| `{artifact.path}` | {artifact.exists} | {size} |")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
