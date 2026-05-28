from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from rnaseq_workflow.cli.interactive import run_interactive_console
from rnaseq_workflow.cli.tui import run_tui
from rnaseq_workflow.core.config import load_project_config
from rnaseq_workflow.core.assets import AssetWorkspace, cleanup_allowed_targets
from rnaseq_workflow.core.config_edit import set_config_value
from rnaseq_workflow.core.config_template import ConfigTemplateOptions, write_config_template
from rnaseq_workflow.core.config_validation import validate_project_config
from rnaseq_workflow.core.doctor import run_doctor_checks
from rnaseq_workflow.core.errors import ConfigError
from rnaseq_workflow.core.finalize import finalize_project
from rnaseq_workflow.core.logging import TaskLogManager
from rnaseq_workflow.core.models import RunContext, Sample, SampleLayout, StepStatus
from rnaseq_workflow.core.pipeline import Pipeline
from rnaseq_workflow.core.references import (
    ReferenceAsset,
    ReferenceCheckReport,
    check_reference_asset,
    build_hisat2_index_for_reference,
    list_references,
    load_reference,
    reference_config_values,
    register_reference,
)
from rnaseq_workflow.core.reference_sources import build_ensembl_reference_urls, prepare_reference_from_urls
from rnaseq_workflow.core.samples import samples_from_config
from rnaseq_workflow.core.step_registry import PIPELINE_STAGE_LABELS, build_pipeline_steps
from rnaseq_workflow.core.steps import PipelineStep
from rnaseq_workflow.cli.ui import (
    print_error,
    print_download_results,
    print_doctor_checks,
    print_config_summary,
    print_finalize_result as ui_print_finalize_result,
    print_matrix_result,
    print_report_summary,
    print_run_start,
    print_run_summary,
    print_success,
    print_validation_result,
    print_workflow_plan,
    run_download_manager_with_progress,
    run_executor_with_progress,
)
from rnaseq_workflow.executors.local import LocalExecutor
from rnaseq_workflow.persistence.json_state import JsonStateRepository
from rnaseq_workflow.steps.data_ingestion import SraToFastqStep, scan_inputs, write_manifest_csv, write_manifest_json
from rnaseq_workflow.steps.alignment import Hisat2AlignStep, SamtoolsSortStep
from rnaseq_workflow.steps.download import (
    DownloadManager,
    DownloadRequest,
    AutoDownloader,
    EnaFastqDownloader,
    PrefetchDownloader,
    fetch_sra_metadata,
    group_sra_metadata,
    looks_like_sra_accession,
    read_download_requests,
    smart_download,
    build_smart_download_requests,
    split_sra_targets,
    write_download_results_csv,
    write_download_results_json,
    write_sra_metadata_sidecars,
)
from rnaseq_workflow.steps.quality_control import FastQCStep
from rnaseq_workflow.steps.quantification import FeatureCountsStep, merge_featurecounts_files, write_count_matrix_tsv
from rnaseq_workflow.steps.reporting import build_project_report, write_report_json, write_report_markdown
from rnaseq_workflow.steps.read_trimming import TrimGaloreStep

app = typer.Typer(help="RNA-seq workflow CLI")
console = Console()


DEFAULT_STEPS = list(PIPELINE_STAGE_LABELS.items())


def _format_bytes(size: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f}{unit}" if unit != "B" else f"{int(value)}B"
        value /= 1024


@app.command("ui")
def ui_command(
    config: Path = typer.Option(Path("config.yaml"), help="Default workflow config path."),
) -> None:
    """Open the enhanced terminal workflow UI."""
    run_tui(console, default_config=config)


@app.command("tui")
def tui_command(
    config: Path = typer.Option(Path("config.yaml"), help="Default workflow config path."),
) -> None:
    """Open the enhanced terminal workflow UI."""
    run_tui(console, default_config=config)


@app.command("simple-ui")
def simple_ui_command(
    config: Path = typer.Option(Path("config.yaml"), help="Default workflow config path."),
) -> None:
    """Open the simple line-oriented terminal workflow menu."""
    run_interactive_console(console, default_config=config)


@app.command("doctor")
def doctor_command(
    check_docker_image: bool = typer.Option(True, help="Check rnaseq-workflow Docker image exists."),
    docker_image: str = typer.Option("rnaseq-workflow:tools", help="Docker image to inspect."),
) -> None:
    """Check CLI runtime environment."""
    checks = run_doctor_checks(check_docker_image=check_docker_image, image=docker_image)
    print_doctor_checks(console, checks)
    if not all(check.ok for check in checks):
        raise typer.Exit(code=1)


@app.command("assets-init")
def assets_init_command(
    asset_root: Path = typer.Option(Path("workspace"), help="Asset workspace root."),
) -> None:
    """Create the multi-user asset workspace base directories."""
    workspace = AssetWorkspace(asset_root)
    workspace.ensure()
    table = Table(title="Asset Workspace")
    table.add_column("Field")
    table.add_column("Path")
    table.add_row("root", str(workspace.root))
    table.add_row("shared references", str(workspace.global_reference_dir))
    table.add_row("shared reference downloads", str(workspace.global_reference_downloads_dir))
    table.add_row("users", str(workspace.users_dir))
    table.add_row("database", str(workspace.database_path))
    console.print(table)


@app.command("user-register")
def user_register_command(
    username: str = typer.Argument(..., help="Login username."),
    display_name: str = typer.Option("", help="Optional display name."),
    password: str = typer.Option(..., prompt=True, hide_input=True, confirmation_prompt=True, help="Login password."),
    asset_root: Path = typer.Option(Path("workspace"), help="Asset workspace root."),
) -> None:
    """Register a local terminal user in the workspace database."""
    workspace = AssetWorkspace(asset_root)
    try:
        user = workspace.database.create_user(username=username, password=password, display_name=display_name)
    except ValueError as exc:
        print_error(console, str(exc))
        raise typer.Exit(code=1) from exc
    workspace.ensure_user(user.user_id)
    console.print(f"[green]User registered:[/green] {user.username}  {user.user_id}")


@app.command("user-login")
def user_login_command(
    username: str = typer.Argument(..., help="Login username."),
    password: str = typer.Option(..., prompt=True, hide_input=True, help="Login password."),
    asset_root: Path = typer.Option(Path("workspace"), help="Asset workspace root."),
) -> None:
    """Login a local terminal user and persist the active session."""
    workspace = AssetWorkspace(asset_root)
    user = workspace.database.authenticate(username, password)
    if not user:
        print_error(console, "Username or password is incorrect.")
        raise typer.Exit(code=1)
    workspace.ensure_user(user.user_id)
    session_id = workspace.database.create_session(user.user_id)
    workspace.save_session(session_id, user.user_id)
    console.print(f"[green]Logged in:[/green] {user.username}  {user.user_id}")


@app.command("user-logout")
def user_logout_command(
    asset_root: Path = typer.Option(Path("workspace"), help="Asset workspace root."),
) -> None:
    """Logout the active local terminal user."""
    workspace = AssetWorkspace(asset_root)
    session = workspace.load_session()
    workspace.database.logout(session.get("session_id") if session else None)
    workspace.clear_session()
    console.print("[green]Logged out.[/green]")


@app.command("users-list")
def users_list_command(
    asset_root: Path = typer.Option(Path("workspace"), help="Asset workspace root."),
) -> None:
    """List users stored in the workspace database."""
    users = AssetWorkspace(asset_root).database.list_users()
    table = Table(title="Users")
    table.add_column("Username")
    table.add_column("User ID")
    table.add_column("Display")
    table.add_column("Created")
    table.add_column("Last Login")
    for user in users:
        table.add_row(user.username, user.user_id, user.display_name, user.created_at, user.last_login_at or "")
    console.print(table)


@app.command("tasks-list")
def tasks_list_command(
    user_id: str | None = typer.Option(None, help="Filter by user UUID."),
    asset_root: Path = typer.Option(Path("workspace"), help="Asset workspace root."),
) -> None:
    """List tasks stored in the workspace database."""
    tasks = AssetWorkspace(asset_root).database.list_tasks(user_id=user_id)
    table = Table(title="Tasks")
    table.add_column("Task ID")
    table.add_column("User ID")
    table.add_column("Name")
    table.add_column("Status")
    table.add_column("Updated")
    table.add_column("Directory")
    for task in tasks:
        table.add_row(task.task_id, task.user_id, task.task_name, task.status, task.updated_at, task.task_dir)
    console.print(table)


@app.command("cleanup-test-artifacts")
def cleanup_test_artifacts_command(
    root: Path = typer.Option(Path("."), help="Project root containing old runtime artifact directories."),
    execute: bool = typer.Option(False, help="Actually delete allowed artifact directories. Default is dry-run."),
) -> None:
    """Safely clean old runtime test artifacts only."""
    targets = cleanup_allowed_targets(root, dry_run=not execute)
    table = Table(title="Cleanup test artifacts" + ("" if execute else " dry-run"))
    table.add_column("Path")
    table.add_column("Exists")
    table.add_column("Allowed")
    table.add_column("Files", justify="right")
    table.add_column("Size", justify="right")
    for target in targets:
        table.add_row(
            str(target.path),
            str(target.exists),
            str(target.allowed),
            str(target.file_count),
            _format_bytes(target.size_bytes),
        )
    console.print(table)
    if not execute:
        console.print("[yellow]Dry-run only. Pass --execute to delete allowed targets.[/yellow]")
    else:
        console.print("[green]Allowed artifact targets removed.[/green]")


@app.command("init-config")
def init_config_command(
    output: Path = typer.Argument(..., help="Path to write the YAML config template."),
    project_id: str = typer.Option("rnaseq_project", help="Project id."),
    sample_id: str = typer.Option("S1", help="Example sample id."),
    source_path: str = typer.Option("data/S1.fastq.gz", help="Example sample source FASTQ/SRA path."),
    layout: str = typer.Option("single", help="Sample layout: single, paired, or unknown."),
    output_dir: str = typer.Option("output", help="Workflow output directory."),
    execution_mode: str = typer.Option("docker", help="Execution mode: docker or local."),
    overwrite: bool = typer.Option(False, help="Overwrite output config if it already exists."),
) -> None:
    """Create a starter YAML config for a workflow project."""
    options = ConfigTemplateOptions(
        project_id=project_id,
        output_dir=output_dir,
        sample_id=sample_id,
        source_path=source_path,
        layout=layout,
        execution_mode=execution_mode,
    )
    try:
        written = write_config_template(output, options=options, overwrite=overwrite)
    except FileExistsError as exc:
        print_error(console, f"Config error: {exc}")
        raise typer.Exit(code=1) from exc
    print_success(console, f"Config template written: {written}")


@app.command("config-show")
def config_show_command(config: Path) -> None:
    """Show resolved workflow config values."""
    cfg = load_project_config(config)
    print_config_summary(console, cfg)


@app.command("config-set")
def config_set_command(
    config: Path,
    key: str = typer.Argument(..., help="YAML key path, e.g. hisat2_threads or samples.0.source_path."),
    value: str = typer.Argument(..., help="Value parsed as YAML scalar/list/object."),
) -> None:
    """Set a value in a YAML config file."""
    try:
        set_config_value(config, key, value)
    except (ValueError, IndexError, KeyError) as exc:
        print_error(console, f"Config set error: {exc}")
        raise typer.Exit(code=1) from exc
    print_success(console, f"Updated {key} in {config}")


@app.command("reference-register")
def reference_register_command(
    reference_id: str = typer.Argument(..., help="Reference id, e.g. tair10 or hg38."),
    fasta: Path = typer.Option(..., help="Genome FASTA file."),
    annotation: Path | None = typer.Option(None, help="GTF/GFF annotation file for featureCounts."),
    hisat2_index: Path | None = typer.Option(None, help="Existing HISAT2 index prefix, e.g. references/hg38/hisat2/genome."),
    reference_dir: Path = typer.Option(Path("references"), help="Managed reference directory."),
    copy_files: bool = typer.Option(True, help="Copy FASTA/GTF into the managed reference directory."),
    overwrite: bool = typer.Option(False, help="Overwrite existing reference metadata and managed files."),
    notes: str = typer.Option("", help="Optional notes stored in reference metadata."),
    provider: str = typer.Option("custom", help="Source provider, e.g. ensembl, refseq, custom."),
    annotation_provider: str | None = typer.Option(None, help="Annotation provider, defaults to provider."),
    species: str | None = typer.Option(None, help="Species name recorded in metadata."),
    assembly: str | None = typer.Option(None, help="Assembly name recorded in metadata."),
    release: str | None = typer.Option(None, help="Release recorded in metadata."),
    taxon_id: str | None = typer.Option(None, help="Taxon id recorded in metadata."),
    source_url: list[str] = typer.Option([], "--source-url", help="Original source URL, repeatable."),
    annotation_format: str | None = typer.Option(None, help="Annotation format, e.g. gtf, gff3."),
    created_by: str = typer.Option("manual", help="Metadata provenance label."),
    build_status: str = typer.Option("registered", help="Initial build status label."),
    allow_mixed_source: bool = typer.Option(False, help="Allow FASTA and annotation providers from different families."),
) -> None:
    """Register a genome FASTA and optional GTF/GFF annotation as a managed reference."""
    try:
        asset = register_reference(
            reference_id=reference_id,
            fasta=fasta,
            annotation=annotation,
            hisat2_index=hisat2_index,
            reference_dir=reference_dir,
            copy_files=copy_files,
            overwrite=overwrite,
            notes=notes,
            provider=provider,
            annotation_provider=annotation_provider,
            species=species,
            assembly=assembly,
            release=release,
            taxon_id=taxon_id,
            source_urls=source_url,
            annotation_format=annotation_format,
            created_by=created_by,
            build_status=build_status,
            allow_mixed_source=allow_mixed_source,
        )
    except (FileExistsError, FileNotFoundError, ValueError) as exc:
        print_error(console, f"Reference error: {exc}")
        raise typer.Exit(code=1) from exc
    _print_reference_asset(asset, title=f"Reference Registered: {asset.reference_id}")


@app.command("reference-list")
def reference_list_command(
    reference_dir: Path = typer.Option(Path("references"), help="Managed reference directory."),
) -> None:
    """List managed references."""
    assets = list_references(reference_dir)
    table = Table(title=f"References: {reference_dir}")
    table.add_column("ID")
    table.add_column("Provider")
    table.add_column("Build")
    table.add_column("FASTA")
    table.add_column("Annotation")
    table.add_column("HISAT2 index")
    table.add_column("Updated")
    for asset in assets:
        table.add_row(
            asset.reference_id,
            asset.provider,
            asset.build_status,
            str(asset.fasta),
            "" if asset.annotation is None else str(asset.annotation),
            str(asset.hisat2_index),
            asset.updated_at,
        )
    console.print(table)


@app.command("reference-show")
def reference_show_command(
    reference_id: str,
    reference_dir: Path = typer.Option(Path("references"), help="Managed reference directory."),
) -> None:
    """Show one managed reference."""
    try:
        asset = load_reference(reference_id, reference_dir)
    except FileNotFoundError as exc:
        print_error(console, f"Reference error: {exc}")
        raise typer.Exit(code=1) from exc
    _print_reference_asset(asset, title=f"Reference: {asset.reference_id}")


@app.command("reference-check")
def reference_check_command(
    reference_id: str,
    reference_dir: Path = typer.Option(Path("references"), help="Managed reference directory."),
) -> None:
    """Check a managed reference for missing files and index completeness."""
    try:
        asset = load_reference(reference_id, reference_dir)
    except FileNotFoundError as exc:
        print_error(console, f"Reference error: {exc}")
        raise typer.Exit(code=1) from exc
    report = check_reference_asset(asset)
    _print_reference_check_report(report)
    if not report.ok:
        raise typer.Exit(code=1)


@app.command("reference-build-hisat2")
def reference_build_hisat2_command(
    reference_id: str,
    reference_dir: Path = typer.Option(Path("references"), help="Managed reference directory."),
    threads: int = typer.Option(4, min=1, help="hisat2-build thread count."),
    execution_mode: str = typer.Option("docker", help="Execution mode: docker or local."),
    docker_image: str = typer.Option("rnaseq-workflow:tools", help="Docker image containing hisat2-build."),
    docker_workspace: Path = typer.Option(Path("."), help="Docker bind workspace."),
    force: bool = typer.Option(False, help="Rebuild even when index files already exist."),
    dry_run: bool = typer.Option(True, help="Build and record the command without executing it."),
) -> None:
    """Build HISAT2 index files for a managed reference from its FASTA."""
    context = RunContext(
        project_id=f"reference_{reference_id}",
        work_dir=Path.cwd(),
        output_dir=reference_root_for_cli(reference_dir, reference_id),
        config={
            "execution_mode": execution_mode,
            "docker_image": docker_image,
            "docker_workspace": str(docker_workspace),
        },
        dry_run=dry_run,
    )
    try:
        asset, result = build_hisat2_index_for_reference(
            reference_id=reference_id,
            reference_dir=reference_dir,
            context=context,
            threads=threads,
            force=force,
        )
    except (FileExistsError, FileNotFoundError) as exc:
        print_error(console, f"Reference error: {exc}")
        raise typer.Exit(code=1) from exc

    _print_reference_asset(asset, title=f"HISAT2 Index: {asset.reference_id}")
    _print_command_result(result)
    if not result.ok:
        raise typer.Exit(code=result.return_code or 1)


@app.command("reference-use")
def reference_use_command(
    config: Path,
    reference_id: str,
    reference_dir: Path = typer.Option(Path("references"), help="Managed reference directory."),
) -> None:
    """Write managed reference paths into a workflow config file."""
    try:
        asset = load_reference(reference_id, reference_dir)
    except FileNotFoundError as exc:
        print_error(console, f"Reference error: {exc}")
        raise typer.Exit(code=1) from exc
    report = check_reference_asset(asset)
    if not report.ok:
        _print_reference_check_report(report)
        raise typer.Exit(code=1)
    values = reference_config_values(asset)
    for key, value in values.items():
        set_config_value(config, key, value)
    print_success(console, f"Config updated with reference {asset.reference_id}: {config}")
    _print_reference_asset(asset, title=f"Reference Configured: {asset.reference_id}")


@app.command("reference-prepare")
def reference_prepare_command(
    reference_id: str = typer.Argument(..., help="Reference id to create."),
    fasta_url: str | None = typer.Option(None, help="Genome FASTA URL. Use with --annotation-url."),
    annotation_url: str | None = typer.Option(None, help="GTF/GFF URL. Use with --fasta-url."),
    provider: str = typer.Option("custom", help="Source provider, e.g. ensembl, refseq, custom."),
    annotation_provider: str | None = typer.Option(None, help="Annotation provider, defaults to provider."),
    species: str | None = typer.Option(None, help="Ensembl species name, e.g. homo_sapiens or Arabidopsis thaliana."),
    division: str = typer.Option("vertebrates", help="Ensembl division: vertebrates, plants, fungi, metazoa, protists."),
    release: str = typer.Option("current", help="Ensembl release number or current."),
    assembly: str | None = typer.Option(None, help="Assembly name recorded in metadata."),
    taxon_id: str | None = typer.Option(None, help="Taxon id recorded in metadata."),
    reference_dir: Path = typer.Option(Path("references"), help="Managed reference directory."),
    download_dir: Path = typer.Option(Path("reference_downloads"), help="Raw download cache directory."),
    config: Path | None = typer.Option(None, help="Optional config file to update after preparing reference."),
    threads: int = typer.Option(4, min=1, help="hisat2-build thread count."),
    execution_mode: str = typer.Option("docker", help="Execution mode for hisat2-build: docker or local."),
    docker_image: str = typer.Option("rnaseq-workflow:tools", help="Docker image containing hisat2-build."),
    docker_workspace: Path = typer.Option(Path("."), help="Docker bind workspace."),
    build_index: bool = typer.Option(True, help="Build HISAT2 index after downloading."),
    force: bool = typer.Option(False, help="Overwrite downloads, reference metadata, and index files."),
    dry_run_index: bool = typer.Option(False, help="Download/register files but only print hisat2-build command."),
    allow_mixed_source: bool = typer.Option(False, help="Allow FASTA and annotation providers from different families."),
) -> None:
    """Download FASTA+GTF, register them, build HISAT2 index, and optionally update config."""
    if species:
        try:
            fasta_url, annotation_url = build_ensembl_reference_urls(species, division=division, release=release)
            provider = "ensembl"
            annotation_provider = annotation_provider or provider
        except (FileNotFoundError, ValueError) as exc:
            print_error(console, f"Ensembl lookup error: {exc}")
            raise typer.Exit(code=1) from exc
    if not fasta_url or not annotation_url:
        print_error(console, "Provide either --species or both --fasta-url and --annotation-url")
        raise typer.Exit(code=1)

    context = RunContext(
        project_id=f"reference_{reference_id}",
        work_dir=Path.cwd(),
        output_dir=reference_dir / reference_id,
        config={
            "execution_mode": execution_mode,
            "docker_image": docker_image,
            "docker_workspace": str(docker_workspace),
        },
        dry_run=dry_run_index,
    )
    try:
        prepared = prepare_reference_from_urls(
            reference_id=reference_id,
            fasta_url=fasta_url,
            annotation_url=annotation_url,
            reference_dir=reference_dir,
            download_dir=download_dir,
            context=context,
            threads=threads,
            build_index=build_index,
            force=force,
            provider=provider,
            annotation_provider=annotation_provider,
            species=species,
            assembly=assembly,
            release=release,
            taxon_id=taxon_id,
            created_by="download",
            allow_mixed_source=allow_mixed_source,
        )
    except (FileExistsError, FileNotFoundError, RuntimeError, ValueError) as exc:
        print_error(console, f"Reference prepare error: {exc}")
        raise typer.Exit(code=1) from exc

    _print_reference_asset(prepared.asset, title=f"Reference Prepared: {prepared.asset.reference_id}")
    table = Table(title="Downloads")
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("provider", prepared.asset.provider)
    table.add_row("annotation_provider", prepared.asset.annotation_provider)
    table.add_row("build_status", prepared.asset.build_status)
    table.add_row("fasta_url", prepared.plan.fasta_url)
    table.add_row("annotation_url", prepared.plan.annotation_url)
    table.add_row("fasta", str(prepared.plan.fasta_ready))
    table.add_row("annotation", str(prepared.plan.annotation_ready))
    if prepared.index_command:
        table.add_row("hisat2_build", " ".join(prepared.index_command))
        table.add_row("index_return_code", str(prepared.index_return_code))
        table.add_row("index_dry_run", str(prepared.dry_run))
    console.print(table)
    if config:
        for key, value in reference_config_values(prepared.asset).items():
            set_config_value(config, key, value)
        print_success(console, f"Config updated with reference {prepared.asset.reference_id}: {config}")


@app.command()
def plan(config: Path) -> None:
    """Show the configured samples and pipeline steps."""
    cfg = load_project_config(config)
    samples = _build_samples(cfg.samples, cfg.project_id)
    print_workflow_plan(console, cfg, samples)


@app.command("validate-config")
def validate_config_command(
    config: Path,
    check_files: bool = typer.Option(True, help="Check input/reference files exist."),
) -> None:
    """Validate workflow config before running."""
    cfg = load_project_config(config)
    result = validate_project_config(cfg, check_files=check_files)
    print_validation_result(console, result)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command()
def run(
    config: Path,
    dry_run: bool = typer.Option(True, help="Run placeholder steps without requiring real input files."),
    max_workers: int = typer.Option(1, min=1, help="Local sample-level concurrency."),
    finalize: bool = typer.Option(False, help="Merge featureCounts outputs and generate reports after sample steps."),
    progress: bool = typer.Option(True, help="Show live progress bars."),
) -> None:
    """Run the workflow with the local executor."""
    cfg = load_project_config(config)
    validation = validate_project_config(cfg, check_files=not dry_run)
    if not validation.ok:
        print_validation_result(console, validation)
        raise typer.Exit(code=1)
    cfg.output_dir.mkdir(parents=True, exist_ok=True)

    samples = _build_samples(cfg.samples, cfg.project_id)
    steps = _build_steps(cfg.steps)
    print_run_start(console, cfg, samples, [step.step_id for step in steps], dry_run)
    context = RunContext(
        project_id=cfg.project_id,
        work_dir=cfg.work_dir,
        output_dir=cfg.output_dir,
        config=cfg.settings,
        dry_run=dry_run,
    )
    repository = JsonStateRepository(cfg.output_dir / "progress.json")
    log_manager = TaskLogManager(cfg.output_dir, task_id=cfg.project_id)
    log_manager.event("workflow_started", message="workflow started", dry_run=dry_run, sample_count=len(samples))
    pipeline = Pipeline(steps=steps, repository=repository, log_manager=log_manager)
    executor = LocalExecutor(pipeline=pipeline, max_workers=max_workers)

    try:
        if progress:
            run_executor_with_progress(console, executor, samples, context)
        else:
            executor.run(samples, context)
    except BaseException as exc:
        log_manager.event("workflow_cancelled", level="CRITICAL", message=str(exc))
        raise
    log_manager.event("workflow_completed", message="workflow completed", dry_run=dry_run)
    state_path = cfg.output_dir / "progress.json"
    print_success(console, f"Workflow finished. State: {state_path}")
    print_run_summary(console, state_path)
    if finalize:
        try:
            result = finalize_project(cfg.project_id, cfg.output_dir, samples)
        except FileNotFoundError as exc:
            print_error(console, f"Finalize error: {exc}")
            raise typer.Exit(code=1) from exc
        ui_print_finalize_result(console, result)


@app.command("scan-inputs")
def scan_inputs_command(
    input_dir: Path,
    project_id: str | None = typer.Option(None, help="Optional project id to attach to discovered samples."),
    json_output: Path | None = typer.Option(None, help="Write discovered samples to a JSON manifest."),
    csv_output: Path | None = typer.Option(None, help="Write discovered samples to a CSV manifest."),
) -> None:
    """Scan local SRA/FASTQ inputs and infer samples."""
    result = scan_inputs(input_dir, project_id=project_id)
    table = Table(title=f"Input scan: {input_dir}")
    table.add_column("Sample")
    table.add_column("Layout")
    table.add_column("Type")
    table.add_column("Files")
    for sample in result.samples:
        table.add_row(
            sample.sample_id,
            sample.layout.value,
            str(sample.metadata.get("input_type", "")),
            str(len(sample.source_paths)),
        )
    console.print(table)

    if json_output:
        write_manifest_json(result.samples, json_output)
        console.print(f"[green]JSON manifest written:[/green] {json_output}")
    if csv_output:
        write_manifest_csv(result.samples, csv_output)
        console.print(f"[green]CSV manifest written:[/green] {csv_output}")


@app.command("sra-to-fastq")
def sra_to_fastq_command(
    sra_path: Path,
    output_dir: Path,
    sample_id: str | None = typer.Option(None, help="Sample id. Defaults to SRA filename stem."),
    project_id: str = typer.Option("module_test", help="Project id for the module test context."),
    threads: int = typer.Option(4, min=1, help="fasterq-dump thread count."),
    split_files: bool = typer.Option(True, help="Use fasterq-dump --split-files."),
    progress: bool = typer.Option(False, help="Use fasterq-dump --progress."),
    dry_run: bool = typer.Option(True, help="Build and record the command without executing it."),
    result_json: Path | None = typer.Option(None, help="Write StepResult to JSON."),
) -> None:
    """Run or dry-run the SRA to FASTQ module for one SRA file."""
    sample = Sample(
        sample_id=sample_id or sra_path.stem,
        source_path=sra_path,
        source_paths=[sra_path],
        layout=SampleLayout.UNKNOWN,
        project_id=project_id,
        metadata={"input_type": "sra"},
    )
    context = RunContext(
        project_id=project_id,
        work_dir=Path.cwd(),
        output_dir=output_dir,
        config={
            "fasterq_dump_threads": threads,
            "fasterq_dump_split_files": split_files,
            "fasterq_dump_progress": progress,
        },
        dry_run=dry_run,
    )
    step = SraToFastqStep()
    try:
        step.validate_inputs(sample, context)
    except FileNotFoundError as exc:
        console.print(f"[red]Input error:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    result = step.run(sample, context)

    table = Table(title=f"{step.name}: {sample.sample_id}")
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("status", result.status.value)
    table.add_row("return_code", "" if result.return_code is None else str(result.return_code))
    table.add_row("message", result.message)
    table.add_row("command", " ".join(result.command or []))
    table.add_row("output", "; ".join(str(path) for path in result.outputs))
    console.print(table)

    if result_json:
        result_json.parent.mkdir(parents=True, exist_ok=True)
        result_json.write_text(json.dumps(_step_result_to_dict(result), ensure_ascii=False, indent=2), encoding="utf-8")
        console.print(f"[green]Result JSON written:[/green] {result_json}")


@app.command("download-accession")
def download_accession_command(
    accession: str,
    output_dir: Path,
    max_size: str | None = typer.Option(None, help="prefetch --max-size value, e.g. 20G."),
    transport: str | None = typer.Option(None, help="prefetch --transport value."),
    force: bool = typer.Option(False, help="Force prefetch even if a cached SRA exists."),
    clean_before_download: bool = typer.Option(False, help="Delete existing accession artifacts before download."),
    cleanup_on_fail: bool = typer.Option(True, help="Cleanup accession artifacts after failed/cancelled downloads."),
    retries: int = typer.Option(0, min=0, help="Retry count after failed downloads."),
    retry_delay: float = typer.Option(5.0, min=0.0, help="Seconds to wait between retries."),
    timeout_seconds: float | None = typer.Option(None, min=0.1, help="Cancel prefetch after this many seconds."),
    execution_mode: str = typer.Option("docker", help="Execution mode for prefetch: docker or local."),
    docker_image: str = typer.Option("rnaseq-workflow:tools", help="Docker image containing SRA Toolkit."),
    docker_workspace: Path = typer.Option(Path("."), help="Docker bind workspace."),
    resume_partial: bool = typer.Option(True, help="Keep partial files so a rerun can resume."),
    dry_run: bool = typer.Option(True, help="Build and record the command without executing it."),
    result_json: Path | None = typer.Option(None, help="Write download result to JSON."),
    result_csv: Path | None = typer.Option(None, help="Write download result to CSV."),
) -> None:
    """Download or dry-run one SRA run accession with prefetch."""
    downloader = PrefetchDownloader(
        max_size=max_size,
        transport=transport,
        force=force,
        execution_mode=execution_mode,
        docker_image=docker_image,
        docker_workspace=docker_workspace,
        resume_partial=resume_partial,
        clean_before_download=clean_before_download,
        cleanup_on_fail=cleanup_on_fail,
        retries=retries,
        retry_delay_seconds=retry_delay,
        timeout_seconds=timeout_seconds,
    )
    request = DownloadRequest(accession=accession, output_dir=output_dir)
    try:
        result = downloader.download(request, dry_run=dry_run)
    except KeyboardInterrupt:
        console.print("[yellow]Download interrupted by user.[/yellow]")
        raise typer.Exit(code=130)
    print_download_results(console, [result], title=f"Download: {accession}")
    if result_json:
        write_download_results_json([result], result_json)
        print_success(console, f"JSON result written: {result_json}")
    if result_csv:
        write_download_results_csv([result], result_csv)
        print_success(console, f"CSV result written: {result_csv}")


@app.command("download")
def smart_download_command(
    target: str = typer.Argument(..., help="SRA run accession like SRR11047173 or a TXT/CSV/JSON manifest path."),
    output_dir: Path = typer.Option(Path("downloads"), help="Download output directory."),
    max_size: str | None = typer.Option("5G", help="prefetch --max-size value, e.g. 5G or 20G."),
    source: str = typer.Option("auto", help="Download source: auto, ena, or sra."),
    execution_mode: str = typer.Option("docker", help="Execution mode for SRA prefetch: docker or local."),
    docker_image: str = typer.Option("rnaseq-workflow:tools", help="Docker image containing SRA Toolkit."),
    docker_workspace: Path = typer.Option(Path("."), help="Docker bind workspace."),
    max_workers: int = typer.Option(2, min=1, help="Concurrent downloads for manifest targets."),
    force: bool = typer.Option(False, help="Force prefetch even if a cached SRA exists."),
    dry_run: bool = typer.Option(False, help="Build and record commands without executing them."),
    metadata_preflight: bool = typer.Option(True, help="Fetch SRA metadata, write sidecars, and check mixed groups before download."),
    allow_mixed_metadata: bool = typer.Option(False, help="Allow downloading mixed metadata groups in one queue."),
    result_json: Path | None = typer.Option(None, help="Write download result to JSON."),
    result_csv: Path | None = typer.Option(None, help="Write download result to CSV."),
) -> None:
    """Download a target with simple defaults."""
    sra_downloader = PrefetchDownloader(
        max_size=max_size,
        force=force,
        execution_mode=execution_mode,
        docker_image=docker_image,
        docker_workspace=docker_workspace,
        resume_partial=True,
    )
    if source == "ena":
        downloader = EnaFastqDownloader()
    elif source == "sra":
        downloader = sra_downloader
    else:
        downloader = AutoDownloader(ena_downloader=EnaFastqDownloader(), sra_downloader=sra_downloader)
    try:
        requests = build_smart_download_requests(target, output_dir)
        if metadata_preflight:
            _preflight_sra_metadata_for_download(requests, output_dir, allow_mixed=allow_mixed_metadata)
        manager = DownloadManager(downloader=downloader, max_workers=max_workers)
        summary = run_download_manager_with_progress(console, manager, requests, dry_run)
    except ValueError as exc:
        print_error(console, f"Download target error: {exc}")
        raise typer.Exit(code=1) from exc
    print_download_results(console, summary.results, title=f"Download: {target}")
    if result_json:
        write_download_results_json(summary.results, result_json)
        print_success(console, f"JSON result written: {result_json}")
    if result_csv:
        write_download_results_csv(summary.results, result_csv)
        print_success(console, f"CSV result written: {result_csv}")


@app.command("sra-metadata")
def sra_metadata_command(
    target: str = typer.Argument(..., help="One or more SRR/ERR/DRR accessions separated by comma/space/semicolon."),
    output_dir: Path = typer.Option(Path("downloads"), help="Download root used for metadata sidecars."),
    write_sidecars: bool = typer.Option(True, help="Write downloads/{accession}/metadata.json files."),
) -> None:
    """Fetch SRA RunInfo metadata and show grouping compatibility."""
    accessions = split_sra_targets(target)
    if not accessions and looks_like_sra_accession(target):
        accessions = [target.strip().upper()]
    if not accessions:
        print_error(console, "Provide one or more SRA run accessions, e.g. SRR1,SRR2")
        raise typer.Exit(code=1)
    try:
        metadata = fetch_sra_metadata(accessions)
    except OSError as exc:
        print_error(console, f"SRA metadata fetch failed: {exc}")
        raise typer.Exit(code=1) from exc
    if write_sidecars:
        written = write_sra_metadata_sidecars(metadata, output_dir)
        print_success(console, f"Metadata sidecars written: {len(written)}")
    _print_sra_metadata(metadata)
    _print_sra_metadata_groups(metadata)
    if len(group_sra_metadata(metadata)) > 1:
        print_error(console, "Mixed metadata groups detected. Split samples before reference/alignment/quantification.")


def _preflight_sra_metadata_for_download(
    requests: list[DownloadRequest],
    output_dir: Path,
    allow_mixed: bool = False,
) -> None:
    accessions = sorted({request.accession.upper() for request in requests if looks_like_sra_accession(request.accession)})
    if not accessions:
        return
    try:
        metadata = fetch_sra_metadata(accessions)
    except OSError as exc:
        console.print(f"[yellow]SRA metadata preflight skipped:[/yellow] {exc}")
        return
    if not metadata:
        console.print("[yellow]SRA metadata preflight found no RunInfo records.[/yellow]")
        return
    written = write_sra_metadata_sidecars(metadata, output_dir)
    if written:
        print_success(console, f"SRA metadata sidecars written: {len(written)}")
    _print_sra_metadata_groups(metadata)
    groups = group_sra_metadata(metadata)
    if len(groups) > 1 and not allow_mixed:
        print_error(
            console,
            "Mixed SRA metadata groups detected. Split this queue before downstream analysis, "
            "or pass --allow-mixed-metadata if you only want to download files together.",
        )
        raise typer.Exit(code=1)


@app.command("download-resume")
def download_resume_command(
    accession: str,
    output_dir: Path,
    max_size: str | None = typer.Option(None, help="prefetch --max-size value, e.g. 20G."),
    transport: str | None = typer.Option(None, help="prefetch --transport value."),
    execution_mode: str = typer.Option("docker", help="Execution mode for prefetch: docker or local."),
    docker_image: str = typer.Option("rnaseq-workflow:tools", help="Docker image containing SRA Toolkit."),
    docker_workspace: Path = typer.Option(Path("."), help="Docker bind workspace."),
    timeout_seconds: float | None = typer.Option(None, min=0.1, help="Cancel prefetch after this many seconds."),
    dry_run: bool = typer.Option(True, help="Build and record the command without executing it."),
    result_json: Path | None = typer.Option(None, help="Write download result to JSON."),
) -> None:
    """Resume an interrupted SRA download without cleaning partial files."""
    downloader = PrefetchDownloader(
        max_size=max_size,
        transport=transport,
        execution_mode=execution_mode,
        docker_image=docker_image,
        docker_workspace=docker_workspace,
        cleanup_on_fail=True,
        resume_partial=True,
        timeout_seconds=timeout_seconds,
    )
    request = DownloadRequest(accession=accession, output_dir=output_dir)
    try:
        result = downloader.download(request, dry_run=dry_run)
    except KeyboardInterrupt:
        console.print("[yellow]Download interrupted by user.[/yellow]")
        raise typer.Exit(code=130)
    print_download_results(console, [result], title=f"Resume download: {accession}")
    if result_json:
        write_download_results_json([result], result_json)
        print_success(console, f"JSON result written: {result_json}")


@app.command("download-batch")
def download_batch_command(
    manifest: Path,
    output_dir: Path,
    max_workers: int = typer.Option(2, min=1, help="Concurrent downloads."),
    max_size: str | None = typer.Option(None, help="prefetch --max-size value, e.g. 20G."),
    transport: str | None = typer.Option(None, help="prefetch --transport value."),
    force: bool = typer.Option(False, help="Force prefetch even if a cached SRA exists."),
    clean_before_download: bool = typer.Option(False, help="Delete existing accession artifacts before download."),
    cleanup_on_fail: bool = typer.Option(True, help="Cleanup accession artifacts after failed/cancelled downloads."),
    retries: int = typer.Option(0, min=0, help="Retry count after failed downloads."),
    retry_delay: float = typer.Option(5.0, min=0.0, help="Seconds to wait between retries."),
    timeout_seconds: float | None = typer.Option(None, min=0.1, help="Cancel each prefetch after this many seconds."),
    execution_mode: str = typer.Option("docker", help="Execution mode for prefetch: docker or local."),
    docker_image: str = typer.Option("rnaseq-workflow:tools", help="Docker image containing SRA Toolkit."),
    docker_workspace: Path = typer.Option(Path("."), help="Docker bind workspace."),
    resume_partial: bool = typer.Option(True, help="Keep partial files so a rerun can resume."),
    dry_run: bool = typer.Option(True, help="Build and record commands without executing them."),
    progress: bool = typer.Option(True, help="Show live download progress."),
    result_json: Path | None = typer.Option(None, help="Write download results to JSON."),
    result_csv: Path | None = typer.Option(None, help="Write download results to CSV."),
) -> None:
    """Download or dry-run accessions from a TXT/CSV/JSON manifest."""
    requests = read_download_requests(manifest, output_dir)
    downloader = PrefetchDownloader(
        max_size=max_size,
        transport=transport,
        force=force,
        execution_mode=execution_mode,
        docker_image=docker_image,
        docker_workspace=docker_workspace,
        resume_partial=resume_partial,
        clean_before_download=clean_before_download,
        cleanup_on_fail=cleanup_on_fail,
        retries=retries,
        retry_delay_seconds=retry_delay,
        timeout_seconds=timeout_seconds,
    )
    manager = DownloadManager(downloader=downloader, max_workers=max_workers)
    try:
        if progress:
            summary = run_download_manager_with_progress(console, manager, requests, dry_run)
        else:
            summary = manager.download_many(requests, dry_run=dry_run)
    except KeyboardInterrupt:
        manager.cancel_all()
        console.print("[yellow]Download batch interrupted by user.[/yellow]")
        raise typer.Exit(code=130)
    print_download_results(console, summary.results, title=f"Download batch: {manifest}")
    overall = manager.overall_progress()
    print_success(
        console,
        f"total={overall.total} completed={overall.completed} failed={overall.failed} "
        f"cancelled={overall.cancelled} skipped={overall.skipped} bytes={overall.downloaded_bytes}",
    )
    if result_json:
        write_download_results_json(summary.results, result_json)
        print_success(console, f"JSON result written: {result_json}")
    if result_csv:
        write_download_results_csv(summary.results, result_csv)
        print_success(console, f"CSV result written: {result_csv}")


@app.command("fastqc")
def fastqc_command(
    fastq_path: Path,
    output_dir: Path,
    mate: Path | None = typer.Option(None, help="Optional mate FASTQ for paired-end data."),
    sample_id: str | None = typer.Option(None, help="Sample id. Defaults to FASTQ filename stem."),
    project_id: str = typer.Option("module_test", help="Project id for the module test context."),
    threads: int = typer.Option(2, min=1, help="FastQC thread count."),
    extract: bool = typer.Option(False, help="Use fastqc --extract."),
    quiet: bool = typer.Option(True, help="Use fastqc --quiet."),
    execution_mode: str = typer.Option("docker", help="Execution mode for FastQC: docker or local."),
    docker_image: str = typer.Option("rnaseq-workflow:tools", help="Docker image containing FastQC."),
    docker_workspace: Path = typer.Option(Path("."), help="Docker bind workspace."),
    dry_run: bool = typer.Option(True, help="Build and record the command without executing it."),
    result_json: Path | None = typer.Option(None, help="Write StepResult to JSON."),
) -> None:
    """Run or dry-run the FastQC module for one sample."""
    source_paths = [fastq_path]
    if mate:
        source_paths.append(mate)
    sample = Sample(
        sample_id=sample_id or _fastq_sample_id(fastq_path),
        source_path=fastq_path,
        source_paths=source_paths,
        layout=SampleLayout.PAIRED if mate else SampleLayout.SINGLE,
        project_id=project_id,
        metadata={"input_type": "fastq"},
    )
    context = RunContext(
        project_id=project_id,
        work_dir=Path.cwd(),
        output_dir=output_dir,
        config={
            "fastqc_threads": threads,
            "fastqc_extract": extract,
            "fastqc_quiet": quiet,
            "execution_mode": execution_mode,
            "docker_image": docker_image,
            "docker_workspace": str(docker_workspace),
        },
        dry_run=dry_run,
    )
    step = FastQCStep()
    try:
        step.validate_inputs(sample, context)
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]Input error:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    result = step.run(sample, context)
    _print_step_result(result, title=f"{step.name}: {sample.sample_id}")
    if result.status not in {StepStatus.COMPLETED, StepStatus.SKIPPED}:
        raise typer.Exit(code=result.return_code or 1)
    if result_json:
        result_json.parent.mkdir(parents=True, exist_ok=True)
        result_json.write_text(json.dumps(_step_result_to_dict(result), ensure_ascii=False, indent=2), encoding="utf-8")
        console.print(f"[green]Result JSON written:[/green] {result_json}")


@app.command("trim-galore")
def trim_galore_command(
    fastq_path: Path,
    output_dir: Path,
    mate: Path | None = typer.Option(None, help="Optional mate FASTQ for paired-end data."),
    sample_id: str | None = typer.Option(None, help="Sample id. Defaults to FASTQ filename stem."),
    project_id: str = typer.Option("module_test", help="Project id for the module test context."),
    quality: int = typer.Option(20, min=0, help="Trim Galore --quality value."),
    phred: str = typer.Option("33", help="Phred encoding: 33 or 64."),
    stringency: int = typer.Option(3, min=1, help="Trim Galore --stringency value."),
    cores: int = typer.Option(1, min=1, help="Trim Galore --cores value."),
    gzip_output: bool = typer.Option(True, help="Use Trim Galore --gzip."),
    execution_mode: str = typer.Option("docker", help="Execution mode for Trim Galore: docker or local."),
    docker_image: str = typer.Option("rnaseq-workflow:tools", help="Docker image containing Trim Galore."),
    docker_workspace: Path = typer.Option(Path("."), help="Docker bind workspace."),
    dry_run: bool = typer.Option(True, help="Build and record the command without executing it."),
    result_json: Path | None = typer.Option(None, help="Write StepResult to JSON."),
) -> None:
    """Run or dry-run the Trim Galore module for one sample."""
    source_paths = [fastq_path]
    if mate:
        source_paths.append(mate)
    sample = Sample(
        sample_id=sample_id or _fastq_sample_id(fastq_path),
        source_path=fastq_path,
        source_paths=source_paths,
        layout=SampleLayout.PAIRED if mate else SampleLayout.SINGLE,
        project_id=project_id,
        metadata={"input_type": "fastq"},
    )
    context = RunContext(
        project_id=project_id,
        work_dir=Path.cwd(),
        output_dir=output_dir,
        config={
            "trim_galore_quality": quality,
            "trim_galore_phred": phred,
            "trim_galore_stringency": stringency,
            "trim_galore_cores": cores,
            "trim_galore_gzip": gzip_output,
            "execution_mode": execution_mode,
            "docker_image": docker_image,
            "docker_workspace": str(docker_workspace),
            "cleanup_on_fail": True,
        },
        dry_run=dry_run,
    )
    step = TrimGaloreStep()
    try:
        step.validate_inputs(sample, context)
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]Input error:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    result = step.run(sample, context)
    _print_step_result(result, title=f"{step.name}: {sample.sample_id}")
    if result.status not in {StepStatus.COMPLETED, StepStatus.SKIPPED}:
        raise typer.Exit(code=result.return_code or 1)
    if result_json:
        result_json.parent.mkdir(parents=True, exist_ok=True)
        result_json.write_text(json.dumps(_step_result_to_dict(result), ensure_ascii=False, indent=2), encoding="utf-8")
        console.print(f"[green]Result JSON written:[/green] {result_json}")


@app.command("hisat2-align")
def hisat2_align_command(
    fastq_path: Path,
    output_dir: Path,
    index_prefix: Path = typer.Option(..., help="HISAT2 index prefix."),
    mate: Path | None = typer.Option(None, help="Optional mate FASTQ for paired-end data."),
    sample_id: str | None = typer.Option(None, help="Sample id. Defaults to FASTQ filename stem."),
    project_id: str = typer.Option("module_test", help="Project id for the module test context."),
    threads: int = typer.Option(4, min=1, help="HISAT2 thread count."),
    execution_mode: str = typer.Option("docker", help="Execution mode for HISAT2: docker or local."),
    docker_image: str = typer.Option("rnaseq-workflow:tools", help="Docker image containing HISAT2."),
    docker_workspace: Path = typer.Option(Path("."), help="Docker bind workspace."),
    dry_run: bool = typer.Option(True, help="Build and record the command without executing it."),
    result_json: Path | None = typer.Option(None, help="Write StepResult to JSON."),
) -> None:
    """Run or dry-run the HISAT2 alignment module for one sample."""
    source_paths = [fastq_path]
    if mate:
        source_paths.append(mate)
    sample = Sample(
        sample_id=sample_id or _fastq_sample_id(fastq_path),
        source_path=fastq_path,
        source_paths=source_paths,
        layout=SampleLayout.PAIRED if mate else SampleLayout.SINGLE,
        project_id=project_id,
        metadata={"input_type": "fastq"},
    )
    context = RunContext(
        project_id=project_id,
        work_dir=Path.cwd(),
        output_dir=output_dir,
        config={
            "hisat2_index": str(index_prefix),
            "hisat2_threads": threads,
            "execution_mode": execution_mode,
            "docker_image": docker_image,
            "docker_workspace": str(docker_workspace),
        },
        dry_run=dry_run,
    )
    step = Hisat2AlignStep()
    try:
        step.validate_inputs(sample, context)
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]Input error:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    result = step.run(sample, context)
    _print_step_result(result, title=f"{step.name}: {sample.sample_id}")
    if result.status not in {StepStatus.COMPLETED, StepStatus.SKIPPED}:
        raise typer.Exit(code=result.return_code or 1)
    if result_json:
        result_json.parent.mkdir(parents=True, exist_ok=True)
        result_json.write_text(json.dumps(_step_result_to_dict(result), ensure_ascii=False, indent=2), encoding="utf-8")
        console.print(f"[green]Result JSON written:[/green] {result_json}")


@app.command("samtools-sort")
def samtools_sort_command(
    sample_id: str,
    output_dir: Path,
    sam_input: Path | None = typer.Option(None, help="SAM input. Defaults to samples/{sample_id}/alignment/{sample_id}.sam."),
    project_id: str = typer.Option("module_test", help="Project id for the module test context."),
    threads: int = typer.Option(2, min=1, help="samtools sort thread count."),
    execution_mode: str = typer.Option("docker", help="Execution mode for samtools: docker or local."),
    docker_image: str = typer.Option("rnaseq-workflow:tools", help="Docker image containing samtools."),
    docker_workspace: Path = typer.Option(Path("."), help="Docker bind workspace."),
    index: bool = typer.Option(True, help="Create BAM index after sorting."),
    dry_run: bool = typer.Option(True, help="Build and record the command without executing it."),
    result_json: Path | None = typer.Option(None, help="Write StepResult to JSON."),
) -> None:
    """Run or dry-run the samtools sort module for one sample."""
    sample = Sample(sample_id=sample_id, source_path=Path(f"{sample_id}.sam"), project_id=project_id)
    config = {
        "samtools_threads": threads,
        "samtools_index": index,
        "execution_mode": execution_mode,
        "docker_image": docker_image,
        "docker_workspace": str(docker_workspace),
    }
    if sam_input:
        config["sam_input"] = str(sam_input)
    context = RunContext(project_id=project_id, work_dir=Path.cwd(), output_dir=output_dir, config=config, dry_run=dry_run)
    step = SamtoolsSortStep()
    try:
        step.validate_inputs(sample, context)
    except FileNotFoundError as exc:
        console.print(f"[red]Input error:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    result = step.run(sample, context)
    _print_step_result(result, title=f"{step.name}: {sample.sample_id}")
    if result.status not in {StepStatus.COMPLETED, StepStatus.SKIPPED}:
        raise typer.Exit(code=result.return_code or 1)
    if result_json:
        result_json.parent.mkdir(parents=True, exist_ok=True)
        result_json.write_text(json.dumps(_step_result_to_dict(result), ensure_ascii=False, indent=2), encoding="utf-8")
        console.print(f"[green]Result JSON written:[/green] {result_json}")


@app.command("feature-counts")
def feature_counts_command(
    sample_id: str,
    output_dir: Path,
    annotation: Path = typer.Option(..., help="GTF/GFF annotation file for featureCounts -a."),
    bam: Path | None = typer.Option(
        None,
        help="Input sorted BAM. Defaults to samples/{sample_id}/alignment/{sample_id}.sorted.bam.",
    ),
    project_id: str = typer.Option("module_test", help="Project id for the module test context."),
    threads: int = typer.Option(2, min=1, help="featureCounts thread count."),
    feature_type: str = typer.Option("exon", help="featureCounts -t value."),
    attribute_type: str = typer.Option("gene_id", help="featureCounts -g value."),
    strandness: int = typer.Option(0, min=0, max=2, help="featureCounts -s value: 0, 1, or 2."),
    paired: bool = typer.Option(False, help="Use featureCounts -p for paired-end fragments."),
    execution_mode: str = typer.Option("docker", help="Execution mode for featureCounts: docker or local."),
    docker_image: str = typer.Option("rnaseq-workflow:tools", help="Docker image containing featureCounts."),
    docker_workspace: Path = typer.Option(Path("."), help="Docker bind workspace."),
    dry_run: bool = typer.Option(True, help="Build and record the command without executing it."),
    result_json: Path | None = typer.Option(None, help="Write StepResult to JSON."),
) -> None:
    """Run or dry-run the featureCounts quantification module for one sample."""
    sample = Sample(sample_id=sample_id, source_path=Path(f"{sample_id}.sorted.bam"), project_id=project_id)
    config = {
        "featurecounts_annotation": str(annotation),
        "featurecounts_threads": threads,
        "featurecounts_feature_type": feature_type,
        "featurecounts_attribute_type": attribute_type,
        "featurecounts_strandness": strandness,
        "featurecounts_paired": paired,
        "execution_mode": execution_mode,
        "docker_image": docker_image,
        "docker_workspace": str(docker_workspace),
    }
    if bam:
        config["featurecounts_bam"] = str(bam)
    context = RunContext(project_id=project_id, work_dir=Path.cwd(), output_dir=output_dir, config=config, dry_run=dry_run)
    step = FeatureCountsStep()
    try:
        step.validate_inputs(sample, context)
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]Input error:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    result = step.run(sample, context)
    _print_step_result(result, title=f"{step.name}: {sample.sample_id}")
    if result.status != StepStatus.COMPLETED:
        raise typer.Exit(code=result.return_code or 1)
    if result_json:
        result_json.parent.mkdir(parents=True, exist_ok=True)
        result_json.write_text(json.dumps(_step_result_to_dict(result), ensure_ascii=False, indent=2), encoding="utf-8")
        console.print(f"[green]Result JSON written:[/green] {result_json}")


@app.command("merge-counts")
def merge_counts_command(
    count_tables: list[Path] = typer.Argument(..., help="featureCounts output tables to merge."),
    output: Path = typer.Option(..., help="Output raw counts matrix TSV."),
) -> None:
    """Merge per-sample featureCounts tables into a gene x sample raw counts matrix."""
    try:
        matrix = merge_featurecounts_files(count_tables)
    except ValueError as exc:
        print_error(console, f"Input error: {exc}")
        raise typer.Exit(code=1) from exc

    write_count_matrix_tsv(matrix, output)
    print_matrix_result(console, len(matrix.sample_ids), len(matrix.gene_ids), output)


@app.command("report-summary")
def report_summary_command(
    project_id: str,
    output_dir: Path,
    state: Path | None = typer.Option(None, help="Workflow progress JSON. Defaults to OUTPUT_DIR/progress.json."),
    counts_matrix: Path | None = typer.Option(None, help="Optional raw counts matrix TSV."),
    artifact: list[Path] | None = typer.Option(None, help="Artifact path to include. Can be repeated."),
    json_output: Path | None = typer.Option(None, help="Write report JSON."),
    markdown_output: Path | None = typer.Option(None, help="Write report Markdown."),
) -> None:
    """Generate a minimal project report summary as JSON and/or Markdown."""
    state_path = state or output_dir / "progress.json"
    artifacts = artifact or []
    report = build_project_report(
        project_id=project_id,
        output_dir=output_dir,
        state_path=state_path,
        counts_matrix_path=counts_matrix,
        artifact_paths=artifacts,
    )
    if json_output:
        write_report_json(report, json_output)
    if markdown_output:
        write_report_markdown(report, markdown_output)

    print_report_summary(console, report, json_output, markdown_output)


@app.command("finalize")
def finalize_command(
    config: Path,
    counts_matrix: Path | None = typer.Option(None, help="Output raw counts matrix TSV. Defaults to OUTPUT_DIR/reports/raw_counts.tsv."),
    json_output: Path | None = typer.Option(None, help="Output report JSON. Defaults to OUTPUT_DIR/reports/report.json."),
    markdown_output: Path | None = typer.Option(None, help="Output report Markdown. Defaults to OUTPUT_DIR/reports/report.md."),
) -> None:
    """Merge sample featureCounts outputs and generate project reports."""
    cfg = load_project_config(config)
    samples = _build_samples(cfg.samples, cfg.project_id)
    try:
        result = finalize_project(
            project_id=cfg.project_id,
            output_dir=cfg.output_dir,
            samples=samples,
            counts_matrix=counts_matrix,
            report_json=json_output,
            report_markdown=markdown_output,
        )
    except FileNotFoundError as exc:
        print_error(console, f"Finalize error: {exc}")
        raise typer.Exit(code=1) from exc
    ui_print_finalize_result(console, result)


def _build_samples(raw_samples: list[dict], project_id: str) -> list[Sample]:
    return samples_from_config(raw_samples, project_id)


def _build_steps(step_ids: list[str]) -> list[PipelineStep]:
    return build_pipeline_steps(step_ids)


def _step_result_to_dict(result) -> dict:
    return {
        "sample_id": result.sample_id,
        "step_id": result.step_id,
        "status": result.status.value,
        "message": result.message,
        "command": result.command,
        "return_code": result.return_code,
        "inputs": [str(path) for path in result.inputs],
        "outputs": [str(path) for path in result.outputs],
        "extra": result.extra,
    }


def _print_step_result(result, title: str) -> None:
    table = Table(title=title)
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("status", result.status.value)
    table.add_row("return_code", "" if result.return_code is None else str(result.return_code))
    table.add_row("message", result.message)
    table.add_row("command", " ".join(result.command or []))
    table.add_row("output", "; ".join(str(path) for path in result.outputs))
    console.print(table)


def _print_reference_asset(asset: ReferenceAsset, title: str) -> None:
    table = Table(title=title)
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("reference_id", asset.reference_id)
    table.add_row("root", str(asset.root))
    table.add_row("provider", asset.provider)
    table.add_row("annotation_provider", asset.annotation_provider)
    if asset.species:
        table.add_row("species", asset.species)
    if asset.assembly:
        table.add_row("assembly", asset.assembly)
    if asset.release:
        table.add_row("release", asset.release)
    if asset.taxon_id:
        table.add_row("taxon_id", asset.taxon_id)
    table.add_row("fasta", str(asset.fasta))
    table.add_row("annotation", "" if asset.annotation is None else str(asset.annotation))
    table.add_row("hisat2_index", str(asset.hisat2_index))
    table.add_row("build_status", asset.build_status)
    if asset.source_urls:
        table.add_row("source_urls", "\n".join(asset.source_urls))
    if asset.annotation_format:
        table.add_row("annotation_format", asset.annotation_format)
    table.add_row("created_by", asset.created_by)
    table.add_row("created_at", asset.created_at)
    table.add_row("updated_at", asset.updated_at)
    if asset.warnings:
        table.add_row("warnings", "\n".join(asset.warnings))
    if asset.notes:
        table.add_row("notes", asset.notes)
    console.print(table)


def _print_command_result(result) -> None:
    table = Table(title="Command Result")
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("status", "COMPLETED" if result.ok else "FAILED")
    table.add_row("dry_run", str(result.dry_run))
    table.add_row("return_code", str(result.return_code))
    table.add_row("command", " ".join(result.command))
    if result.stderr:
        table.add_row("stderr", result.stderr[-1000:])
    console.print(table)


def _print_sra_metadata(metadata) -> None:
    table = Table(title="SRA Metadata")
    table.add_column("Run")
    table.add_column("BioProject")
    table.add_column("BioSample")
    table.add_column("Organism")
    table.add_column("TaxID")
    table.add_column("Strategy")
    table.add_column("Source")
    table.add_column("Layout")
    table.add_column("Platform")
    for record in metadata:
        table.add_row(
            record.run,
            record.bioproject,
            record.biosample,
            record.scientific_name,
            record.taxid,
            record.library_strategy,
            record.library_source,
            record.library_layout,
            record.platform,
        )
    console.print(table)


def _print_sra_metadata_groups(metadata) -> None:
    table = Table(title="SRA Metadata Groups")
    table.add_column("Group")
    table.add_column("Runs")
    table.add_column("Organism")
    table.add_column("BioProject")
    table.add_column("Layout")
    table.add_column("Source")
    for idx, group in enumerate(group_sra_metadata(metadata), start=1):
        table.add_row(
            str(idx),
            ", ".join(record.run for record in group.runs),
            group.scientific_name,
            group.bioproject,
            group.library_layout,
            group.library_source,
        )
    console.print(table)


def _print_reference_check_report(report: ReferenceCheckReport) -> None:
    table = Table(title=f"Reference Check: {report.reference_id}")
    table.add_column("Level")
    table.add_column("Field")
    table.add_column("Message")
    if not report.issues:
        table.add_row("[bold green]ok[/bold green]", "-", "all checks passed")
    else:
        for issue in report.issues:
            style = "red" if issue.level == "error" else "yellow"
            table.add_row(f"[bold {style}]{issue.level}[/bold {style}]", issue.field, issue.message)
    console.print(table)


def reference_root_for_cli(reference_dir: Path, reference_id: str) -> Path:
    return reference_dir / reference_id


def _fastq_sample_id(path: Path) -> str:
    name = path.name
    for suffix in (".fastq.gz", ".fq.gz", ".fastq", ".fq"):
        if name.lower().endswith(suffix):
            return name[: -len(suffix)]
    return path.stem


if __name__ == "__main__":
    app()
