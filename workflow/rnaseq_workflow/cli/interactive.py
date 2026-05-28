from __future__ import annotations

import os
from pathlib import Path

from rich.console import Console
from rich.table import Table

from rnaseq_workflow.cli.ui import (
    print_config_summary,
    print_doctor_checks,
    print_download_results,
    print_error,
    print_finalize_result,
    print_run_start,
    print_run_summary,
    print_success,
    print_validation_result,
    print_workflow_plan,
    run_download_manager_with_progress,
    run_executor_with_progress,
)
from rnaseq_workflow.core.config import load_project_config
from rnaseq_workflow.core.config_edit import set_config_value
from rnaseq_workflow.core.config_template import ConfigTemplateOptions, write_config_template
from rnaseq_workflow.core.config_validation import validate_project_config
from rnaseq_workflow.core.doctor import run_doctor_checks
from rnaseq_workflow.core.finalize import finalize_project
from rnaseq_workflow.core.logging import TaskLogManager
from rnaseq_workflow.core.models import RunContext
from rnaseq_workflow.core.pipeline import Pipeline
from rnaseq_workflow.core.references import (
    ReferenceAsset,
    check_reference_asset,
    build_hisat2_index_for_reference,
    list_references,
    load_reference,
    reference_config_values,
    register_reference,
)
from rnaseq_workflow.core.reference_sources import build_ensembl_reference_urls, prepare_reference_from_urls
from rnaseq_workflow.core.samples import samples_from_config
from rnaseq_workflow.core.step_registry import build_pipeline_steps
from rnaseq_workflow.executors.local import LocalExecutor
from rnaseq_workflow.persistence.json_state import JsonStateRepository
from rnaseq_workflow.steps.data_ingestion import scan_inputs, write_manifest_csv, write_manifest_json
from rnaseq_workflow.steps.download import DownloadManager, DownloadRequest, PrefetchDownloader, read_download_requests


def run_interactive_console(console: Console, default_config: Path = Path("config.yaml")) -> None:
    state = {"config": default_config}
    while True:
        _print_main_menu(console, state["config"])
        choice = _prompt(console, "选择功能", default="0")
        if choice == "0":
            print_success(console, "已退出终端工作台")
            return
        if choice == "1":
            _doctor(console)
        elif choice == "2":
            _config_menu(console, state)
        elif choice == "3":
            _reference_menu(console, state)
        elif choice == "4":
            _download_menu(console)
        elif choice == "5":
            _scan_inputs_menu(console)
        elif choice == "6":
            _run_workflow_menu(console, state)
        elif choice == "7":
            _change_working_dir(console)
        else:
            print_error(console, "未知选项")
        _pause(console)


def _print_main_menu(console: Console, config_path: Path) -> None:
    table = Table(title="RNA-seq Workflow Terminal")
    table.add_column("Key", justify="right")
    table.add_column("入口")
    table.add_row("1", "环境检查 doctor")
    table.add_row("2", f"配置 config 当前: {config_path}")
    table.add_row("3", "参考基因组 reference")
    table.add_row("4", "下载 SRA")
    table.add_row("5", "扫描输入 FASTQ/SRA")
    table.add_row("6", "运行 workflow")
    table.add_row("7", f"切换工作目录 当前: {Path.cwd()}")
    table.add_row("0", "退出")
    console.print(table)


def _doctor(console: Console) -> None:
    image = _prompt(console, "Docker 镜像", "rnaseq-workflow:tools")
    checks = run_doctor_checks(check_docker_image=True, image=image)
    print_doctor_checks(console, checks)


def _config_menu(console: Console, state: dict) -> None:
    while True:
        table = Table(title=f"Config: {state['config']}")
        table.add_column("Key", justify="right")
        table.add_column("操作")
        table.add_row("1", "选择 config 文件")
        table.add_row("2", "创建 init-config")
        table.add_row("3", "查看 config-show")
        table.add_row("4", "校验 validate-config")
        table.add_row("5", "设置 config-set")
        table.add_row("6", "查看 plan")
        table.add_row("0", "返回")
        console.print(table)
        choice = _prompt(console, "选择配置操作", "0")
        if choice == "0":
            return
        if choice == "1":
            state["config"] = _prompt_path(console, "config 文件", state["config"])
        elif choice == "2":
            output = _prompt_path(console, "输出 config", state["config"])
            project_id = _prompt(console, "project_id", "rnaseq_project")
            overwrite = _confirm(console, "如果文件已存在，是否覆盖", False)
            try:
                write_config_template(output, ConfigTemplateOptions(project_id=project_id), overwrite=overwrite)
                state["config"] = output
                print_success(console, f"Config written: {output}")
            except FileExistsError as exc:
                print_error(console, str(exc))
        elif choice == "3":
            cfg = _load_config_or_report(console, state["config"])
            if cfg:
                print_config_summary(console, cfg)
        elif choice == "4":
            cfg = _load_config_or_report(console, state["config"])
            if cfg:
                result = validate_project_config(cfg, check_files=_confirm(console, "检查文件是否存在", True))
                print_validation_result(console, result)
        elif choice == "5":
            key = _prompt(console, "键，例如 hisat2_threads 或 samples.0.source_path")
            value = _prompt(console, "值")
            try:
                set_config_value(state["config"], key, value)
                print_success(console, f"Updated {key}")
            except (ValueError, IndexError, KeyError) as exc:
                print_error(console, str(exc))
        elif choice == "6":
            cfg = _load_config_or_report(console, state["config"])
            if cfg:
                print_workflow_plan(console, cfg, samples_from_config(cfg.samples, cfg.project_id))


def _reference_menu(console: Console, state: dict) -> None:
    while True:
        table = Table(title="Reference")
        table.add_column("Key", justify="right")
        table.add_column("操作")
        table.add_row("1", "列出 reference")
        table.add_row("2", "查看 reference")
        table.add_row("3", "登记 FASTA/GTF")
        table.add_row("4", "构建 HISAT2 index")
        table.add_row("5", "检查 reference")
        table.add_row("6", "写入当前 config")
        table.add_row("7", "一条龙下载 FASTA+GTF 并构建 index")
        table.add_row("0", "返回")
        console.print(table)
        choice = _prompt(console, "选择 reference 操作", "0")
        if choice == "0":
            return
        reference_dir = _prompt_path(console, "reference_dir", Path("references"), directory=True)
        if choice == "1":
            _print_references(console, list_references(reference_dir))
        elif choice == "2":
            _show_reference(console, _prompt(console, "reference_id"), reference_dir)
        elif choice == "3":
            reference_id = _prompt(console, "reference_id")
            fasta = _prompt_path(console, "genome FASTA", must_exist=True)
            annotation = _prompt_path(console, "annotation GTF/GFF，可留空", allow_empty=True, must_exist=True)
            try:
                asset = register_reference(
                    reference_id,
                    fasta=fasta,
                    annotation=annotation if str(annotation) else None,
                    reference_dir=reference_dir,
                    overwrite=_confirm(console, "覆盖已有 reference", False),
                    provider=_prompt(console, "provider", "custom") or "custom",
                    annotation_provider=_prompt(console, "annotation_provider", "") or None,
                )
                _print_reference(console, asset, "Reference registered")
            except (FileExistsError, FileNotFoundError, ValueError) as exc:
                print_error(console, str(exc))
        elif choice == "4":
            reference_id = _prompt(console, "reference_id")
            threads = _prompt_int(console, "线程数", 4, minimum=1)
            context = RunContext(
                project_id=f"reference_{reference_id}",
                work_dir=Path.cwd(),
                output_dir=reference_dir / reference_id,
                config={
                    "execution_mode": _prompt(console, "execution_mode", "docker"),
                    "docker_image": _prompt(console, "docker_image", "rnaseq-workflow:tools"),
                    "docker_workspace": str(_prompt_path(console, "docker_workspace", Path("."), directory=True)),
                },
                dry_run=not _confirm(console, "实际执行 hisat2-build", False),
            )
            try:
                asset, result = build_hisat2_index_for_reference(
                    reference_id, reference_dir, context, threads=threads, force=_confirm(console, "强制重建", False)
                )
                _print_reference(console, asset, "HISAT2 index")
                print_success(console, "dry-run command: " + " ".join(result.command) if result.dry_run else "hisat2-build finished")
                if not result.ok:
                    print_error(console, result.stderr)
            except (FileExistsError, FileNotFoundError) as exc:
                print_error(console, str(exc))
        elif choice == "5":
            reference_id = _prompt(console, "reference_id")
            try:
                asset = load_reference(reference_id, reference_dir)
                report = check_reference_asset(asset)
                if report.ok:
                    print_success(console, "reference check passed")
                else:
                    print_error(console, "; ".join(f"{issue.field}: {issue.message}" for issue in report.issues))
            except FileNotFoundError as exc:
                print_error(console, str(exc))
        elif choice == "6":
            reference_id = _prompt(console, "reference_id")
            try:
                asset = load_reference(reference_id, reference_dir)
                report = check_reference_asset(asset)
                if not report.ok:
                    print_error(console, "; ".join(f"{issue.field}: {issue.message}" for issue in report.issues))
                    continue
                for key, value in reference_config_values(asset).items():
                    set_config_value(state["config"], key, value)
                print_success(console, f"Config updated: {state['config']}")
            except FileNotFoundError as exc:
                print_error(console, str(exc))
        elif choice == "7":
            _prepare_reference_menu(console, state, reference_dir)


def _download_menu(console: Console) -> None:
    table = Table(title="Download")
    table.add_column("Key", justify="right")
    table.add_column("操作")
    table.add_row("1", "下载单个 accession")
    table.add_row("2", "批量下载 manifest")
    console.print(table)
    choice = _prompt(console, "选择下载操作", "2")
    output_dir = _prompt_path(console, "输出目录", Path("downloads"), directory=True)
    dry_run = not _confirm(console, "实际下载", False)
    downloader = PrefetchDownloader(
        max_size=_prompt(console, "max_size，可留空", "", allow_empty=True) or None,
        force=_confirm(console, "force 重下", False),
        retries=_prompt_int(console, "失败重试次数", 0, minimum=0),
        execution_mode=_prompt(console, "execution_mode", "docker"),
        docker_image=_prompt(console, "docker_image", "rnaseq-workflow:tools"),
        docker_workspace=_prompt_path(console, "docker_workspace", Path("."), directory=True),
    )
    if choice == "1":
        request = DownloadRequest(accession=_prompt(console, "accession"), output_dir=output_dir)
        result = downloader.download(request, dry_run=dry_run)
        print_download_results(console, [result])
    else:
        manifest = _prompt_path(console, "manifest TXT/CSV/JSON", must_exist=True)
        requests = read_download_requests(manifest, output_dir)
        manager = DownloadManager(downloader=downloader, max_workers=_prompt_int(console, "并发数", 2, minimum=1))
        summary = run_download_manager_with_progress(console, manager, requests, dry_run=dry_run)
        print_download_results(console, summary.results)


def _prepare_reference_menu(console: Console, state: dict, reference_dir: Path) -> None:
    reference_id = _prompt(console, "reference_id，例如 hg38 或 tair10")
    mode = _prompt(console, "来源 1=Ensembl物种 2=直接URL", "1")
    if mode == "1":
        species = _prompt(console, "species，例如 homo_sapiens / Arabidopsis thaliana")
        division = _prompt(console, "division: vertebrates/plants/fungi/metazoa/protists", "vertebrates")
        release = _prompt(console, "release", "current")
        try:
            fasta_url, annotation_url = build_ensembl_reference_urls(species, division=division, release=release)
            print_success(console, f"FASTA: {fasta_url}")
            print_success(console, f"GTF: {annotation_url}")
        except (FileNotFoundError, ValueError) as exc:
            print_error(console, str(exc))
            return
    else:
        fasta_url = _prompt(console, "FASTA URL")
        annotation_url = _prompt(console, "GTF/GFF URL")
    provider = _prompt(console, "provider", "ensembl" if mode == "1" else "custom") or "custom"
    annotation_provider = _prompt(console, "annotation_provider", provider) or provider
    context = RunContext(
        project_id=f"reference_{reference_id}",
        work_dir=Path.cwd(),
        output_dir=reference_dir / reference_id,
        config={
            "execution_mode": _prompt(console, "execution_mode", "docker"),
            "docker_image": _prompt(console, "docker_image", "rnaseq-workflow:tools"),
            "docker_workspace": str(_prompt_path(console, "docker_workspace", Path("."), directory=True)),
        },
        dry_run=not _confirm(console, "实际构建 HISAT2 index", False),
    )
    try:
        prepared = prepare_reference_from_urls(
            reference_id=reference_id,
            fasta_url=fasta_url,
            annotation_url=annotation_url,
            reference_dir=reference_dir,
            download_dir=_prompt_path(console, "下载缓存目录", Path("reference_downloads"), directory=True),
            context=context,
            threads=_prompt_int(console, "hisat2-build 线程数", 4, minimum=1),
            build_index=True,
            force=_confirm(console, "覆盖已有下载/reference/index", False),
            provider=provider,
            annotation_provider=annotation_provider,
            created_by="download",
        )
        _print_reference(console, prepared.asset, "Reference prepared")
        if _confirm(console, f"写入当前 config: {state['config']}", True):
            for key, value in reference_config_values(prepared.asset).items():
                set_config_value(state["config"], key, value)
            print_success(console, f"Config updated: {state['config']}")
    except (FileExistsError, FileNotFoundError, RuntimeError, ValueError) as exc:
        print_error(console, str(exc))


def _scan_inputs_menu(console: Console) -> None:
    input_dir = _prompt_path(console, "输入目录", Path("data"), must_exist=True, directory=True)
    result = scan_inputs(input_dir, project_id=_prompt(console, "project_id，可留空", "", allow_empty=True) or None)
    table = Table(title=f"Input scan: {input_dir}")
    table.add_column("Sample")
    table.add_column("Layout")
    table.add_column("Files")
    for sample in result.samples:
        table.add_row(sample.sample_id, sample.layout.value, "\n".join(str(path) for path in sample.source_paths))
    console.print(table)
    if _confirm(console, "写出 JSON manifest", False):
        write_manifest_json(result.samples, _prompt_path(console, "JSON 输出", Path("samples.json")))
    if _confirm(console, "写出 CSV manifest", False):
        write_manifest_csv(result.samples, _prompt_path(console, "CSV 输出", Path("samples.csv")))


def _run_workflow_menu(console: Console, state: dict) -> None:
    cfg = _load_config_or_report(console, state["config"])
    if not cfg:
        return
    dry_run = not _confirm(console, "实际运行 workflow", False)
    validation = validate_project_config(cfg, check_files=not dry_run)
    if not validation.ok:
        print_validation_result(console, validation)
        return
    samples = samples_from_config(cfg.samples, cfg.project_id)
    steps = build_pipeline_steps(cfg.steps)
    print_run_start(console, cfg, samples, [step.step_id for step in steps], dry_run)
    context = RunContext(cfg.project_id, cfg.work_dir, cfg.output_dir, cfg.settings, dry_run=dry_run)
    repository = JsonStateRepository(cfg.output_dir / "progress.json")
    log_manager = TaskLogManager(cfg.output_dir, task_id=cfg.project_id)
    log_manager.event("workflow_started", message="workflow started", dry_run=dry_run, sample_count=len(samples))
    executor = LocalExecutor(
        Pipeline(steps=steps, repository=repository, log_manager=log_manager),
        max_workers=_prompt_int(console, "样本并发数", 1, minimum=1),
    )
    try:
        run_executor_with_progress(console, executor, samples, context)
    except BaseException as exc:
        log_manager.event("workflow_cancelled", level="CRITICAL", message=str(exc))
        raise
    log_manager.event("workflow_completed", message="workflow completed", dry_run=dry_run)
    print_run_summary(console, cfg.output_dir / "progress.json")
    if _confirm(console, "生成最终 counts matrix 和报告", False):
        try:
            print_finalize_result(console, finalize_project(cfg.project_id, cfg.output_dir, samples))
        except FileNotFoundError as exc:
            print_error(console, str(exc))


def _change_working_dir(console: Console) -> None:
    path = _prompt_path(console, "新的工作目录", Path.cwd(), must_exist=True, directory=True)
    os.chdir(path)
    print_success(console, f"cwd: {Path.cwd()}")


def _load_config_or_report(console: Console, path: Path):
    try:
        return load_project_config(path)
    except Exception as exc:
        print_error(console, f"Config error: {exc}")
        return None


def _print_references(console: Console, assets: list[ReferenceAsset]) -> None:
    table = Table(title="Managed references")
    table.add_column("ID")
    table.add_column("Provider")
    table.add_column("Build")
    table.add_column("FASTA")
    table.add_column("Annotation")
    table.add_column("HISAT2 index")
    for asset in assets:
        table.add_row(
            asset.reference_id,
            asset.provider,
            asset.build_status,
            str(asset.fasta),
            str(asset.annotation or ""),
            str(asset.hisat2_index),
        )
    console.print(table)


def _show_reference(console: Console, reference_id: str, reference_dir: Path) -> None:
    try:
        _print_reference(console, load_reference(reference_id, reference_dir), "Reference")
    except FileNotFoundError as exc:
        print_error(console, str(exc))


def _print_reference(console: Console, asset: ReferenceAsset, title: str) -> None:
    table = Table(title=title)
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("reference_id", asset.reference_id)
    table.add_row("root", str(asset.root))
    table.add_row("provider", asset.provider)
    table.add_row("annotation_provider", asset.annotation_provider)
    table.add_row("fasta", str(asset.fasta))
    table.add_row("annotation", str(asset.annotation or ""))
    table.add_row("hisat2_index", str(asset.hisat2_index))
    table.add_row("build_status", asset.build_status)
    if asset.source_urls:
        table.add_row("source_urls", "\n".join(asset.source_urls))
    table.add_row("updated_at", asset.updated_at)
    console.print(table)


def _prompt(console: Console, label: str, default: str | None = None, allow_empty: bool = False) -> str:
    suffix = f" [{default}]" if default not in (None, "") else ""
    while True:
        value = console.input(f"{label}{suffix}: ").strip()
        if value:
            return value
        if default is not None:
            return default
        if allow_empty:
            return ""
        print_error(console, "不能为空")


def _prompt_int(console: Console, label: str, default: int, minimum: int | None = None) -> int:
    while True:
        raw = _prompt(console, label, str(default))
        try:
            value = int(raw)
        except ValueError:
            print_error(console, "请输入整数")
            continue
        if minimum is not None and value < minimum:
            print_error(console, f"不能小于 {minimum}")
            continue
        return value


def _confirm(console: Console, label: str, default: bool) -> bool:
    raw = _prompt(console, f"{label} {'Y/n' if default else 'y/N'}", "y" if default else "n").lower()
    return raw in {"y", "yes", "是", "true", "1"}


def _prompt_path(
    console: Console,
    label: str,
    default: Path | str | None = None,
    must_exist: bool = False,
    directory: bool = False,
    allow_empty: bool = False,
) -> Path:
    base = Path.cwd()
    default_path = Path(default) if default not in (None, "") else None
    while True:
        raw_default = str(default_path) if default_path is not None else None
        raw = _prompt(console, f"{label} (:ls 查看, :cd PATH 进入, :up 返回上级)", raw_default, allow_empty=allow_empty)
        if not raw and allow_empty:
            return Path("")
        if raw == ":ls":
            _list_dir(console, base)
            continue
        if raw == ":up":
            base = base.parent
            print_success(console, f"browse: {base}")
            continue
        if raw.startswith(":cd"):
            target_raw = raw[3:].strip() or "."
            target = _resolve_from_base(base, target_raw)
            if target.exists() and target.is_dir():
                base = target
                print_success(console, f"browse: {base}")
            else:
                print_error(console, f"目录不存在: {target}")
            continue
        path = _resolve_from_base(base, raw)
        if must_exist and not path.exists():
            print_error(console, f"路径不存在: {path}")
            continue
        if directory and path.exists() and not path.is_dir():
            print_error(console, f"不是目录: {path}")
            continue
        return path


def _resolve_from_base(base: Path, raw: str) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else (base / path)


def _list_dir(console: Console, path: Path) -> None:
    table = Table(title=f"Browse: {path}")
    table.add_column("Type")
    table.add_column("Name")
    for item in sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))[:80]:
        table.add_row("dir" if item.is_dir() else "file", item.name)
    console.print(table)


def _pause(console: Console) -> None:
    console.input("按 Enter 继续...")
