from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, TaskProgressColumn, TextColumn, TimeElapsedColumn
from rich.table import Table
from rich.text import Text

from rnaseq_workflow.core.config import ProjectConfig
from rnaseq_workflow.core.config_validation import ConfigValidationResult
from rnaseq_workflow.core.models import Sample
from rnaseq_workflow.core.pipeline import PipelineEvent
from rnaseq_workflow.core.step_registry import expand_step_ids
from rnaseq_workflow.executors.local import LocalExecutor
from rnaseq_workflow.steps.download.models import BatchDownloadSummary


def print_header(console: Console, title: str, subtitle: str | None = None) -> None:
    text = Text(title, style="bold cyan")
    if subtitle:
        text.append("\n")
        text.append(subtitle, style="dim")
    console.print(Panel(text, border_style="cyan", box=box.ROUNDED))


def print_success(console: Console, message: str) -> None:
    console.print(f"[bold green]OK[/bold green] {message}")


def print_error(console: Console, message: str) -> None:
    console.print(f"[bold red]ERROR[/bold red] {message}")


def print_workflow_plan(console: Console, config: ProjectConfig, samples: list[Sample]) -> None:
    print_header(console, f"Workflow Plan: {config.project_id}", "Configuration overview before execution")

    overview = Table(box=box.SIMPLE_HEAVY)
    overview.add_column("Field", style="bold")
    overview.add_column("Value")
    overview.add_row("work_dir", str(config.work_dir))
    overview.add_row("output_dir", str(config.output_dir))
    overview.add_row("execution_mode", str(config.settings.get("execution_mode", "local")))
    overview.add_row("samples", str(len(samples)))
    console.print(overview)

    step_table = Table(title="Steps", box=box.SIMPLE)
    step_table.add_column("#", justify="right", style="dim")
    step_table.add_column("Configured")
    step_table.add_column("Concrete steps")
    configured_steps = config.steps or ["quality_control", "read_trimming", "alignment", "quantification"]
    expanded = expand_step_ids(config.steps)
    step_table.add_row("1", ", ".join(configured_steps), " -> ".join(expanded))
    console.print(step_table)

    sample_table = Table(title="Samples", box=box.SIMPLE)
    sample_table.add_column("Sample")
    sample_table.add_column("Layout")
    sample_table.add_column("Inputs")
    for sample in samples:
        sample_table.add_row(
            sample.sample_id,
            sample.layout.value,
            "\n".join(str(path) for path in sample.source_paths),
        )
    console.print(sample_table)


def print_validation_result(console: Console, result: ConfigValidationResult) -> None:
    title = "Config Validation"
    subtitle = "Ready to run" if result.ok else "Please fix the errors below"
    print_header(console, title, subtitle)

    table = Table(box=box.SIMPLE_HEAVY)
    table.add_column("Level")
    table.add_column("Field")
    table.add_column("Message")
    if not result.issues:
        table.add_row("[bold green]ok[/bold green]", "-", "configuration is valid")
    for issue in result.issues:
        style = "red" if issue.level == "error" else "yellow"
        table.add_row(f"[bold {style}]{issue.level}[/bold {style}]", issue.field, issue.message)
    console.print(table)


def print_run_summary(console: Console, state_path: Path) -> None:
    if not state_path.exists():
        print_error(console, f"state file not found: {state_path}")
        return
    with state_path.open("r", encoding="utf-8") as handle:
        state = json.load(handle)

    samples: dict[str, Any] = state.get("samples", {})
    counts = {"COMPLETED": 0, "FAILED": 0, "RUNNING": 0, "SKIPPED": 0, "CANCELLED": 0, "PAUSED": 0, "PENDING": 0}
    for sample_data in samples.values():
        for step in sample_data.get("steps", {}).values():
            status = str(step.get("status", "PENDING"))
            counts[status] = counts.get(status, 0) + 1

    print_header(console, "Run Summary", str(state_path))
    summary = Table(box=box.SIMPLE_HEAVY)
    summary.add_column("Metric")
    summary.add_column("Value", justify="right")
    summary.add_row("samples", str(len(samples)))
    summary.add_row("completed", str(counts.get("COMPLETED", 0)))
    summary.add_row("failed", str(counts.get("FAILED", 0)))
    summary.add_row("skipped", str(counts.get("SKIPPED", 0)))
    summary.add_row("paused", str(counts.get("PAUSED", 0)))
    console.print(summary)

    detail = Table(title="Sample Steps", box=box.SIMPLE)
    detail.add_column("Sample")
    detail.add_column("Step")
    detail.add_column("Status")
    detail.add_column("Message")
    for sample_id, sample_data in samples.items():
        for step_id, step in sample_data.get("steps", {}).items():
            status = str(step.get("status", ""))
            style = "green" if status == "COMPLETED" else "red" if status == "FAILED" else "yellow"
            detail.add_row(sample_id, step_id, f"[{style}]{status}[/{style}]", str(step.get("message", "")))
    console.print(detail)


def print_run_start(console: Console, config: ProjectConfig, samples: list[Sample], concrete_steps: list[str], dry_run: bool) -> None:
    mode = str(config.settings.get("execution_mode", "local"))
    subtitle = f"{len(samples)} sample(s), {len(concrete_steps)} step(s), mode={mode}, dry_run={dry_run}"
    print_header(console, f"Running Workflow: {config.project_id}", subtitle)


def run_executor_with_progress(console: Console, executor: LocalExecutor, samples: list[Sample], context) -> None:
    total_steps = len(samples) * len(executor.pipeline.steps)
    if total_steps == 0:
        executor.run(samples, context)
        return

    with Progress(
        TextColumn("[bold]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        overall_task = progress.add_task("Overall", total=total_steps)
        sample_tasks = {
            sample.sample_id: progress.add_task(f"{sample.sample_id}", total=len(executor.pipeline.steps))
            for sample in samples
        }

        def on_event(event: PipelineEvent) -> None:
            if event.event not in {"finished", "skipped_completed"}:
                return
            progress.advance(overall_task)
            progress.advance(sample_tasks[event.sample_id])

        previous = executor.pipeline.event_callback

        def chained(event: PipelineEvent) -> None:
            if previous is not None:
                previous(event)
            on_event(event)

        executor.pipeline.event_callback = chained
        try:
            executor.run(samples, context)
        finally:
            executor.pipeline.event_callback = previous


def run_download_manager_with_progress(console: Console, manager: Any, requests: list[Any], dry_run: bool) -> BatchDownloadSummary:
    if not requests:
        return BatchDownloadSummary()

    with Progress(
        TextColumn("[bold]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TextColumn("{task.fields[status]}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        overall_task = progress.add_task("Downloads", total=len(requests), status="")
        tasks = {
            request.accession: progress.add_task(request.accession, total=100, status="pending")
            for request in requests
        }

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(manager.download_many, requests, dry_run)
            while not future.done():
                _refresh_download_progress(progress, manager, tasks, overall_task)
                time.sleep(0.2)
            summary = future.result()
            _refresh_download_progress(progress, manager, tasks, overall_task)
            completed = summary.completed + summary.failed + summary.cancelled + summary.skipped
            progress.update(overall_task, completed=completed)
            return summary


def _refresh_download_progress(progress: Progress, manager: Any, tasks: dict[str, Any], overall_task: Any) -> None:
    terminal = {"COMPLETED", "FAILED", "CANCELLED", "SKIPPED"}
    done = 0
    for accession, task_id in tasks.items():
        row = manager.get_progress(accession)
        if row is None:
            continue
        status = row.status.value
        if status in terminal:
            done += 1
        completed = 100 if status in terminal else row.percent or 0
        detail = f"{status} {_format_bytes(row.downloaded_bytes)}"
        if row.speed_bps:
            detail += f" {_format_bytes(row.speed_bps)}/s"
        progress.update(task_id, completed=completed, status=detail)
    progress.update(overall_task, completed=done)


def _format_bytes(value: float | int) -> str:
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(size) < 1024 or unit == "TB":
            return f"{size:.1f}{unit}" if unit != "B" else f"{size:.0f}B"
        size /= 1024
    return f"{size:.1f}TB"


def print_finalize_result(console: Console, result: Any) -> None:
    print_header(console, "Project Finalize", "Project-level matrix and reports")
    table = Table(box=box.SIMPLE_HEAVY)
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("samples", str(result.sample_count))
    table.add_row("genes", str(result.gene_count))
    table.add_row("count_tables", str(len(result.count_tables)))
    table.add_row("counts_matrix", str(result.counts_matrix))
    table.add_row("report_json", str(result.report_json))
    table.add_row("report_markdown", str(result.report_markdown))
    console.print(table)


def print_download_results(console: Console, results: list[Any], title: str = "Download Results") -> None:
    completed = sum(1 for result in results if str(result.status.value) == "COMPLETED")
    failed = sum(1 for result in results if str(result.status.value) == "FAILED")
    skipped = sum(1 for result in results if str(result.status.value) == "SKIPPED")
    print_header(console, title, f"total={len(results)}, completed={completed}, failed={failed}, skipped={skipped}")

    table = Table(box=box.SIMPLE_HEAVY)
    table.add_column("Accession")
    table.add_column("Status")
    table.add_column("Cached")
    table.add_column("Bytes", justify="right")
    table.add_column("Speed B/s", justify="right")
    table.add_column("Path")
    table.add_column("Message")
    for result in results:
        status = result.status.value
        style = "green" if status == "COMPLETED" else "red" if status == "FAILED" else "yellow"
        table.add_row(
            result.accession,
            f"[{style}]{status}[/{style}]",
            str(result.cached),
            str(result.downloaded_bytes),
            f"{result.speed_bps:.2f}",
            "" if result.local_path is None else str(result.local_path),
            result.message,
        )
    console.print(table)


def print_matrix_result(console: Console, sample_count: int, gene_count: int, output: Path) -> None:
    print_header(console, "Raw Counts Matrix", "Merged featureCounts tables")
    table = Table(box=box.SIMPLE_HEAVY)
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("samples", str(sample_count))
    table.add_row("genes", str(gene_count))
    table.add_row("output", str(output))
    console.print(table)


def print_report_summary(console: Console, report: Any, json_output: Path | None, markdown_output: Path | None) -> None:
    print_header(console, f"Report Summary: {report.project_id}", "Project-level report outputs")
    table = Table(box=box.SIMPLE_HEAVY)
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("samples", str(report.sample_count))
    table.add_row("steps", str(report.step_status.total))
    table.add_row("completed", str(report.step_status.completed))
    table.add_row("failed", str(report.step_status.failed))
    if report.counts_matrix:
        table.add_row("count_genes", str(report.counts_matrix.gene_count))
        table.add_row("count_samples", str(report.counts_matrix.sample_count))
    if json_output:
        table.add_row("json", str(json_output))
    if markdown_output:
        table.add_row("markdown", str(markdown_output))
    console.print(table)


def print_doctor_checks(console: Console, checks: list[Any]) -> None:
    ok = all(check.ok for check in checks)
    print_header(console, "CLI Doctor", "Environment looks ready" if ok else "Some checks failed")
    table = Table(box=box.SIMPLE_HEAVY)
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Message")
    for check in checks:
        style = "green" if check.ok else "red"
        table.add_row(check.name, f"[{style}]{'OK' if check.ok else 'FAIL'}[/{style}]", check.message)
    console.print(table)


def print_config_summary(console: Console, config: ProjectConfig) -> None:
    print_header(console, f"Config: {config.project_id}", "Resolved workflow configuration")
    table = Table(box=box.SIMPLE_HEAVY)
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("work_dir", str(config.work_dir))
    table.add_row("output_dir", str(config.output_dir))
    table.add_row("samples", str(len(config.samples)))
    table.add_row("steps", ", ".join(config.steps or ["default"]))
    for key, value in sorted(config.settings.items()):
        table.add_row(key, str(value))
    console.print(table)
