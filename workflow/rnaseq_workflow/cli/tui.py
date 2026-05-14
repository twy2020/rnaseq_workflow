from __future__ import annotations

import os
import json
import re
import threading
import shutil
import time
import urllib.parse
import urllib.request
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass
from datetime import datetime
from io import StringIO
from pathlib import Path
from typing import Any, Callable

import yaml

from prompt_toolkit.application import Application
from prompt_toolkit.completion import PathCompleter
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Dimension, HSplit, Layout, VSplit, Window, WindowAlign
from prompt_toolkit.widgets import Box, Button, Dialog, Frame, Label, TextArea
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.mouse_events import MouseEvent, MouseEventType
from prompt_toolkit.shortcuts import button_dialog, checkboxlist_dialog, input_dialog, message_dialog, radiolist_dialog, yes_no_dialog
from prompt_toolkit.styles import Style
from prompt_toolkit.utils import get_cwidth
from rich.console import Console
from rich.table import Table

from rnaseq_workflow.cli.interactive import run_interactive_console
from rnaseq_workflow.cli.ui import (
    print_config_summary,
    print_doctor_checks,
    print_download_results,
    print_error,
    print_run_start,
    print_run_summary,
    print_success,
    print_validation_result,
    print_workflow_plan,
    run_executor_with_progress,
)
from rnaseq_workflow.cli.ui import _format_bytes
from rnaseq_workflow.core.assets import AssetWorkspace, TaskWorkspace, build_asset_workspace, cleanup_allowed_targets, generate_user_id
from rnaseq_workflow.core.app_db import DbUser
from rnaseq_workflow.core.config import load_project_config
from rnaseq_workflow.core.config_edit import set_config_value
from rnaseq_workflow.core.config_template import ConfigTemplateOptions, write_config_template
from rnaseq_workflow.core.config_validation import validate_project_config
from rnaseq_workflow.core.cancellation import CancellationToken
from rnaseq_workflow.core.doctor import run_doctor_checks
from rnaseq_workflow.core.finalize import FinalizeResult, finalize_project
from rnaseq_workflow.core.models import RunContext, Sample, SampleLayout, StepResult, StepStatus
from rnaseq_workflow.core.pipeline import Pipeline
from rnaseq_workflow.core.reference_sources import PreparedReference
from rnaseq_workflow.core.reference_sources import build_ensembl_reference_urls, prepare_reference_from_urls
from rnaseq_workflow.core.references import (
    ReferenceAsset,
    check_reference_asset,
    build_hisat2_index_for_reference,
    cleanup_stale_reference_records,
    list_references,
    load_reference,
    reference_config_values,
    register_reference,
)
from rnaseq_workflow.core.samples import samples_from_config
from rnaseq_workflow.core.step_registry import build_pipeline_steps
from rnaseq_workflow.core.system_monitor import CpuSampler, DiskSnapshot, SystemSnapshot, collect_system_snapshot
from rnaseq_workflow.core.task_manifest import parse_task_manifest
from rnaseq_workflow.core.task_params import TaskParams, default_task_params, read_task_params, validate_task_params, write_task_params
from rnaseq_workflow.core.resource_check import estimate_workflow_resources, run_resource_checks, write_resource_checks
from rnaseq_workflow.executors.local import LocalExecutor
from rnaseq_workflow.executors.workflow_runner import WorkflowRunner
from rnaseq_workflow.executors.workflow_runner import WorkflowRunSummary
from rnaseq_workflow.persistence.json_state import JsonStateRepository
from rnaseq_workflow.steps.data_ingestion import SraToFastqStep, scan_inputs
from rnaseq_workflow.steps.download import (
    DownloadManager,
    DownloadRequest,
    AutoDownloader,
    EnaFastqDownloader,
    PrefetchDownloader,
    build_smart_download_requests,
    fetch_sra_metadata,
    group_sra_metadata,
    looks_like_sra_accession,
    read_download_requests,
    split_sra_targets,
    write_sra_metadata_sidecars,
)
from rnaseq_workflow.steps.download.cache import find_partial_sra_artifacts
from rnaseq_workflow.steps.alignment import Hisat2AlignStep, SamtoolsSortStep
from rnaseq_workflow.steps.quality_control import FastQCStep
from rnaseq_workflow.steps.quantification import FeatureCountsStep, merge_featurecounts_files, write_count_matrix_tsv
from rnaseq_workflow.steps.reporting import build_project_report, write_report_json, write_report_markdown
from rnaseq_workflow.steps.read_trimming import TrimGaloreStep


DEFAULT_TUI_CONCURRENCY = 6
DEFAULT_HEAVY_STEP_CONCURRENCY = 2


STYLE = Style.from_dict(
    {
        "dialog": "bg:#0b1020",
        "dialog.body": "bg:#111827 #e5e7eb",
        "dialog shadow": "bg:#030712",
        "frame": "bg:#111827 #e5e7eb",
        "frame.border": "#38bdf8 bold",
        "frame.label": "bg:#111827 #7dd3fc bold",
        "button": "bg:#111827 #cbd5e1",
        "button.arrow": "#38bdf8 bold",
        "button.text": "#e5e7eb",
        "button.focused": "bg:#111827 #ffffff bold",
        "button.focused button.arrow": "#facc15 bold",
        "button.focused button.text": "#ffffff bold",
        "radio": "bg:#111827 #e5e7eb",
        "radio-selected": "#facc15 bold",
        "radio-checked": "#22c55e bold",
        "checkbox": "bg:#111827 #e5e7eb",
        "checkbox-selected": "#facc15 bold",
        "checkbox-checked": "#22c55e bold",
        "input": "bg:#0f172a #f8fafc",
        "menu": "bg:#111827 #e5e7eb",
        "menu.selected": "#ffffff bold",
        "menu.marker": "#facc15 bold",
        "menu.border": "#38bdf8 bold",
        "text-area": "bg:#111827 #e5e7eb",
        "text-area.focused": "#ffffff bold",
    }
)

KEY_HINT = "↑↓ 选择    Enter 确认    Esc 返回"
LINE_MODE_ENV = "RNASEQ_TUI_MODE"


@dataclass
class TuiState:
    config: Path
    console: Console
    output_log: str = ""
    asset_root: Path = Path("workspace")
    user_id: str | None = None
    task_id: str | None = None
    username: str | None = None
    session_id: str | None = None

    @property
    def workspace(self) -> AssetWorkspace:
        return build_asset_workspace(self.asset_root)

    @property
    def task(self) -> TaskWorkspace | None:
        if not self.user_id or not self.task_id:
            return None
        return self.workspace.user(self.user_id).task(self.task_id)


def run_tui(console: Console, default_config: Path = Path("config.yaml"), fallback_when_not_tty: bool = True) -> None:
    if fallback_when_not_tty and not _is_interactive_terminal():
        run_interactive_console(console, default_config=default_config)
        return
    state = TuiState(config=default_config, console=console)
    _load_saved_session(state)
    while True:
        choice = _menu(
            "RNA-seq Workflow",
            _home_status_text(state),
            [
                ("doctor", "环境检查 doctor"),
                ("assets", "用户与任务管理"),
                ("config", "基础配置"),
                ("workflow", "Workflow"),
                ("reference", "Reference"),
                ("tools", "工具调试"),
                ("system", "系统信息与资源策略"),
                ("output", "查看最近输出"),
                ("exit", "退出"),
            ],
        )
        if choice in (None, "exit"):
            _message("退出", "已退出终端工作台")
            return
        _dispatch(choice, state)


def _home_status_text(state: TuiState) -> str:
    return "\n".join(
        [
            f"登录: {state.username or '未登录'}",
            f"任务: {_current_task_display(state)}",
            f"配置: {state.config}",
            f"目录: {Path.cwd()}",
            "",
            "从用户与任务管理开始。",
        ]
    )


def _dispatch(choice: str, state: TuiState) -> None:
    actions: dict[str, Callable[[TuiState], None]] = {
        "doctor": _doctor,
        "assets": _assets_menu,
        "config": _config_menu,
        "workflow": _workflow_menu,
        "reference": _reference_menu,
        "tools": _tools_menu,
        "system": _system_resource_menu,
        "download": _download_menu,
        "advanced_download": _advanced_download_menu,
        "metadata": _metadata_menu,
        "resume": _resume_download_menu,
        "scan": _scan_inputs,
        "sra_to_fastq": _sra_to_fastq_menu,
        "fastqc": _fastqc_menu,
        "trim": _trim_galore_menu,
        "hisat2": _hisat2_menu,
        "samtools": _samtools_menu,
        "featurecounts": _featurecounts_menu,
        "report": _report_menu,
        "run": _run_workflow,
        "cwd": _change_cwd,
        "output": _show_recent_output,
    }
    action = actions.get(choice)
    if action:
        action(state)


def _doctor(state: TuiState) -> None:
    image = _input("Docker 镜像", "用于检查工具镜像是否存在", "rnaseq-workflow:tools")
    if image is None:
        return
    checks = run_doctor_checks(check_docker_image=True, image=image)
    _capture_output(state, lambda console: print_doctor_checks(console, checks), "环境检查结果")


def _assets_menu(state: TuiState) -> None:
    while True:
        account_action = ("logout", f"登出 {state.username}") if state.username else ("login", "登录/注册用户")
        choice = _menu(
            "用户与任务管理",
            _asset_status_text(state),
            [
                account_action,
                ("task", "任务管理"),
                ("cleanup", "旧测试产物清理 dry-run"),
                ("back", "返回"),
            ],
        )
        if choice in (None, "back"):
            return
        if choice == "login":
            _account_menu(state)
        elif choice == "logout":
            _logout_user(state)
        elif choice == "task":
            _task_management_menu(state)
        elif choice == "cleanup":
            _show_cleanup_plan(state)


def _tools_menu(state: TuiState) -> None:
    while True:
        choice = _menu(
            "工具调试",
            "单项工具入口用于排错和局部重跑；正式任务建议使用 Workflow。",
            [
                ("download", "下载 SRA"),
                ("advanced_download", "高级下载设置"),
                ("metadata", "SRA 元数据预检/分组"),
                ("resume", "继续未完成下载"),
                ("scan", "扫描输入 FASTQ/SRA"),
                ("sra_to_fastq", "SRA 转 FASTQ"),
                ("fastqc", "FastQC 质控"),
                ("trim", "Trim Galore 修剪"),
                ("hisat2", "HISAT2 对齐"),
                ("samtools", "Samtools sort/index"),
                ("featurecounts", "featureCounts 定量"),
                ("report", "结果汇总/报告"),
                ("run", "运行旧 workflow"),
                ("back", "返回"),
            ],
        )
        if choice in (None, "back"):
            return
        _dispatch(choice, state)


def _workflow_menu(state: TuiState) -> None:
    while True:
        choice = _menu(
            "Workflow",
            _workflow_status_text(state),
            [
                ("prepare", f"1 创建/选择任务  {_status_badge(bool(state.task))}"),
                ("reference", f"2 选择 Reference  {_status_badge(_task_reference_selected(state))}"),
                ("manifest", f"3 提交清单  {_status_badge(_task_file_exists(state, 'manifest.json'))}"),
                ("params", f"4 工具配置  {_status_badge(_task_file_exists(state, 'params.json'))}"),
                ("check", f"5 资源检查  {_status_badge(_task_file_exists(state, 'resource_check.json'))}"),
                ("run", "6 正式运行"),
                ("back", "返回"),
            ],
        )
        if choice in (None, "back"):
            return
        if choice == "prepare":
            _task_management_menu(state)
        elif choice == "reference":
            _workflow_reference_page(state)
        elif choice == "manifest":
            _workflow_manifest_page(state)
        elif choice == "params":
            _workflow_params_page(state)
        elif choice == "check":
            _workflow_resource_check_page(state)
        elif choice == "run":
            _workflow_run_page(state)


def _workflow_status_text(state: TuiState) -> str:
    lines = [_asset_status_text(state)]
    if not state.task:
        lines.append("[Workflow] 尚未选择任务。")
        return "\n".join(lines)
    lines.extend(
        [
            f"[Reference] {_reference_status_text(state.task)}",
            f"[清单] {_manifest_status_text(state.task)}",
            f"[参数] {'已配置' if _task_metadata_file_exists(state.task, 'params.json') else '未配置'}",
            f"[资源检查] {'已完成' if _task_metadata_file_exists(state.task, 'resource_check.json') else '未完成'}",
        ]
    )
    return "\n".join(lines)


def _status_badge(done: bool) -> str:
    return "[OK]" if done else "[未完成]"


def _task_file_exists(state: TuiState, filename: str) -> bool:
    return bool(state.task and _task_metadata_file_exists(state.task, filename))


def _task_reference_selected(state: TuiState) -> bool:
    metadata = state.task.read_metadata() if state.task else None
    return bool(metadata and metadata.reference_id)


def _task_metadata_file_exists(task: TaskWorkspace, filename: str) -> bool:
    return (task.metadata_dir / filename).exists()


def _asset_status_text(state: TuiState) -> str:
    lines = [
        f"[资产根目录] {state.asset_root}",
        f"[账号] {state.username or '未登录'}",
        f"[用户ID] {state.user_id or '未设置'}",
        f"[当前任务] {_current_task_display(state)}",
    ]
    if state.task:
        lines.append(f"[任务目录] {state.task.root}")
    return "\n".join(lines)


def _reference_status_text(task: TaskWorkspace) -> str:
    metadata = task.read_metadata()
    if not metadata or not metadata.reference_id:
        return "未选择"
    try:
        asset = _load_task_reference_asset(task, metadata.reference_id)
    except FileNotFoundError:
        return f"{metadata.reference_id}（已失效）"
    return f"{asset.reference_id} / {asset.provider} / {asset.build_status}"


def _current_task_display(state: TuiState) -> str:
    if not state.task_id or not state.task:
        return "未选择"
    metadata = state.task.read_metadata()
    return metadata.task_name if metadata and metadata.task_name else "未命名任务"


def _account_menu(state: TuiState) -> None:
    while True:
        choice = _menu(
            "登录/注册",
            "登录后使用个人任务与资产。",
            [("login", "登录已有用户"), ("register", "注册新用户"), ("temp", "临时 UUID 用户"), ("back", "返回")],
        )
        if choice in (None, "back"):
            return
        if choice == "login" and _login_user(state):
            return
        if choice == "register" and _register_user(state):
            return
        if choice == "temp":
            _ensure_user(state, force=True)
            return


def _task_management_menu(state: TuiState) -> None:
    while True:
        choice = _menu(
            "任务管理",
            _asset_status_text(state),
            [
                ("new", "创建新任务"),
                ("select", "选择已有任务"),
                ("edit", "修改当前任务名称/描述"),
                ("delete", "删除当前任务"),
                ("show", "查看当前任务"),
                ("stats", "产物统计"),
                ("cleanup_outputs", "产物清理"),
                ("workflow", "进入 Workflow 向导"),
                ("back", "返回"),
            ],
        )
        if choice in (None, "back"):
            return
        if choice == "new":
            task = _create_task(state)
            if task and _confirm_yes("继续进入 Workflow 向导？", True):
                _workflow_menu(state)
        elif choice == "select":
            task = _select_task(state)
            if task and _confirm_yes("继续进入 Workflow 向导？", True):
                _workflow_menu(state)
        elif choice == "edit":
            _edit_current_task(state)
        elif choice == "delete":
            _delete_current_task(state)
        elif choice == "show":
            _show_current_task(state)
        elif choice == "stats":
            _task_artifact_stats_page(state)
        elif choice == "cleanup_outputs":
            _task_artifact_cleanup_page(state)
        elif choice == "workflow":
            if _current_or_new_task(state):
                _workflow_menu(state)


def _load_saved_session(state: TuiState) -> None:
    try:
        session = state.workspace.load_session()
        user = state.workspace.database.get_session_user(session.get("session_id") if session else None)
    except Exception:
        return
    if user:
        state.session_id = session["session_id"] if session else None
        state.user_id = user.user_id
        state.username = user.username
        state.workspace.ensure_user(user.user_id)


def _register_user(state: TuiState) -> DbUser | None:
    username = _input("注册用户名", "用于终端登录。建议使用英文/数字/下划线，不要包含空格。", "")
    if not username:
        return None
    display_name = _input("显示名称", "可留空。", username) or ""
    password = _password_input("密码", "输入注册密码，内容不会显示。")
    if not password:
        _message("注册失败", "密码不能为空。")
        return None
    confirm = _password_input("确认密码", "再次输入注册密码。")
    if confirm is None:
        return None
    if password != confirm:
        _message("注册失败", "两次输入的密码不一致。")
        return None
    try:
        user = state.workspace.database.create_user(username=username, password=password, display_name=display_name)
    except ValueError as exc:
        _message("注册失败", str(exc))
        return None
    state.workspace.ensure_user(user.user_id)
    _set_logged_in_user(state, user)
    _message("注册成功", f"username: {user.username}\nuser_id: {user.user_id}")
    return user


def _login_user(state: TuiState) -> DbUser | None:
    username = _input("登录用户名", "输入注册用户名。", state.username or "")
    if not username:
        return None
    password = _password_input("密码", "输入密码，内容不会显示。")
    if password is None:
        return None
    user = state.workspace.database.authenticate(username, password)
    if not user:
        _message("登录失败", "用户名或密码错误。")
        return None
    state.workspace.ensure_user(user.user_id)
    _set_logged_in_user(state, user)
    _message("登录成功", f"username: {user.username}\nuser_id: {user.user_id}")
    return user


def _logout_user(state: TuiState) -> None:
    state.workspace.database.logout(state.session_id)
    state.workspace.clear_session()
    state.session_id = None
    state.username = None
    state.user_id = None
    state.task_id = None
    _message("已登出", "当前用户和任务已清空。")


def _set_logged_in_user(state: TuiState, user: DbUser) -> None:
    session_id = state.workspace.database.create_session(user.user_id)
    state.workspace.save_session(session_id, user.user_id)
    state.session_id = session_id
    state.user_id = user.user_id
    state.username = user.username
    state.task_id = None


def _ensure_user(state: TuiState, force: bool = False) -> str | None:
    if state.user_id and not force:
        return state.user_id
    if not force:
        choice = _menu(
            "用户",
            "选择身份后继续。",
            [("login", "登录已有用户"), ("register", "注册新用户"), ("temp", "使用临时 UUID"), ("back", "返回")],
        )
        if choice in (None, "back"):
            return state.user_id
        if choice == "login":
            user = _login_user(state)
            return user.user_id if user else state.user_id
        if choice == "register":
            user = _register_user(state)
            return user.user_id if user else state.user_id
    default_user = state.user_id or generate_user_id()
    user_id = _input("user UUID", "留空使用自动生成 UUID。", default_user)
    if user_id is None:
        return state.user_id
    state.user_id = user_id.strip() or default_user
    state.workspace.ensure_user(state.user_id)
    state.username = None
    _message("当前用户", state.user_id)
    return state.user_id


def _create_task(state: TuiState) -> TaskWorkspace | None:
    user_id = _ensure_user(state)
    if not user_id:
        return None
    task_name = _input("任务名称", "可留空，目录仍使用 UUID。", "") or ""
    description = _input("任务描述", "可留空。", "") or ""
    task = state.workspace.ensure_user(user_id).create_task(task_name=task_name, description=description)
    state.workspace.database.upsert_task(
        task_id=task.task_id,
        user_id=user_id,
        task_dir=task.root,
        task_name=task_name,
        description=description,
        status="created",
    )
    state.task_id = task.task_id
    _message("任务已创建", f"任务: {_task_display_name(task)}\n目录: {task.root}")
    return task


def _select_task(state: TuiState) -> TaskWorkspace | None:
    user_id = _ensure_user(state)
    if not user_id:
        return None
    user = state.workspace.ensure_user(user_id)
    tasks = user.list_tasks()
    if not tasks:
        _message("无任务", "当前用户还没有任务，请先创建新任务。")
        return None
    values = [(task.task_id, _task_label(task)) for task in tasks]
    values.append(("back", "返回"))
    selected = _menu("选择任务", f"user_id: {user_id}", values)
    if selected in (None, "back"):
        return None
    state.task_id = selected
    task = user.task(selected)
    metadata = task.read_metadata()
    state.workspace.database.upsert_task(
        task_id=task.task_id,
        user_id=user_id,
        task_dir=task.root,
        task_name=metadata.task_name if metadata else "",
        description=metadata.description if metadata else "",
        status=metadata.status if metadata else "created",
        reference_id=metadata.reference_id if metadata else None,
    )
    return task


def _task_label(task: TaskWorkspace) -> str:
    return _task_display_name(task)


def _task_display_name(task: TaskWorkspace) -> str:
    metadata = task.read_metadata()
    return metadata.task_name if metadata and metadata.task_name else "未命名任务"


def _show_current_task(state: TuiState) -> None:
    task = _current_or_new_task(state)
    if not task:
        return
    metadata = task.read_metadata()
    lines = [
        f"user_id: {task.user_id}",
        f"task_id: {task.task_id}",
        f"task_dir: {task.root}",
        f"downloads: {task.downloads_dir}",
        f"samples/output: {task.task_output_dir}",
        f"reports: {task.reports_dir}",
        f"metadata: {task.metadata_path}",
    ]
    if metadata:
        lines.extend([f"task_name: {metadata.task_name}", f"description: {metadata.description}"])
    _message("当前任务目录", "\n".join(lines))


def _edit_current_task(state: TuiState) -> None:
    task = _current_or_new_task(state)
    if not task:
        return
    metadata = task.read_metadata()
    old_name = metadata.task_name if metadata else ""
    old_description = metadata.description if metadata else ""
    new_name = _input("任务名称", "修改显示名称；目录仍保持 UUID 不变。", old_name)
    if new_name is None:
        return
    new_description = _input("任务描述", "可留空。", old_description)
    if new_description is None:
        return
    updated = task.update_metadata(task_name=new_name, description=new_description)
    state.workspace.database.upsert_task(
        task_id=task.task_id,
        user_id=task.user_id,
        task_dir=task.root,
        task_name=updated.task_name,
        description=updated.description,
        status=updated.status,
        reference_id=updated.reference_id,
    )
    _message("任务已更新", f"{updated.task_name or '未命名任务'}\n{task.root}")


def _delete_current_task(state: TuiState) -> None:
    if not state.task:
        task = _select_task(state)
        if not task:
            return
    task = state.task
    metadata = task.read_metadata()
    task_name = metadata.task_name if metadata and metadata.task_name else "未命名任务"
    if not _yes_no("确认删除当前任务？", False):
        return
    confirm = _input("输入任务名确认", f"将删除任务目录和数据库记录：{task.root}\n请输入任务名：{task_name}", "")
    if confirm != task_name:
        _message("已取消", "任务名不匹配，未删除。")
        return
    try:
        state.workspace.user(task.user_id).delete_task(task.task_id)
    except ValueError as exc:
        _message("删除失败", str(exc))
        return
    state.workspace.database.delete_task(task.task_id, user_id=task.user_id)
    state.task_id = None
    _message("任务已删除", task_name)


@dataclass(frozen=True, slots=True)
class _ArtifactTarget:
    key: str
    label: str
    path: Path
    files: int
    size_bytes: int
    description: str


def _task_artifact_stats_page(state: TuiState) -> None:
    task = state.task
    if not task:
        _message("未选择任务", "请先选择任务。")
        return
    targets = _task_artifact_targets(task)
    _capture_output(state, lambda console: _print_task_artifact_stats(console, task, targets), "任务产物统计")


def _task_artifact_cleanup_page(state: TuiState) -> None:
    task = state.task
    if not task:
        _message("未选择任务", "请先选择任务。")
        return
    while True:
        targets = _task_artifact_targets(task)
        choices = [(target.key, f"{target.label} {_format_bytes(target.size_bytes)} / {target.files} files") for target in targets if target.files or target.size_bytes]
        choices.append(("all_intermediate", "清理中间产物和半成品"))
        choices.append(("back", "返回"))
        choice = _menu("产物清理", _task_artifact_cleanup_text(targets), choices)
        if choice in (None, "back"):
            return
        selected = _artifact_targets_for_choice(choice, targets)
        if not selected:
            _message("无需清理", "没有找到可清理的产物。")
            continue
        detail = "\n".join(f"{target.label}: {_format_bytes(target.size_bytes)}  {target.path}" for target in selected)
        if not _confirm_yes(f"确认清理以下产物？\n{detail}", False):
            continue
        removed = _remove_task_artifacts(task, selected)
        _message("清理完成", f"已清理 {_format_bytes(removed)}。")


def _task_artifact_targets(task: TaskWorkspace) -> list[_ArtifactTarget]:
    targets = [
        ("downloads", "下载文件", task.downloads_dir, "下载得到的 FASTQ/SRA 和半成品。"),
        ("inputs", "输入记录", task.inputs_dir, "本地输入记录和路径索引。"),
        ("samples", "样本中间产物", task.samples_dir, "按样本组织的 FASTQ、QC、比对和计数中间产物。"),
        ("logs", "日志", task.logs_dir, "运行日志和命令输出。"),
        ("reports", "报告", task.reports_dir, "矩阵、JSON、Markdown 和下载报告。"),
    ]
    return [
        _ArtifactTarget(key, label, path, *_path_file_count_size(path), description)
        for key, label, path, description in targets
    ]


def _path_file_count_size(path: Path) -> tuple[int, int]:
    if not path.exists():
        return 0, 0
    if path.is_file():
        return 1, _safe_file_size(path)
    files = [item for item in path.rglob("*") if item.is_file()]
    return len(files), sum(_safe_file_size(item) for item in files)


def _safe_file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _print_task_artifact_stats(console: Console, task: TaskWorkspace, targets: list[_ArtifactTarget]) -> None:
    table = Table(title="任务产物统计")
    table.add_column("类别")
    table.add_column("文件数", justify="right")
    table.add_column("大小", justify="right")
    table.add_column("路径")
    for target in targets:
        table.add_row(target.label, str(target.files), _format_bytes(target.size_bytes), str(target.path))
    table.add_section()
    table.add_row("合计", str(sum(target.files for target in targets)), _format_bytes(sum(target.size_bytes for target in targets)), str(task.root))
    console.print(table)


def _task_artifact_cleanup_text(targets: list[_ArtifactTarget]) -> str:
    lines = ["选择要清理的产物类别。任务配置和元数据会保留。", ""]
    for target in targets:
        lines.append(f"{target.label}: {_format_bytes(target.size_bytes)} / {target.files} files")
    return "\n".join(lines)


def _artifact_targets_for_choice(choice: str, targets: list[_ArtifactTarget]) -> list[_ArtifactTarget]:
    if choice == "all_intermediate":
        return [target for target in targets if target.key in {"downloads", "samples", "logs"}]
    return [target for target in targets if target.key == choice]


def _remove_task_artifacts(task: TaskWorkspace, targets: list[_ArtifactTarget]) -> int:
    removed = 0
    root = task.root.resolve()
    for target in targets:
        path = target.path.resolve()
        if path == root or root not in path.parents:
            raise ValueError(f"refuse to clean outside task dir: {path}")
        removed += target.size_bytes
        if path.exists():
            if path.is_dir():
                shutil.rmtree(path)
                path.mkdir(parents=True, exist_ok=True)
            else:
                path.unlink()
    task.ensure()
    return removed


def _show_cleanup_plan(state: TuiState) -> None:
    targets = cleanup_allowed_targets(Path("."), dry_run=True)
    _capture_output(state, lambda console: _print_cleanup_targets(console, targets), "旧测试产物清理 dry-run")


def _print_cleanup_targets(console: Console, targets) -> None:
    table = Table(title="Cleanup dry-run")
    table.add_column("Path")
    table.add_column("Exists")
    table.add_column("Allowed")
    table.add_column("Files", justify="right")
    table.add_column("Size")
    for target in targets:
        table.add_row(
            str(target.path),
            str(target.exists),
            str(target.allowed),
            str(target.file_count),
            _format_bytes(target.size_bytes),
        )
    console.print(table)


def _workflow_manifest_page(state: TuiState) -> None:
    task = _current_or_new_task(state)
    if not task:
        return
    existing = _read_task_manifest_record(task)
    mode = _menu(
        "提交清单",
        _manifest_page_text(task, existing),
        [("sra", "SRA accession 列表"), ("url", "自定义 URL JSON"), ("local", "本地数据目录"), ("view", "查看当前清单"), ("back", "返回")],
    )
    if mode in (None, "back"):
        return
    if mode == "view":
        _message("当前清单", _manifest_detail_text(existing))
        return
    if mode == "local":
        _workflow_local_manifest_page(state, task)
        return
    default = _manifest_default_for_mode(mode, existing)
    raw = _multiline_input(
        "清单内容",
        f"{_manifest_status_text(task)}。支持多行粘贴。",
        default,
    )
    if raw is None:
        return
    if not raw.strip():
        _message("输入错误", "清单不能为空。")
        return
    parsed = parse_task_manifest(raw)
    manifest_data = parsed.to_dict()
    if parsed.accessions:
        _enrich_manifest_expected_sizes(manifest_data)
    manifest_path = task.metadata_dir / "manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest_data, ensure_ascii=False, indent=2), encoding="utf-8")
    if not parsed.ok:
        _message("清单校验失败", "\n".join(parsed.errors))
        return
    _message(
        "清单已保存",
        f"accessions={len(parsed.accessions)}\nurls={len(parsed.urls)}\n{manifest_path}",
    )


def _read_task_manifest_record(task: TaskWorkspace) -> dict[str, Any] | None:
    path = task.metadata_dir / "manifest.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _manifest_status_text(task: TaskWorkspace) -> str:
    data = _read_task_manifest_record(task)
    if not data:
        return "未提交"
    errors = data.get("errors") or []
    if errors:
        return f"已保存但校验失败 errors={len(errors)}"
    return f"已提交 accessions={len(data.get('accessions') or [])} urls={len(data.get('urls') or [])} local={len(data.get('local_files') or [])}"


def _manifest_page_text(task: TaskWorkspace, data: dict[str, Any] | None) -> str:
    return "\n".join(
        [
            _manifest_status_text(task),
            f"文件: {task.metadata_dir / 'manifest.json'}",
            "再次编辑会载入上次清单。",
        ]
    )


def _manifest_detail_text(data: dict[str, Any] | None) -> str:
    if not data:
        return "当前任务还没有提交清单。"
    raw = str(data.get("raw") or "").strip()
    preview = raw if len(raw) <= 4000 else raw[:4000] + "\n...(已截断)"
    return "\n".join(
        [
            f"accessions: {len(data.get('accessions') or [])}",
            f"urls: {len(data.get('urls') or [])}",
            f"local_files: {len(data.get('local_files') or [])}",
            f"errors: {len(data.get('errors') or [])}",
            "",
            preview or "(raw 为空)",
        ]
    )


def _manifest_default_for_mode(mode: str, existing: dict[str, Any] | None) -> str:
    if existing and existing.get("raw"):
        return str(existing["raw"])
    if mode == "sra":
        return "SRR000001\nSRR000002"
    return '{\n  "url_groups": [\n    {\n      "base_url": "https://example.org/data",\n      "filenames": ["sample_1.fastq.gz", "sample_2.fastq.gz"]\n    }\n  ]\n}'


def _workflow_local_manifest_page(state: TuiState, task: TaskWorkspace) -> None:
    raw = _multiline_input(
        "本地数据路径",
        "每行一个目录或文件路径。Tab 可补全路径；支持 .sra、.fastq、.fq、.fastq.gz、.fq.gz。",
        str(task.inputs_dir),
        completer=PathCompleter(expanduser=True),
    )
    if raw is None:
        return
    paths = [Path(line.strip().strip('"')) for line in raw.splitlines() if line.strip()]
    if not paths:
        _message("输入错误", "请至少提供一个本地路径。")
        return
    files, errors = _scan_local_manifest_paths(paths)
    manifest = {
        "raw": raw,
        "accessions": [],
        "url_groups": [],
        "urls": [],
        "local_paths": [str(path) for path in paths],
        "local_files": files,
        "errors": errors,
    }
    manifest_path = task.metadata_dir / "manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    _capture_output(
        state,
        lambda console: _print_local_manifest_scan(console, files, errors),
        "本地数据扫描",
    )


def _scan_local_manifest_paths(paths: list[Path]) -> tuple[list[dict[str, Any]], list[str]]:
    suffixes = (".sra", ".fastq", ".fq", ".fastq.gz", ".fq.gz")
    files: list[dict[str, Any]] = []
    errors: list[str] = []
    for path in paths:
        if not path.exists():
            errors.append(f"path not found: {path}")
            continue
        candidates = [path] if path.is_file() else [item for item in path.rglob("*") if item.is_file()]
        for item in candidates:
            lower = item.name.lower()
            if not any(lower.endswith(suffix) for suffix in suffixes):
                continue
            files.append(
                {
                    "path": str(item),
                    "name": item.name,
                    "sample_id": _sample_id_from_local_file(item),
                    "input_type": "sra" if lower.endswith(".sra") else "fastq",
                    "size_bytes": item.stat().st_size,
                }
            )
    if not files and not errors:
        errors.append("no supported SRA/FASTQ files found")
    return files, errors


def _sample_id_from_local_file(path: Path) -> str:
    name = path.name
    for suffix in (".fastq.gz", ".fq.gz", ".fastq", ".fq", ".sra"):
        if name.lower().endswith(suffix):
            name = name[: -len(suffix)]
            break
    for token in ("_R1", "_R2", "_1", "_2"):
        if name.endswith(token):
            return name[: -len(token)]
    return name


def _print_local_manifest_scan(console: Console, files: list[dict[str, Any]], errors: list[str]) -> None:
    table = Table(title="本地数据扫描")
    table.add_column("Sample")
    table.add_column("Type")
    table.add_column("Size")
    table.add_column("Path")
    for row in files:
        table.add_row(str(row["sample_id"]), str(row["input_type"]), _format_bytes(int(row["size_bytes"])), str(row["path"]))
    console.print(table)
    if errors:
        console.print("[yellow]Warnings[/yellow]")
        for error in errors:
            console.print(f"- {error}")


def _workflow_params_page(state: TuiState) -> None:
    task = _current_or_new_task(state)
    if not task:
        return
    defaults = _load_task_params_defaults(task)
    metadata = task.read_metadata()
    if not metadata or not metadata.reference_id:
        _message("Reference 未设置", "请先在 Workflow 中选择 Reference。")
        return
    try:
        asset = _load_task_reference_asset(task, metadata.reference_id)
    except FileNotFoundError as exc:
        _message("Reference 错误", str(exc))
        return
    reference_id = asset.reference_id
    reference_dir = str(asset.root.parent)
    hisat2_index = str(asset.hisat2_index)
    annotation = str(asset.annotation or "")
    fc_defaults = _featurecounts_defaults_for_reference(asset)
    feature_type = fc_defaults["feature_type"]
    attribute_type = fc_defaults["attribute_type"]

    values = _task_params_wizard(defaults, feature_type, attribute_type)
    if values is None:
        return
    params = TaskParams(
        execution_mode=str(values["execution_mode"]),
        cleanup_policy=str(values["cleanup_policy"]),
        max_workers=int(values["max_workers"]),
        download_workers=int(values["download_workers"]),
        docker_image=str(values["docker_image"]) or defaults.docker_image,
        docker_workspace=_task_docker_workspace(task),
        download_source=str(values["download_source"]) or defaults.download_source,
        download_max_size=str(values["download_max_size"]) or defaults.download_max_size,
        download_proxy=str(values["download_proxy"]).strip(),
        sra_threads=int(values["sra_threads"]),
        fastqc_threads=int(values["fastqc_threads"]),
        trim_quality=int(values["trim_quality"]),
        trim_cores=int(values["trim_cores"]),
        hisat2_threads=int(values["hisat2_threads"]),
        samtools_threads=int(values["samtools_threads"]),
        featurecounts_threads=int(values["featurecounts_threads"]),
        featurecounts_feature_type=str(values["featurecounts_feature_type"]) or feature_type,
        featurecounts_attribute_type=str(values["featurecounts_attribute_type"]) or attribute_type,
        featurecounts_strandness=int(values["featurecounts_strandness"]),
        featurecounts_paired=bool(values["featurecounts_paired"]),
        reference_id=reference_id,
        reference_dir=reference_dir,
        hisat2_index=hisat2_index,
        annotation=annotation,
        downloads_dir=str(task.downloads_dir),
        output_dir=str(task.task_output_dir),
        reports_dir=str(task.reports_dir),
        resource_guard_enabled=bool(values["resource_guard_enabled"]),
        disk_guard_min_free_gb=float(values["disk_guard_min_free_gb"]),
        disk_guard_min_free_percent=float(values["disk_guard_min_free_percent"]),
        disk_guard_strategy=str(values["disk_guard_strategy"]),
        spill_paths=list(values["spill_paths"]),
        spill_large_outputs=bool(values["spill_large_outputs"]),
    )
    issues = validate_task_params(params)
    if issues:
        _message("参数校验失败", "\n".join(f"{issue.field}: {issue.message}" for issue in issues))
        return
    path = write_task_params(params, task.metadata_dir / "params.json")
    _message("参数已保存", str(path))


def _task_docker_workspace(task: TaskWorkspace) -> str:
    return str(task.root.parents[3])


def _docker_workspace_from_params(params: TaskParams) -> Path:
    workspace = Path(params.docker_workspace or ".")
    return workspace.resolve()


def _load_task_params_defaults(task: TaskWorkspace) -> TaskParams:
    path = task.metadata_dir / "params.json"
    if path.exists():
        try:
            return read_task_params(path)
        except (OSError, json.JSONDecodeError, TypeError):
            pass
    return default_task_params(task)


@dataclass(frozen=True, slots=True)
class _ParamField:
    key: str
    title: str
    description: str
    kind: str
    default: Any
    minimum: int | None = None
    choices: tuple[tuple[str, str], ...] = ()
    show_if: Callable[[dict[str, Any]], bool] | None = None


def _task_params_wizard(defaults: TaskParams, feature_type: str, attribute_type: str) -> dict[str, Any] | None:
    fields: list[tuple[str, str, str, str, int | None, tuple[tuple[str, str], ...]]] = [
        ("execution_mode", "执行模式", "按样本流水线会让先完成的样本继续下一步，适合正式任务；按阶段批量便于排错和教学。", "choice", None, (("sample_pipeline", "按样本流水线"), ("stage_batch", "按阶段批量"))),
        ("cleanup_policy", "清理策略", "任务完成后清理更稳妥；每步清理节省空间；不自动清理便于复查。", "choice", None, (("cleanup_after_task", "任务完成后清理"), ("cleanup_after_step", "每步成功后清理"), ("no_auto_cleanup", "不自动清理"))),
        ("download_workers", "下载并发数", _friendly_field("下载并发数")[1], "int", 1, ()),
        ("max_workers", "工作流样本并发数", _friendly_field("工作流样本并发数")[1], "int", 1, ()),
        ("docker_image", "Docker 镜像", "默认镜像适合标准流程。维护了自定义工具镜像时再修改。", "choice_custom", None, ((defaults.docker_image, f"默认: {defaults.docker_image}"),)),
        ("download_source", "下载来源", "自动模式会按可用性选择来源；ENA 直接下载 FASTQ；SRA Toolkit 下载 .sra；URL 使用清单页提供的地址。", "choice_custom", None, (("auto", "自动"), ("ena", "ENA FASTQ"), ("sra", "SRA Toolkit"), ("url", "自定义 URL 清单"))),
        ("download_max_size", "下载大小上限", "SRA Toolkit 的 max-size 限制。样本较大时可使用 20G、50G。", "str", None, ()),
        ("download_proxy", "下载代理", "可留空。需要代理时填写 http://127.0.0.1:7890 或 socks5://127.0.0.1:7890。", "str", None, ()),
        ("resource_guard_enabled", "资源智能预警", "开启后正式运行会实时检查工作目录所在盘，空间不足时按策略自动处理。", "choice", None, (("yes", "开启"), ("no", "关闭"))),
        ("disk_guard_min_free_gb", "磁盘最低剩余 GB", "工作盘剩余空间低于该值时触发预警。默认 20GB，数据量大时建议提高。", "float", 0, ()),
        ("disk_guard_min_free_percent", "磁盘最低剩余百分比", "工作盘剩余比例低于该值时触发预警。默认 10%。", "float", 0, ()),
        ("disk_guard_strategy", "空间不足处理策略", "取消并终止是默认安全策略；转移策略会把后续样本大产物直接写到转移路径。", "choice", None, (("cancel", "取消并终止当前运行"), ("transfer", "大产物写入转移路径"))),
        ("spill_large_outputs", "后续大产物写入转移路径", "开启后 SRA 转 FASTQ、FastQC、Trim、比对和定量的 samples 产物会写到转移路径的项目结构中。", "choice", None, (("yes", "开启"), ("no", "关闭"))),
        ("spill_paths", "产物转移路径", "仅在转移策略下使用。可填写多个路径，用分号分隔；优先使用第一个空间足够的路径。", "str", None, ()),
        ("sra_threads", "SRA 转 FASTQ 线程数", _friendly_field("fasterq-dump 线程数")[1], "int", 1, ()),
        ("fastqc_threads", "FastQC 线程数", _friendly_field("FastQC 线程数")[1], "int", 1, ()),
        ("trim_quality", "修剪质量阈值", _friendly_field("Trim quality")[1], "int", 0, ()),
        ("trim_cores", "Trim Galore 核心数", _friendly_field("Trim Galore cores")[1], "int", 1, ()),
        ("hisat2_threads", "HISAT2 线程数", _friendly_field("HISAT2 线程数")[1], "int", 1, ()),
        ("samtools_threads", "Samtools 线程数", _friendly_field("Samtools 线程数")[1], "int", 1, ()),
        ("featurecounts_threads", "featureCounts 线程数", _friendly_field("featureCounts 线程数")[1], "int", 1, ()),
        ("featurecounts_feature_type", "featureCounts 特征类型", "选择注释中用于计数的 feature。GTF 通常为 exon；部分 GFF 或病毒注释使用 gene。", "choice", None, (("exon", "exon"), ("gene", "gene"), ("CDS", "CDS"))),
        ("featurecounts_attribute_type", "featureCounts 属性字段", "选择用于汇总 reads 的基因 ID 字段。GTF 通常为 gene_id；GFF 常见 gene 或 ID。", "choice_custom", None, (("gene_id", "gene_id"), ("gene", "gene"), ("ID", "ID"))),
        ("featurecounts_strandness", "链特异性", "0 表示非链特异；1 为正向；2 为反向。不确定时先使用 0。", "choice", None, (("0", "非链特异"), ("1", "正向链特异"), ("2", "反向链特异"))),
        ("featurecounts_paired", "按片段计数", "paired-end 数据通常开启。开启后以 read pair 作为一个片段计数。", "choice", None, (("yes", "是"), ("no", "否"))),
    ]
    defaults_map: dict[str, Any] = {
        "execution_mode": defaults.execution_mode,
        "cleanup_policy": defaults.cleanup_policy,
        "download_workers": defaults.download_workers,
        "max_workers": defaults.max_workers,
        "docker_image": defaults.docker_image,
        "download_source": defaults.download_source,
        "download_max_size": defaults.download_max_size,
        "download_proxy": defaults.download_proxy,
        "resource_guard_enabled": "yes" if defaults.resource_guard_enabled else "no",
        "disk_guard_min_free_gb": defaults.disk_guard_min_free_gb,
        "disk_guard_min_free_percent": defaults.disk_guard_min_free_percent,
        "disk_guard_strategy": defaults.disk_guard_strategy,
        "spill_large_outputs": "yes" if defaults.spill_large_outputs else "no",
        "spill_paths": "; ".join(defaults.spill_paths),
        "sra_threads": defaults.sra_threads,
        "fastqc_threads": defaults.fastqc_threads,
        "trim_quality": defaults.trim_quality,
        "trim_cores": defaults.trim_cores,
        "hisat2_threads": defaults.hisat2_threads,
        "samtools_threads": defaults.samtools_threads,
        "featurecounts_threads": defaults.featurecounts_threads,
        "featurecounts_feature_type": feature_type,
        "featurecounts_attribute_type": attribute_type,
        "featurecounts_strandness": str(defaults.featurecounts_strandness),
        "featurecounts_paired": "yes" if defaults.featurecounts_paired else "no",
    }
    result = _tool_run_wizard("工具参数配置", defaults_map, fields)
    if result is not None:
        result["featurecounts_strandness"] = int(result["featurecounts_strandness"])
        result["featurecounts_paired"] = result["featurecounts_paired"] == "yes"
        result["resource_guard_enabled"] = result["resource_guard_enabled"] == "yes"
        result["disk_guard_min_free_gb"] = float(result["disk_guard_min_free_gb"])
        result["disk_guard_min_free_percent"] = float(result["disk_guard_min_free_percent"])
        result["spill_large_outputs"] = result["spill_large_outputs"] == "yes"
        result["spill_paths"] = _parse_spill_paths(str(result.get("spill_paths") or ""))
    return result


def _parse_spill_paths(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"[;\n]+", value or "") if part.strip()]


def _visible_param_fields(fields: list[_ParamField], values: dict[str, Any]) -> list[_ParamField]:
    return [field for field in fields if field.show_if is None or field.show_if(values)]


def _task_params_line_wizard(fields: list[_ParamField], values: dict[str, Any]) -> dict[str, Any] | None:
    for field in _visible_param_fields(fields, values):
        if field.kind in {"choice", "radio"}:
            choice = _line_menu(field.title, field.description, list(field.choices))
            if choice is None:
                return None
            values[field.key] = choice
        elif field.kind == "readonly":
            _line_message(field.title, str(field.default))
        elif field.kind == "bool":
            value = _line_yes_no(field.title, bool(values[field.key]))
            values[field.key] = bool(value)
        elif field.kind == "int":
            value = _int_input(field.title, int(values[field.key]), minimum=field.minimum, cancel_returns_default=False)
            if value is None:
                return None
            values[field.key] = value
        else:
            value = _line_input(field.title, field.description, str(values[field.key]))
            if value is None:
                return None
            values[field.key] = value
    values["featurecounts_strandness"] = int(values["featurecounts_strandness"])
    values["featurecounts_paired"] = values["featurecounts_paired"] == "yes"
    return values


def _task_params_dialog_wizard(fields: list[_ParamField], values: dict[str, Any]) -> dict[str, Any] | None:
    index = {"value": 0}
    result: dict[str, Any] = {"done": False, "cancelled": False}
    error = {"text": ""}
    visible_fields = {"items": _visible_param_fields(fields, values)}
    text_area = TextArea(
        text=str(values[visible_fields["items"][0].key]),
        multiline=False,
        width=Dimension(preferred=56),
        height=1,
        dont_extend_height=True,
        style="class:input",
        prompt=[("class:menu.border", "> ")],
    )

    def current() -> _ParamField:
        visible_fields["items"] = _visible_param_fields(fields, values)
        if index["value"] >= len(visible_fields["items"]):
            index["value"] = max(0, len(visible_fields["items"]) - 1)
        return visible_fields["items"][index["value"]]

    def display_value(field: _ParamField) -> str:
        value = values[field.key]
        if field.kind == "bool":
            return "是" if bool(value) else "否"
        if field.kind in {"choice", "radio"}:
            return next((label for key, label in field.choices if key == value), str(value))
        return str(value)

    def sync_input() -> None:
        field = current()
        if field.kind == "readonly":
            text_area.text = str(values[field.key])
            text_area.buffer.cursor_position = len(text_area.text)
            return
        if field.kind == "bool":
            text_area.text = "yes" if bool(values[field.key]) else "no"
        else:
            text_area.text = str(values[field.key])
        text_area.buffer.cursor_position = len(text_area.text)

    def parse_current() -> bool:
        field = current()
        raw = text_area.text.strip()
        if field.kind == "readonly":
            error["text"] = ""
            return True
        if field.kind in {"choice", "radio"}:
            for choice_key, label in field.choices:
                if raw == choice_key or raw == label:
                    values[field.key] = choice_key
                    error["text"] = ""
                    return True
            error["text"] = "请选择列表中的值。"
            return False
        if field.kind == "bool":
            lowered = raw.lower()
            if lowered in {"yes", "y", "true", "1", "是"}:
                values[field.key] = True
                error["text"] = ""
                return True
            if lowered in {"no", "n", "false", "0", "否"}:
                values[field.key] = False
                error["text"] = ""
                return True
            error["text"] = "请输入 是 或 否。"
            return False
        if field.kind == "int":
            try:
                value = int(raw)
            except ValueError:
                error["text"] = "请输入整数。"
                return False
            if field.minimum is not None and value < field.minimum:
                error["text"] = f"不能小于 {field.minimum}。"
                return False
            values[field.key] = value
            error["text"] = ""
            return True
        values[field.key] = raw
        error["text"] = ""
        return True

    def go(delta: int, event=None) -> None:
        if not parse_current():
            if event is not None:
                event.app.invalidate()
            return
        visible_fields["items"] = _visible_param_fields(fields, values)
        index["value"] = max(0, min(index["value"] + delta, len(visible_fields["items"]) - 1))
        sync_input()
        if event is not None:
            event.app.layout.focus(text_area)
            event.app.invalidate()

    def submit(event=None) -> None:
        if not parse_current():
            if event is not None:
                event.app.invalidate()
            return
        visible_fields["items"] = _visible_param_fields(fields, values)
        if index["value"] < len(visible_fields["items"]) - 1:
            go(1, event)
            return
        result["done"] = True
        if event is not None:
            event.app.exit(result=values)

    def cancel(event=None) -> None:
        result["cancelled"] = True
        if event is not None:
            event.app.exit(result=None)

    def choose_option(offset: int, event=None) -> None:
        field = current()
        if field.kind == "bool":
            values[field.key] = not bool(values[field.key])
            sync_input()
        elif field.kind in {"choice", "radio"}:
            keys = [key for key, _label in field.choices]
            try:
                pos = keys.index(str(values[field.key]))
            except ValueError:
                pos = 0
            values[field.key] = keys[(pos + offset) % len(keys)]
            sync_input()
        if event is not None:
            event.app.invalidate()

    def mouse_action(name: str):
        def handle(mouse_event: MouseEvent) -> None:
            if mouse_event.event_type != MouseEventType.MOUSE_UP:
                return
            from prompt_toolkit.application.current import get_app

            app = get_app()
            if name == "prev":
                if parse_current():
                    index["value"] = max(0, index["value"] - 1)
                    sync_input()
                    app.layout.focus(text_area)
                app.invalidate()
            elif name == "next":
                if not parse_current():
                    app.invalidate()
                    return
                visible_fields["items"] = _visible_param_fields(fields, values)
                if index["value"] < len(visible_fields["items"]) - 1:
                    index["value"] += 1
                    sync_input()
                    app.layout.focus(text_area)
                    app.invalidate()
                else:
                    result["done"] = True
                    app.exit(result=values)
            elif name == "cancel":
                result["cancelled"] = True
                app.exit(result=None)

        return handle

    def render_body():
        field = current()
        fragments: list[Any] = []
        visible_fields["items"] = _visible_param_fields(fields, values)
        fragments.append(("class:dialog.body", f"{index['value'] + 1}/{len(visible_fields['items'])}  "))
        fragments.append(("class:menu.border", field.title))
        fragments.append(("", "\n\n"))
        if field.kind in {"choice", "radio", "bool", "readonly"}:
            fragments.append(("class:dialog.body", "当前值: "))
            fragments.append(("class:menu.selected", display_value(field)))
            fragments.append(("", "\n"))
            if field.kind in {"choice", "radio"}:
                fragments.append(("class:dialog.body", "可选: " + " / ".join(label for _key, label in field.choices)))
                fragments.append(("", "\n"))
            if field.kind == "readonly":
                fragments.append(("class:dialog.body", "此项由前面选择自动决定。"))
            else:
                fragments.append(("class:dialog.body", "使用上下键选择，或左右键切换。"))
            fragments.append(("", "\n\n"))
        hint_lines = _wrap_display_text(field.description, 68)
        fragments.append(("class:menu.border", "说明: "))
        fragments.append(("class:dialog.body", hint_lines[0]))
        for line in hint_lines[1:]:
            fragments.append(("", "\n"))
            fragments.append(("class:dialog.body", "      " + line))
        if error["text"]:
            fragments.append(("", "\n\n"))
            fragments.append(("class:menu.marker", error["text"]))
        return FormattedText(fragments)

    def render_buttons():
        visible_fields["items"] = _visible_param_fields(fields, values)
        next_label = "保存 Enter" if index["value"] == len(visible_fields["items"]) - 1 else "下一个 Enter"
        return FormattedText(
            [
                ("class:menu.border", "< 上一个 PgUp >", mouse_action("prev")),
                ("class:dialog.body", " "),
                ("class:menu.border", f"< {next_label} >", mouse_action("next")),
                ("class:dialog.body", " "),
                ("class:menu.border", "< 返回 Esc >", mouse_action("cancel")),
            ]
        )

    body_control = FormattedTextControl(render_body, focusable=False)
    button_control = FormattedTextControl(render_buttons, focusable=False)
    dialog = Dialog(
        title=HTML("<b><ansicyan>工具参数配置</ansicyan></b>"),
        body=HSplit(
            [
                Window(content=body_control, always_hide_cursor=True, height=Dimension(preferred=8), dont_extend_height=True),
                Box(Frame(text_area, title=HTML("<ansicyan>值</ansicyan>"), width=Dimension(preferred=62)), padding_top=0, padding_bottom=1),
                Window(content=button_control, always_hide_cursor=True, height=1, dont_extend_height=True, align=WindowAlign.CENTER),
            ],
            padding=1,
        ),
        buttons=[],
        width=Dimension(min=76, preferred=86, max=94),
        with_background=True,
    )

    kb = KeyBindings()

    @kb.add("enter")
    def _enter(event) -> None:
        submit(event)

    @kb.add("pagedown")
    def _page_down(event) -> None:
        submit(event)

    @kb.add("pageup")
    def _page_up(event) -> None:
        go(-1, event)

    @kb.add("right")
    def _right(event) -> None:
        choose_option(1, event)

    @kb.add("left")
    def _left(event) -> None:
        choose_option(-1, event)

    @kb.add("down")
    def _down(event) -> None:
        choose_option(1, event)

    @kb.add("up")
    def _up(event) -> None:
        choose_option(-1, event)

    @kb.add("escape")
    @kb.add("c-c")
    def _cancel(event) -> None:
        cancel(event)

    app = Application(
        layout=Layout(dialog, focused_element=text_area),
        key_bindings=kb,
        style=STYLE,
        mouse_support=True,
        full_screen=True,
    )
    app_result = app.run()
    return app_result if result["done"] else None


def _workflow_resource_check_page(state: TuiState) -> None:
    task = _current_or_new_task(state)
    if not task:
        return
    params_path = task.metadata_dir / "params.json"
    docker_image = "rnaseq-workflow:tools"
    if params_path.exists():
        data = json.loads(params_path.read_text(encoding="utf-8"))
        docker_image = data.get("docker_image") or docker_image
    estimate_input_dir, sample_count = _resource_estimate_inputs(task)
    estimate = estimate_workflow_resources(estimate_input_dir, sample_count=sample_count)
    checks = run_resource_checks(task.root, docker_image=docker_image, estimate=estimate)
    write_resource_checks(checks, task.metadata_dir / "resource_check.json", estimate=estimate)
    _write_resource_settings(task, estimate)
    _capture_output(state, lambda console: _print_resource_checks(console, checks, estimate), "资源检查")


def _resource_estimate_inputs(task: TaskWorkspace) -> tuple[Path, int]:
    manifest_path = task.metadata_dir / "manifest.json"
    sample_count = 0
    if manifest_path.exists():
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            local_files = [row for row in data.get("local_files") or [] if isinstance(row, dict) and row.get("path")]
            if local_files:
                estimate_dir = task.metadata_dir / "resource_estimate_inputs"
                estimate_dir.mkdir(parents=True, exist_ok=True)
                payload = {"files": local_files}
                (estimate_dir / "local_files.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                sample_ids = {str(row.get("sample_id") or _sample_id_from_local_file(Path(str(row["path"])))) for row in local_files}
                return estimate_dir, len(sample_ids)
            sample_count = len(data.get("accessions") or []) + len(data.get("urls") or [])
        except (OSError, json.JSONDecodeError):
            sample_count = 0
    input_dir = task.downloads_dir if task.downloads_dir.exists() else task.inputs_dir
    return input_dir, sample_count


def _write_resource_settings(task: TaskWorkspace, estimate) -> None:
    params_path = task.metadata_dir / "params.json"
    data: dict[str, Any] = {}
    if params_path.exists():
        try:
            data = json.loads(params_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
    data["resource_estimate"] = estimate.to_dict()
    params_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _print_resource_checks(console: Console, checks, estimate=None) -> None:
    if estimate:
        summary = Table(title="Resource Estimate")
        summary.add_column("Field")
        summary.add_column("Value")
        summary.add_row("samples", str(estimate.sample_count))
        summary.add_row("input files", str(estimate.input_file_count))
        summary.add_row("input size", _format_bytes(estimate.input_size_bytes))
        summary.add_row("estimated outputs", _format_bytes(estimate.estimated_output_bytes))
        summary.add_row("peak workspace", _format_bytes(estimate.estimated_peak_workspace_bytes))
        summary.add_row("recommended free", _format_bytes(estimate.recommended_free_bytes))
        console.print(summary)
    table = Table(title="Resource Checks")
    table.add_column("Name")
    table.add_column("Level")
    table.add_column("Status")
    table.add_column("Message")
    table.add_column("Advice")
    for check in checks:
        style = "green" if check.ok else "red" if check.level == "error" else "yellow"
        table.add_row(check.name, check.level, f"[{style}]{'OK' if check.ok else 'FAIL'}[/{style}]", check.message, getattr(check, "recommendation", ""))
    console.print(table)


def _workflow_run_page(state: TuiState) -> None:
    task = _current_or_new_task(state)
    if not task:
        return
    metadata = task.read_metadata()
    if not metadata or not metadata.reference_id:
        _message("Reference 未设置", "请先在 Workflow 中选择 Reference。")
        return
    try:
        asset = _load_task_reference_asset(task, metadata.reference_id)
    except FileNotFoundError as exc:
        _message("Reference 错误", str(exc))
        return
    reference_report = check_reference_asset(asset)
    if not reference_report.ok:
        _message("Reference 未就绪", "\n".join(f"{issue.field}: {issue.message}" for issue in reference_report.issues))
        return
    manifest_path = task.metadata_dir / "manifest.json"
    params_path = task.metadata_dir / "params.json"
    if not manifest_path.exists() or not params_path.exists():
        _message("任务未就绪", "请先完成清单提交和工具配置。")
        return
    params = read_task_params(params_path)
    checks_path = task.metadata_dir / "resource_check.json"
    if not checks_path.exists():
        _message("资源检查未完成", "请先完成资源检查。")
        return
    manifest_data = _read_task_manifest_record(task)
    accessions = [str(item) for item in (manifest_data or {}).get("accessions") or []]
    if params.execution_mode == "sample_pipeline" and accessions:
        download_dir = _download_dir_for_source(task, params.download_source)
        _record_download_source(task, params.download_source, download_dir)
        expected_sizes = _load_manifest_expected_sizes(manifest_data)
        samples = _samples_from_accessions(accessions, download_dir, task.task_id, expected_sizes=expected_sizes)
        for sample in samples:
            sample.metadata["download_roots"] = [str(download_dir)]
        processing_semaphore = threading.BoundedSemaphore(max(1, int(params.max_workers)))
        processing_steps = [
            _ProcessingConcurrencyStep(step, processing_semaphore, max_workers=params.max_workers)
            for step in build_pipeline_steps(["data_ingestion", "quality_control", "read_trimming", "alignment", "quantification"])
        ]
        steps = [
            _ManifestDownloadStep(_downloader_for_params(params), download_dir, max_workers=params.download_workers),
            *processing_steps,
        ]
        runner_workers = max(params.max_workers, params.download_workers)
    else:
        prepared = _prepare_workflow_inputs_from_manifest(task, params)
        if not prepared:
            return
        if isinstance(prepared, list):
            samples = prepared
        else:
            input_dir = prepared
            try:
                scan = scan_inputs(input_dir, project_id=task.task_id)
            except (FileNotFoundError, NotADirectoryError) as exc:
                _message("扫描失败", str(exc))
                return
            samples = scan.samples
        steps = build_pipeline_steps(["data_ingestion", "quality_control", "read_trimming", "alignment", "quantification"])
        runner_workers = params.max_workers
    if not samples:
        _message("未发现样本", "清单没有解析出可处理的 SRA/FASTQ。")
        return
    workflow_output_dir = _existing_output_root(task) or task.task_output_dir
    for sample in samples:
        sample.metadata["_workflow_output_dir"] = str(workflow_output_dir)
    context = RunContext(
        project_id=task.task_id,
        work_dir=Path.cwd(),
        output_dir=workflow_output_dir,
        config=_params_to_run_config(params),
        dry_run=False,
    )
    context.config["manifest_path"] = str(manifest_path)
    context.config["task_workspace"] = task
    context.config["task_params"] = params
    context.config["_output_root_lock"] = threading.Lock()
    summary, events, finalize_result, finalize_message = _run_workflow_with_tui_progress(
        samples=samples,
        context=context,
        steps=steps,
        repository=JsonStateRepository(task.progress_path),
        mode=params.execution_mode,
        max_workers=runner_workers,
        processing_workers=params.max_workers,
        download_workers=params.download_workers if params.execution_mode == "sample_pipeline" and accessions else None,
        title="Workflow 正式运行",
        finalize_callback=lambda: _finalize_completed_workflow(task, context.output_dir, samples),
    )
    _capture_output(
        state,
        lambda console: _print_workflow_run_summary(console, summary, events, finalize_result, finalize_message),
        "Workflow 运行结果",
    )


def _workflow_processing_output_dir(task: TaskWorkspace, params: TaskParams) -> Path:
    if params.disk_guard_strategy == "transfer" and params.spill_large_outputs and params.spill_paths:
        target_root = _choose_spill_target(params.spill_paths, params) or Path(params.spill_paths[0]).expanduser()
        output_dir = _spill_task_output_dir(target_root, task)
        output_dir.mkdir(parents=True, exist_ok=True)
        _record_output_root(task, task.task_output_dir, output_dir, reason="large_outputs_root")
        return output_dir
    return task.task_output_dir


def _existing_output_root(task: TaskWorkspace) -> Path | None:
    for record in reversed(_read_artifact_location_records(task)):
        if record.get("reason") != "large_outputs_root":
            continue
        current = Path(str(record.get("current_path") or ""))
        if current.name == "samples":
            root = current.parent
        else:
            root = current
        if root != task.task_output_dir:
            root.mkdir(parents=True, exist_ok=True)
            return root
    return None


def _spill_task_output_dir(root: Path, task: TaskWorkspace) -> Path:
    return Path(root).expanduser() / "users" / task.user_id / "tasks" / task.task_id


def _workflow_reference_page(state: TuiState) -> None:
    task = _current_or_new_task(state)
    if not task:
        return
    metadata = task.read_metadata()
    current_reference_id = metadata.reference_id if metadata else ""
    choice = _menu(
        "Reference 选择",
        _workflow_reference_status_text(task),
        [
            ("select", "选择已有 reference"),
            ("prepare", "新建并准备 reference"),
            ("register", "登记本地 reference"),
            ("build", "HISAT2 index 构建"),
            ("check", "检查 reference"),
            ("clear", "清除当前 reference"),
            ("back", "返回"),
        ],
    )
    if choice in (None, "back"):
        return
    if choice == "select":
        selected = _choose_reference_asset(state, current_reference_id=current_reference_id)
        if not selected:
            return
        reference_dir, reference_id = selected
        try:
            asset = load_reference(reference_id, reference_dir)
        except FileNotFoundError as exc:
            _message("Reference 错误", str(exc))
            return
        _set_task_reference(task, state, asset)
        _message("已选择", _workflow_reference_detail_text(asset))
    elif choice == "prepare":
        reference_dir = _reference_workspace_dir(state)
        if not reference_dir:
            return
        _prepare_reference(reference_dir, state.config, state=state)
    elif choice == "register":
        reference_dir = _reference_workspace_dir(state)
        if not reference_dir:
            return
        _register_reference(reference_dir, state)
    elif choice == "build":
        reference_dir = _reference_workspace_dir(state)
        if not reference_dir:
            return
        _build_reference_index(reference_dir, state)
    elif choice == "check":
        reference_dir = _reference_workspace_dir(state)
        if not reference_dir:
            return
        _check_reference(reference_dir)
    elif choice == "clear":
        _set_task_reference(task, state, None)


def _reference_workspace_dir(state: TuiState) -> Path | None:
    if state.user_id:
        return state.workspace.user(state.user_id).user_reference_dir
    return state.workspace.global_reference_dir


def _workflow_reference_status_text(task: TaskWorkspace) -> str:
    metadata = task.read_metadata()
    reference_id = metadata.reference_id if metadata else None
    if not reference_id:
        return "当前任务尚未选择 reference。"
    try:
        asset = _load_task_reference_asset(task, reference_id)
    except FileNotFoundError:
        return f"当前 reference: {reference_id}（记录存在，但资产缺失）"
    return _workflow_reference_detail_text(asset)


def _workflow_reference_detail_text(asset: ReferenceAsset) -> str:
    owner = "共享"
    if asset.root.parts and "users" in asset.root.parts:
        idx = asset.root.parts.index("users")
        if idx + 1 < len(asset.root.parts):
            owner = f"用户 {asset.root.parts[idx + 1]}"
    ref_type = "参考基因组文件"
    if asset.annotation or asset.hisat2_index:
        ref_type = "参考资产"
    return (
        f"{ref_type} {asset.reference_id} 已就绪\n"
        f"来源: {asset.provider}\n"
        f"拥有者: {owner}\n"
        f"状态: {asset.build_status}\n"
        f"描述: {asset.notes or '无'}"
    )


def _set_task_reference(task: TaskWorkspace, state: TuiState, asset: ReferenceAsset | None) -> None:
    metadata = task.read_metadata()
    task_name = metadata.task_name if metadata else ""
    description = metadata.description if metadata else ""
    status = metadata.status if metadata else "created"
    reference_id = asset.reference_id if asset else None
    task.update_metadata(
        task_name=task_name,
        description=description,
        status=status,
        reference_id=reference_id,
    )
    state.workspace.database.upsert_task(
        task_id=task.task_id,
        user_id=task.user_id,
        task_dir=task.root,
        task_name=task_name,
        description=description,
        status=status,
        reference_id=reference_id,
    )


def _prepare_workflow_inputs_from_manifest(task: TaskWorkspace, params: TaskParams) -> Path | list[Sample] | None:
    data = _read_task_manifest_record(task)
    if not data:
        _message("清单缺失", "请先提交清单，或选择本地输入目录。")
        return None
    if data.get("errors"):
        _message("清单不可用", "\n".join(str(item) for item in data.get("errors") or []))
        return None
    accessions = [str(item) for item in data.get("accessions") or []]
    urls = [str(item) for item in data.get("urls") or []]
    local_files = [row for row in data.get("local_files") or [] if row.get("path")]
    if local_files:
        record_path = task.metadata_dir / "local_inputs.json"
        record_path.write_text(json.dumps(local_files, ensure_ascii=False, indent=2), encoding="utf-8")
        list_path = task.inputs_dir / "local_input_paths.txt"
        list_path.parent.mkdir(parents=True, exist_ok=True)
        list_path.write_text("\n".join(str(row["path"]) for row in local_files), encoding="utf-8")
        return _samples_from_local_manifest(local_files, task.task_id)
    if accessions:
        downloader = _downloader_for_params(params)
        download_dir = _download_dir_for_source(task, params.download_source)
        expected_sizes = _load_manifest_expected_sizes(data)
        requests = [DownloadRequest(accession=acc, output_dir=download_dir, expected_size_bytes=expected_sizes.get(acc.upper())) for acc in accessions]
        manager = DownloadManager(downloader=downloader, max_workers=params.download_workers)
        summary = _run_download_with_tui_progress(manager, requests, dry_run=False, title="样本下载")
        _record_download_source(task, params.download_source, download_dir)
        report_path = task.reports_dir / "workflow_download_results.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps([asdict(result) for result in summary.results], ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        if summary.failed or summary.cancelled:
            _message("下载未完成", f"completed={summary.completed} failed={summary.failed} cancelled={summary.cancelled}\n{report_path}")
            return None
        return download_dir
    if urls:
        download_dir = _download_dir_for_source(task, "url")
        ok, message = _download_manifest_urls(urls, download_dir, dry_run=False, proxy=params.download_proxy)
        if not ok:
            _message("URL 下载失败", message)
            return None
        _message("URL 下载完成", message)
        _record_download_source(task, "url", download_dir)
        return download_dir
    _message("清单为空", "清单没有可下载的 SRA accession、URL 或本地文件。")
    return None


def _download_dir_for_source(task: TaskWorkspace, source: str) -> Path:
    normalized = _download_source_key(source)
    return task.downloads_dir / normalized


def _download_source_key(source: str) -> str:
    value = str(source or "auto").strip().lower()
    if value == "sra":
        return "ncbi_sra"
    if value == "ncbi":
        return "ncbi_sra"
    if value == "url":
        return "url"
    return "ena_fastq"


def _record_download_source(task: TaskWorkspace, source: str, path: Path) -> None:
    record = {
        "source": _download_source_key(source),
        "path": str(path),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    record_path = task.metadata_dir / "download_source.json"
    record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")


def _samples_from_accessions(
    accessions: list[str],
    output_dir: Path,
    project_id: str,
    expected_sizes: dict[str, int] | None = None,
) -> list[Sample]:
    expected_sizes = expected_sizes or {}
    return [
        Sample(
            sample_id=accession,
            source_path=output_dir / accession,
            source_paths=[],
            layout=SampleLayout.UNKNOWN,
            project_id=project_id,
            metadata={
                "input_type": "remote_sra",
                "accession": accession,
                **({"expected_size_bytes": expected_sizes[accession.upper()]} if accession.upper() in expected_sizes else {}),
            },
        )
        for accession in accessions
    ]


def _load_manifest_expected_sizes(data: dict[str, Any], fetch_missing: bool = False) -> dict[str, int]:
    expected: dict[str, int] = {}
    for row in data.get("requests") or []:
        if not isinstance(row, dict):
            continue
        accession = str(row.get("accession") or row.get("run") or "").strip().upper()
        size = _coerce_positive_int(row.get("expected_size_bytes") or row.get("size_bytes"))
        if accession and size:
            expected[accession] = size
    for row in data.get("metadata") or []:
        if not isinstance(row, dict):
            continue
        accession = str(row.get("run") or row.get("Run") or row.get("accession") or "").strip().upper()
        size = _coerce_positive_int(row.get("expected_size_bytes") or row.get("size_bytes"))
        if not size:
            size_mb = row.get("size_mb") or row.get("size_MB")
            try:
                size = int(float(str(size_mb)) * 1024 * 1024) if size_mb not in (None, "") else None
            except ValueError:
                size = None
        if accession and size:
            expected[accession] = size
    if fetch_missing:
        from rnaseq_workflow.steps.download import fetch_sra_runinfo_rows, fetch_sra_run_size_bytes

        missing = [str(item).strip().upper() for item in data.get("accessions") or [] if str(item).strip().upper() not in expected]
        if missing:
            try:
                for row in fetch_sra_runinfo_rows(missing):
                    accession = str(row.get("Run") or "").strip().upper()
                    size = _coerce_size_mb(row.get("size_MB"))
                    if accession and size:
                        expected[accession] = size
            except Exception:
                for accession in missing:
                    try:
                        size = fetch_sra_run_size_bytes(accession)
                    except Exception:
                        size = None
                    if size:
                        expected[accession] = size
    return expected


def _enrich_manifest_expected_sizes(data: dict[str, Any]) -> bool:
    accessions = [str(item).strip().upper() for item in data.get("accessions") or [] if str(item).strip()]
    missing = [accession for accession in accessions if accession not in _load_manifest_expected_sizes(data)]
    if not missing:
        return False
    try:
        from rnaseq_workflow.steps.download import fetch_sra_runinfo_rows

        rows = fetch_sra_runinfo_rows(missing, timeout_seconds=8.0)
    except Exception:
        return False
    existing = [row for row in data.get("metadata") or [] if isinstance(row, dict)]
    by_run = {str(row.get("run") or row.get("Run") or row.get("accession") or "").strip().upper(): row for row in existing}
    changed = False
    for row in rows:
        accession = str(row.get("Run") or "").strip().upper()
        size = _coerce_size_mb(row.get("size_MB"))
        if not accession or not size:
            continue
        record = by_run.setdefault(accession, {"run": accession})
        record["run"] = accession
        record["size_MB"] = row.get("size_MB")
        record["expected_size_bytes"] = size
        changed = True
    if changed:
        data["metadata"] = list(by_run.values())
    return changed


def _update_manifest_expected_sizes(path: Path, sizes: dict[str, int]) -> None:
    if not path.exists() or not sizes:
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(data, dict):
        return
    existing = [row for row in data.get("metadata") or [] if isinstance(row, dict)]
    by_run = {str(row.get("run") or row.get("Run") or row.get("accession") or "").strip().upper(): row for row in existing}
    changed = False
    for accession, size in sizes.items():
        key = str(accession).strip().upper()
        if not key or not size:
            continue
        row = by_run.setdefault(key, {"run": key})
        if row.get("expected_size_bytes") != size:
            row["run"] = key
            row["expected_size_bytes"] = size
            changed = True
    if changed:
        data["metadata"] = list(by_run.values())
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _coerce_size_mb(value: Any) -> int | None:
    try:
        size = int(float(str(value)) * 1024 * 1024)
    except (TypeError, ValueError):
        return None
    return size if size > 0 else None


def _coerce_positive_int(value: Any) -> int | None:
    try:
        size = int(float(str(value)))
    except (TypeError, ValueError):
        return None
    return size if size > 0 else None


class _ManifestDownloadStep:
    step_id = "download"
    name = "Download sample"
    rerun_completed = True

    def __init__(self, downloader, output_dir: Path, max_workers: int = 1) -> None:
        self.downloader = downloader
        self.output_dir = output_dir
        self.max_workers = max(1, int(max_workers))
        self._semaphore = threading.BoundedSemaphore(self.max_workers)

    def validate_inputs(self, sample: Sample, context: RunContext) -> None:
        accession = str(sample.metadata.get("accession") or sample.sample_id)
        if not accession:
            raise ValueError("missing accession")

    def run(self, sample: Sample, context: RunContext) -> StepResult:
        accession = str(sample.metadata.get("accession") or sample.sample_id)
        token = context.config.get("cancellation_token")
        _emit_workflow_text_progress(context, sample.sample_id, self.step_id, StepStatus.RUNNING, f"排队等待下载槽位 {self.max_workers}")
        if not _acquire_semaphore_cancelable(self._semaphore, token):
            return StepResult(
                sample_id=sample.sample_id,
                step_id=self.step_id,
                status=StepStatus.CANCELLED,
                message="cancelled before download slot",
                inputs=sample.source_paths,
            )
        try:
            _emit_workflow_text_progress(context, sample.sample_id, self.step_id, StepStatus.RUNNING, "已获得下载槽位，准备下载/验证")
            result = self.downloader.download(
                DownloadRequest(accession=accession, output_dir=self.output_dir, expected_size_bytes=sample.metadata.get("expected_size_bytes")),
                dry_run=context.dry_run,
                progress_callback=lambda progress: _emit_workflow_step_progress(context, sample.sample_id, self.step_id, progress),
                cancellation_token=token,
            )
        finally:
            self._semaphore.release()
        if result.status in {StepStatus.COMPLETED, StepStatus.SKIPPED}:
            paths = _downloaded_sample_paths(accession, self.output_dir)
            if paths:
                _apply_downloaded_paths_to_sample(sample, paths)
        return StepResult(
            sample_id=sample.sample_id,
            step_id=self.step_id,
            status=result.status,
            message=result.message,
            command=result.command,
            return_code=result.return_code,
            inputs=[],
            outputs=sample.source_paths,
            extra={"downloaded_bytes": result.downloaded_bytes, "speed_bps": result.speed_bps},
        )

    def apply_cached_result(self, sample: Sample, context: RunContext, record) -> None:
        accession = str(sample.metadata.get("accession") or sample.sample_id)
        paths = [Path(path) for path in getattr(record, "outputs", []) if path]
        if not paths:
            paths = _downloaded_sample_paths(accession, self.output_dir)
        _apply_downloaded_paths_to_sample(sample, paths)


class _ProcessingConcurrencyStep:
    def __init__(self, step: Any, semaphore: threading.BoundedSemaphore, max_workers: int) -> None:
        self.step = step
        self.step_id = step.step_id
        self.name = step.name
        self._semaphore = semaphore
        self.max_workers = max(1, int(max_workers))

    def validate_inputs(self, sample: Sample, context: RunContext) -> None:
        _sync_sample_output_root(sample, context)
        self.step.validate_inputs(sample, context)

    def run(self, sample: Sample, context: RunContext) -> StepResult:
        _emit_workflow_text_progress(context, sample.sample_id, self.step_id, StepStatus.RUNNING, f"排队等待处理槽位 {self.max_workers}")
        token = context.config.get("cancellation_token")
        if not _acquire_semaphore_cancelable(self._semaphore, token):
            return StepResult(
                sample_id=sample.sample_id,
                step_id=self.step_id,
                status=StepStatus.CANCELLED,
                message="cancelled before processing slot",
                inputs=sample.source_paths,
            )
        try:
            _sync_sample_output_root(sample, context)
            _emit_workflow_text_progress(context, sample.sample_id, self.step_id, StepStatus.RUNNING, f"已获得处理槽位，执行 {self.name}")
            return self.step.run(sample, context)
        finally:
            self._semaphore.release()

    def apply_cached_result(self, sample: Sample, context: RunContext, record) -> None:
        _sync_sample_output_root(sample, context)
        apply_cached = getattr(self.step, "apply_cached_result", None)
        if callable(apply_cached):
            apply_cached(sample, context, record)


def _downloaded_sample_paths(accession: str, output_dir: Path) -> list[Path]:
    root = output_dir / accession
    candidates = []
    if root.exists():
        candidates.extend(path for path in root.rglob("*") if path.is_file())
    candidates.extend(path for path in output_dir.glob(f"**/{accession}*") if path.is_file())
    return sorted(path for path in set(candidates) if _is_fastq_path(path) or path.name.lower().endswith(".sra"))


def _apply_downloaded_paths_to_sample(sample: Sample, paths: list[Path]) -> None:
    paths = sorted(path for path in paths if path.exists())
    if not paths:
        return
    sample.source_path = paths[0]
    sample.source_paths = paths
    sample.metadata["download_paths"] = [str(path) for path in paths]
    fastq_count = len([path for path in paths if _is_fastq_path(path)])
    sample.layout = SampleLayout.PAIRED if fastq_count >= 2 else SampleLayout.SINGLE
    if any(path.name.lower().endswith(".sra") for path in paths):
        sample.layout = SampleLayout.UNKNOWN
    sample.metadata["input_type"] = "sra" if any(path.name.lower().endswith(".sra") for path in paths) else "fastq"


def _is_fastq_path(path: Path) -> bool:
    return path.name.lower().endswith((".fastq", ".fq", ".fastq.gz", ".fq.gz"))


def _acquire_semaphore_cancelable(semaphore: threading.BoundedSemaphore, token) -> bool:
    while True:
        if token is not None and token.is_cancelled():
            return False
        if semaphore.acquire(timeout=0.2):
            return True


def _emit_workflow_step_progress(context: RunContext, sample_id: str, step_id: str, progress) -> None:
    _emit_workflow_text_progress(context, sample_id, step_id, progress.status, _workflow_download_progress_detail(progress))


def _emit_workflow_text_progress(context: RunContext, sample_id: str, step_id: str, status: StepStatus, message: str) -> None:
    holder = context.config.get("workflow_progress_callback")
    callback = holder.get("callback") if isinstance(holder, dict) else None
    if callback:
        callback(sample_id, step_id, status, message)


def _workflow_download_progress_detail(progress) -> str:
    expected = getattr(progress, "expected_size_bytes", None)
    percent = progress.percent if progress.percent is not None else _estimated_percent(progress.downloaded_bytes, 0, expected)
    parts: list[str] = []
    if percent is not None:
        parts.append(f"{percent:.1f}%")
    size = _format_bytes(progress.downloaded_bytes)
    if expected:
        size += f"/{_format_bytes(expected)}"
    parts.append(size)
    parts.append(f"{_format_bytes(progress.speed_bps)}/s")
    eta = _download_eta(progress.downloaded_bytes, progress.speed_bps, expected, percent)
    if eta:
        parts.append(f"剩余:{eta}")
    if progress.local_path:
        parts.append(Path(progress.local_path).name)
    if progress.message:
        parts.append(_compact_progress_detail(progress.message))
    return " ".join(parts)


def _materialize_local_input_links(task: TaskWorkspace, local_files: list[dict[str, Any]]) -> Path:
    input_dir = task.inputs_dir / "local_manifest"
    input_dir.mkdir(parents=True, exist_ok=True)
    for row in local_files:
        source = Path(row["path"])
        target = input_dir / source.name
        if target.exists():
            continue
        try:
            target.symlink_to(source)
        except OSError:
            link_record = target.with_suffix(target.suffix + ".source.txt")
            link_record.write_text(str(source), encoding="utf-8")
    return input_dir


def _samples_from_local_manifest(local_files: list[dict[str, Any]], project_id: str) -> list[Sample]:
    grouped: dict[str, list[Path]] = {}
    metadata_by_sample: dict[str, dict[str, Any]] = {}
    for row in local_files:
        path = Path(str(row["path"]))
        sample_id = str(row.get("sample_id") or _sample_id_from_local_file(path))
        grouped.setdefault(sample_id, []).append(path)
        metadata_by_sample.setdefault(sample_id, {})["input_type"] = str(row.get("input_type") or ("sra" if path.name.lower().endswith(".sra") else "fastq"))
    samples: list[Sample] = []
    for sample_id, paths in sorted(grouped.items()):
        layout = SampleLayout.PAIRED if len(paths) >= 2 and metadata_by_sample[sample_id].get("input_type") == "fastq" else SampleLayout.SINGLE
        if metadata_by_sample[sample_id].get("input_type") == "sra":
            layout = SampleLayout.UNKNOWN
        samples.append(
            Sample(
                sample_id=sample_id,
                source_path=paths[0],
                source_paths=paths,
                layout=layout,
                project_id=project_id,
                metadata=metadata_by_sample[sample_id],
            )
        )
    return samples


def _downloader_for_params(params: TaskParams):
    source = params.download_source.lower()
    docker_workspace = _docker_workspace_from_params(params)
    if source == "ena":
        return EnaFastqDownloader(proxy=params.download_proxy)
    if source == "sra":
        return PrefetchDownloader(
            max_size=params.download_max_size,
            execution_mode="docker",
            docker_image=params.docker_image,
            docker_workspace=docker_workspace,
        )
    return AutoDownloader(
        prefer="sra" if source == "sra" else "ena",
        ena_downloader=EnaFastqDownloader(proxy=params.download_proxy),
        sra_downloader=PrefetchDownloader(
            max_size=params.download_max_size,
            execution_mode="docker",
            docker_image=params.docker_image,
            docker_workspace=docker_workspace,
        ),
    )


def _download_manifest_urls(urls: list[str], output_dir: Path, dry_run: bool = False, proxy: str = "") -> tuple[bool, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, str]] = []
    for url in urls:
        filename = Path(urllib.parse.urlparse(url).path).name
        target = output_dir / filename
        if dry_run:
            records.append({"url": url, "path": str(target), "status": "dry_run"})
            continue
        try:
            with _urlopen_with_proxy(url, timeout=60, proxy=proxy) as response, target.open("wb") as handle:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
        except OSError as exc:
            return False, f"{url}\n{exc}"
        records.append({"url": url, "path": str(target), "status": "completed"})
    report = output_dir.parent / "reports" / "workflow_url_downloads.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    return True, f"files={len(records)}\n{report}"


def _urlopen_with_proxy(url: str, timeout: float, proxy: str = ""):
    if not proxy:
        return urllib.request.urlopen(url, timeout=timeout)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    return opener.open(url, timeout=timeout)


def _params_to_run_config(params: TaskParams) -> dict:
    config = {
        "execution_mode": "docker",
        "docker_image": params.docker_image,
        "docker_workspace": str(_docker_workspace_from_params(params)),
        "download_proxy": params.download_proxy,
        "fasterq_dump_threads": params.sra_threads,
        "fastqc_threads": params.fastqc_threads,
        "fastqc_quiet": True,
        "trim_galore_quality": params.trim_quality,
        "trim_galore_cores": params.trim_cores,
        "trim_galore_gzip": True,
        "hisat2_index": params.hisat2_index,
        "hisat2_threads": params.hisat2_threads,
        "samtools_threads": params.samtools_threads,
        "samtools_index": True,
        "featurecounts_annotation": params.annotation,
        "featurecounts_threads": params.featurecounts_threads,
        "featurecounts_feature_type": params.featurecounts_feature_type,
        "featurecounts_attribute_type": params.featurecounts_attribute_type,
        "featurecounts_strandness": params.featurecounts_strandness,
        "featurecounts_paired": params.featurecounts_paired,
    }
    if params.disk_guard_strategy == "transfer":
        config["docker_extra_mounts"] = params.spill_paths
    return config


def _finalize_completed_workflow(task: TaskWorkspace, output_dir: Path, samples: list[Sample]) -> tuple[FinalizeResult | None, str]:
    readiness = _workflow_finalize_readiness(task, samples)
    if readiness:
        return None, readiness
    try:
        result = finalize_project(
            task.task_id,
            output_dir,
            samples,
            counts_matrix=task.reports_dir / "count_matrix.tsv",
            report_json=task.reports_dir / "report.json",
            report_markdown=task.reports_dir / "report.md",
            state_path=task.progress_path,
        )
    except Exception as exc:
        return None, f"汇总失败: {type(exc).__name__}: {exc}"
    return result, "汇总完成"


def _workflow_finalize_readiness(task: TaskWorkspace, samples: list[Sample]) -> str:
    try:
        data = json.loads(task.progress_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "未执行汇总：进度文件不可读取。"
    statuses: dict[str, str] = {}
    for sample in samples:
        record = data.get("samples", {}).get(sample.sample_id, {}).get("steps", {}).get("featurecounts")
        status = str(record.get("status") or "") if isinstance(record, dict) else ""
        statuses[sample.sample_id] = status
    incomplete = {sample_id: status or "PENDING" for sample_id, status in statuses.items() if status not in {StepStatus.COMPLETED.value, StepStatus.SKIPPED.value}}
    if incomplete:
        preview = ", ".join(f"{sample_id}={status}" for sample_id, status in sorted(incomplete.items())[:8])
        suffix = f" 等 {len(incomplete)} 个样本" if len(incomplete) > 8 else ""
        return f"未执行汇总：需等待全部样本 featureCounts 完成；未就绪 {preview}{suffix}。"
    return ""


def _print_workflow_run_summary(console: Console, summary, events, finalize_result: FinalizeResult | None = None, finalize_message: str = "") -> None:
    table = Table(title="Workflow Summary")
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("mode", summary.mode)
    table.add_row("samples", str(summary.sample_count))
    table.add_row("steps", str(summary.step_count))
    table.add_row("completed", str(summary.completed_events))
    table.add_row("failed", str(summary.failed_events))
    if finalize_message:
        table.add_row("finalize", finalize_message)
    if finalize_result:
        table.add_row("count_matrix", str(finalize_result.counts_matrix))
        table.add_row("report_json", str(finalize_result.report_json))
        table.add_row("report_markdown", str(finalize_result.report_markdown))
    console.print(table)
    event_table = Table(title="Recent Events")
    event_table.add_column("Sample")
    event_table.add_column("Step")
    event_table.add_column("Event")
    event_table.add_column("Status")
    event_table.add_column("Message")
    for event in events[-20:]:
        event_table.add_row(event.sample_id, event.step_id, event.event, event.status.value, event.message)
    console.print(event_table)


def _workflow_finalize_display_text(finalize_result: FinalizeResult | None, finalize_message: str) -> str:
    if not finalize_result:
        return finalize_message
    return "\n".join(
        [
            finalize_message,
            f"count_matrix: {finalize_result.counts_matrix}",
            f"report_json: {finalize_result.report_json}",
            f"report_markdown: {finalize_result.report_markdown}",
        ]
    )


def _run_workflow_with_tui_progress(
    samples: list[Sample],
    context: RunContext,
    steps: list[Any],
    repository: JsonStateRepository,
    mode: str,
    max_workers: int,
    title: str,
    processing_workers: int | None = None,
    download_workers: int | None = None,
    finalize_callback: Callable[[], tuple[FinalizeResult | None, str]] | None = None,
) -> tuple[WorkflowRunSummary, list[Any], FinalizeResult | None, str]:
    statuses: dict[tuple[str, str], str] = {(sample.sample_id, step.step_id): StepStatus.PENDING.value for sample in samples for step in steps}
    messages: dict[tuple[str, str], str] = {}
    cancel_token = CancellationToken()
    context.config["cancellation_token"] = cancel_token
    context.config["workflow_progress_callback"] = on_download_progress = {"callback": None}
    started_at = time.monotonic()
    result_holder: dict[str, Any] = {"summary": None, "events": [], "error": None, "done": False, "finalize_result": None, "finalize_message": ""}
    manifest_path_value = context.config.get("manifest_path")
    task = context.config.get("task_workspace") if isinstance(context.config.get("task_workspace"), TaskWorkspace) else None
    params = context.config.get("task_params") if isinstance(context.config.get("task_params"), TaskParams) else None
    resource_guard = _RuntimeResourceGuard(task, params, cancel_token, context=context, samples=samples)
    system_text = resource_guard.display_text()

    status_area = TextArea(
        text=_workflow_progress_text(
            samples,
            steps,
            statuses,
            messages,
            title,
            mode,
            max_workers,
            done=False,
            elapsed=0.0,
            processing_workers=processing_workers,
            download_workers=download_workers,
            system_text=system_text,
        ),
        read_only=True,
        scrollbar=True,
        focusable=False,
        wrap_lines=False,
    )
    kb = KeyBindings()

    @kb.add("c")
    def _cancel(event) -> None:
        cancel_token.cancel()
        for key, value in list(statuses.items()):
            if value in {StepStatus.PENDING.value, "QUEUED"}:
                statuses[key] = StepStatus.CANCELLED.value
        status_area.text = _workflow_progress_text(
            samples,
            steps,
            statuses,
            messages,
            title,
            mode,
            max_workers,
            done=False,
            elapsed=time.monotonic() - started_at,
            note="正在取消当前命令。",
            processing_workers=processing_workers,
            download_workers=download_workers,
            system_text=resource_guard.display_text(),
        )
        event.app.invalidate()

    @kb.add("q")
    def _quit_if_done(event) -> None:
        if result_holder["done"]:
            event.app.exit()

    def on_event(event) -> None:
        result_holder["events"].append(event)
        key = (event.sample_id, event.step_id)
        statuses[key] = event.status.value
        if event.message:
            messages[key] = event.message
        elif event.event:
            messages[key] = event.event

    def on_progress(sample_id: str, step_id: str, status: StepStatus, message: str) -> None:
        key = (sample_id, step_id)
        statuses[key] = status.value
        if message:
            messages[key] = message

    on_download_progress["callback"] = on_progress

    def worker() -> None:
        try:
            runner = WorkflowRunner(
                steps=steps,
                repository=repository,
                mode=mode,
                max_workers=max_workers,
                event_callback=on_event,
            )
            result_holder["summary"] = runner.run(samples, context)
            result_holder["events"] = list(runner.events)
            if finalize_callback is not None:
                finalize_result, finalize_message = finalize_callback()
                result_holder["finalize_result"] = finalize_result
                result_holder["finalize_message"] = finalize_message
        except BaseException as exc:
            result_holder["error"] = exc
        finally:
            result_holder["done"] = True

    def expected_size_worker() -> None:
        accessions = [
            str(sample.metadata.get("accession") or sample.sample_id).strip().upper()
            for sample in samples
            if str(sample.metadata.get("input_type") or "") == "remote_sra" and not sample.metadata.get("expected_size_bytes")
        ]
        accessions = sorted({accession for accession in accessions if accession})
        if not accessions:
            return
        try:
            from rnaseq_workflow.steps.download import fetch_sra_runinfo_rows

            rows = fetch_sra_runinfo_rows(accessions, timeout_seconds=8.0)
        except Exception:
            return
        sizes: dict[str, int] = {}
        for row in rows:
            accession = str(row.get("Run") or "").strip().upper()
            size = _coerce_size_mb(row.get("size_MB"))
            if accession and size:
                sizes[accession] = size
        for sample in samples:
            accession = str(sample.metadata.get("accession") or sample.sample_id).strip().upper()
            if accession in sizes:
                sample.metadata["expected_size_bytes"] = sizes[accession]
        if sizes and manifest_path_value:
            _update_manifest_expected_sizes(Path(str(manifest_path_value)), sizes)

    app = Application(
        layout=Layout(
            Box(
                Frame(
                    HSplit(
                        [
                            status_area,
                            Label(text=""),
                            Label(text="按 c 取消。完成后按 q 返回。"),
                        ]
                    ),
                    title=title,
                ),
                padding=1,
            )
        ),
        key_bindings=kb,
        style=STYLE,
        full_screen=True,
    )

    def refresher() -> None:
        while not result_holder["done"]:
            guard_note = resource_guard.tick(statuses, messages)
            note = guard_note or ""
            status_area.text = _workflow_progress_text(
                samples,
                steps,
                statuses,
                messages,
                title,
                mode,
                max_workers,
                done=False,
                elapsed=time.monotonic() - started_at,
                note=note,
                processing_workers=processing_workers,
                download_workers=download_workers,
                system_text=resource_guard.display_text(),
            )
            app.invalidate()
            time.sleep(0.5)
        done_note = ""
        if result_holder["error"]:
            done_note = f"运行异常: {type(result_holder['error']).__name__}: {result_holder['error']}"
        elif result_holder["finalize_message"]:
            done_note = _workflow_finalize_display_text(result_holder["finalize_result"], str(result_holder["finalize_message"]))
        status_area.text = _workflow_progress_text(
            samples,
            steps,
            statuses,
            messages,
            title,
            mode,
            max_workers,
            done=True,
            elapsed=time.monotonic() - started_at,
            note=done_note,
            processing_workers=processing_workers,
            download_workers=download_workers,
            system_text=resource_guard.display_text(),
        )
        app.invalidate()

    threading.Thread(target=worker, daemon=True).start()
    threading.Thread(target=expected_size_worker, daemon=True).start()
    threading.Thread(target=refresher, daemon=True).start()
    app.run()
    if result_holder["error"]:
        summary = WorkflowRunSummary(
            mode=mode,
            sample_count=len(samples),
            step_count=len(steps),
            completed_events=sum(1 for value in statuses.values() if value == StepStatus.COMPLETED.value),
            failed_events=sum(1 for value in statuses.values() if value == StepStatus.FAILED.value),
        )
        return summary, list(result_holder["events"]), result_holder["finalize_result"], str(result_holder["finalize_message"] or "")
    summary = result_holder["summary"]
    if summary is None:
        summary = WorkflowRunSummary(
            mode=mode,
            sample_count=len(samples),
            step_count=len(steps),
            completed_events=sum(1 for value in statuses.values() if value == StepStatus.COMPLETED.value),
            failed_events=sum(1 for value in statuses.values() if value == StepStatus.FAILED.value),
        )
    return summary, list(result_holder["events"]), result_holder["finalize_result"], str(result_holder["finalize_message"] or "")


def _workflow_progress_text(
    samples: list[Sample],
    steps: list[Any],
    statuses: dict[tuple[str, str], str],
    messages: dict[tuple[str, str], str],
    title: str,
    mode: str,
    max_workers: int,
    done: bool,
    elapsed: float,
    note: str = "",
    processing_workers: int | None = None,
    download_workers: int | None = None,
    system_text: str = "",
) -> str:
    if mode == "sample_pipeline":
        return _sample_pipeline_progress_text(
            samples,
            steps,
            statuses,
            messages,
            title,
            max_workers,
            done,
            elapsed,
            note,
            processing_workers=processing_workers,
            download_workers=download_workers,
            system_text=system_text,
        )
    return _stage_batch_progress_text(samples, steps, statuses, messages, title, mode, max_workers, done, elapsed, note, system_text=system_text)


def _stage_batch_progress_text(
    samples: list[Sample],
    steps: list[Any],
    statuses: dict[tuple[str, str], str],
    messages: dict[tuple[str, str], str],
    title: str,
    mode: str,
    max_workers: int,
    done: bool,
    elapsed: float,
    note: str = "",
    system_text: str = "",
) -> str:
    total = max(1, len(samples) * len(steps))
    completed = sum(1 for value in statuses.values() if value == StepStatus.COMPLETED.value)
    failed = sum(1 for value in statuses.values() if value == StepStatus.FAILED.value)
    cancelled = sum(1 for value in statuses.values() if value == StepStatus.CANCELLED.value)
    running = sum(1 for value in statuses.values() if value == StepStatus.RUNNING.value)
    finished = completed + failed + cancelled
    lines = [
        title,
        f"模式: {mode}  并发: {max_workers}  用时: {elapsed:.1f}s",
        f"总进度: {finished}/{total}  completed={completed} failed={failed} cancelled={cancelled} running={running}",
    ]
    if system_text:
        lines.append(system_text)
    if note:
        lines.append(f"提示: {note}")
    lines.append("")
    for sample in samples[:80]:
        row = []
        for step in steps:
            value = statuses.get((sample.sample_id, step.step_id), StepStatus.PENDING.value)
            row.append(f"{step.step_id}:{value}")
        lines.append(f"{sample.sample_id}  " + "  ".join(row))
    if len(samples) > 80:
        lines.append(f"... 还有 {len(samples) - 80} 个样本")
    recent_messages = [(key, msg) for key, msg in messages.items() if msg]
    if recent_messages:
        lines.append("")
        lines.append("最近信息:")
        for (sample_id, step_id), msg in recent_messages[-8:]:
            lines.append(f"{sample_id}/{step_id}: {msg[:160]}")
    if done:
        lines.append("")
        lines.append(_run_done_message(cancelled=cancelled, failed=failed))
    else:
        lines.append("")
        lines.append("运行中。按 c 取消当前任务。")
    return "\n".join(lines)


def _sample_pipeline_progress_text(
    samples: list[Sample],
    steps: list[Any],
    statuses: dict[tuple[str, str], str],
    messages: dict[tuple[str, str], str],
    title: str,
    max_workers: int,
    done: bool,
    elapsed: float,
    note: str = "",
    processing_workers: int | None = None,
    download_workers: int | None = None,
    system_text: str = "",
) -> str:
    total_units = max(1, len(samples) * len(steps))
    completed_units = 0.0
    running = failed = cancelled = 0
    sample_rows: list[str] = []
    for sample in samples[:80]:
        current_step = steps[0].step_id if steps else ""
        current_status = StepStatus.PENDING.value
        current_index = 0
        for index, step in enumerate(steps):
            value = statuses.get((sample.sample_id, step.step_id), StepStatus.PENDING.value)
            if value in {StepStatus.COMPLETED.value, StepStatus.SKIPPED.value}:
                completed_units += 1.0
                current_index = index
                current_step = step.step_id
                current_status = value
                continue
            current_index = index
            current_step = step.step_id
            current_status = value
            if value == StepStatus.RUNNING.value:
                running += 1
                if not _is_waiting_slot_message(str(messages.get((sample.sample_id, step.step_id), ""))):
                    completed_units += 0.5
            elif value == StepStatus.FAILED.value:
                failed += 1
            elif value == StepStatus.CANCELLED.value:
                cancelled += 1
            break
        raw_detail = messages.get((sample.sample_id, current_step), "")
        detail = _sample_pipeline_step_detail(sample, current_step, current_status, raw_detail)
        running_credit = 0.0 if _is_waiting_slot_message(str(detail)) else 0.5
        step_units = current_index + (
            running_credit
            if current_status == StepStatus.RUNNING.value
            else 1.0
            if current_status in {StepStatus.COMPLETED.value, StepStatus.SKIPPED.value}
            else 0.0
        )
        sample_percent = min(step_units / max(1, len(steps)) * 100.0, 100.0)
        stage_percent = _sample_current_stage_percent(sample, current_step, current_status, raw_detail, detail)
        detail_text = f"  {detail}" if detail else ""
        sample_rows.append(
            f"{sample.sample_id}: 样本进度 {_text_progress_bar(sample_percent, width=18)} {sample_percent:.1f}%  "
            f"阶段进度 {_text_progress_bar(stage_percent, width=18)} {stage_percent:.1f}%  "
            f"{current_step} {current_status}{detail_text}"
        )
    overall_percent = completed_units / total_units * 100.0
    concurrency_text = f"样本调度: {max_workers}"
    if download_workers is not None or processing_workers is not None:
        concurrency_text = f"下载并发: {download_workers or '-'}  处理并发: {processing_workers or max_workers}  样本调度: {max_workers}"
    lines = [
        title,
        f"模式: 按样本流水线  {concurrency_text}  用时: {elapsed:.1f}s",
        f"样本数: {len(samples)}  当前显示: {min(len(samples), 80)}",
        f"总进度: {_text_progress_bar(overall_percent)} {overall_percent:.1f}%  步骤单位={completed_units:.1f}/{total_units} running={running} failed={failed} cancelled={cancelled}",
    ]
    if system_text:
        lines.append(system_text)
    download_summary = _sample_pipeline_download_summary(samples, statuses, messages)
    if download_summary:
        lines.append(download_summary)
    if note:
        lines.append(f"提示: {note}")
    lines.append("")
    lines.extend(sample_rows)
    if len(samples) > 80:
        lines.append(f"... 还有 {len(samples) - 80} 个样本")
    lines.append("")
    lines.append(_run_done_message(cancelled=cancelled, failed=failed) if done else "运行中。按 c 取消当前任务。")
    return "\n".join(lines)


class _RuntimeResourceGuard:
    def __init__(self, task: TaskWorkspace | None, params: TaskParams | None, cancel_token: CancellationToken, context: RunContext | None = None, samples: list[Sample] | None = None) -> None:
        self.task = task
        self.params = params or default_task_params(task)
        self.cancel_token = cancel_token
        self.context = context
        self.samples = samples or []
        self.sampler = CpuSampler()
        self.snapshot: SystemSnapshot | None = None
        self.note = ""
        self.triggered = False
        self.last_check = 0.0

    def tick(self, statuses: dict[tuple[str, str], str], messages: dict[tuple[str, str], str]) -> str:
        if not self.params.resource_guard_enabled:
            self.note = ""
            return ""
        now = time.monotonic()
        if now - self.last_check < 1.0:
            return self.note
        self.last_check = now
        self.snapshot = _system_snapshot_for_params(self.params, self.task, sampler=self.sampler)
        disk = self.snapshot.work_disk
        if not disk or disk.warning_level != "critical" or self.triggered:
            self.note = ""
            return ""
        if self.params.disk_guard_strategy == "transfer" and self.params.spill_large_outputs:
            if self.task and self.context and self.params.spill_paths:
                output_dir = _workflow_processing_output_dir(self.task, self.params)
                moved = _activate_spill_output_root(self.task, self.context, self.samples, output_dir, statuses)
                self.triggered = True
                migrated = f"，已迁移 {moved} 个样本已有产物" if moved else ""
                self.note = f"工作盘空间不足，后续大产物将写入备用路径: {output_dir}{migrated}"
                return self.note
            self.note = "工作盘空间不足，但备用路径未配置，已自动取消当前运行。"
        elif self.params.disk_guard_strategy == "transfer" and self.task and self.params.spill_paths:
            self.triggered = True
            moved, target = _spill_stable_task_artifacts(self.task, self.params, statuses)
            if moved:
                self.note = f"工作盘空间不足，已转移 {moved} 个稳定产物到 {target}。"
                return self.note
            self.note = "工作盘空间不足，未找到可安全转移的稳定产物，已自动取消当前运行。"
        else:
            self.triggered = True
            self.note = "工作盘空间不足，已自动取消并终止当前运行。"
        self.triggered = True
        self.cancel_token.cancel()
        for key, value in list(statuses.items()):
            if value in {StepStatus.PENDING.value, "QUEUED", StepStatus.RUNNING.value}:
                statuses[key] = StepStatus.CANCELLED.value
                messages[key] = self.note
        return self.note

    def display_text(self) -> str:
        if not self.params.resource_guard_enabled:
            return "系统: 资源智能预警已关闭"
        if self.snapshot is None:
            self.snapshot = _system_snapshot_for_params(self.params, self.task, sampler=self.sampler)
        return _compact_system_snapshot_text(self.snapshot)


def _system_snapshot_for_params(params: TaskParams, task: TaskWorkspace | None, sampler: CpuSampler | None = None) -> SystemSnapshot:
    work_path = task.root if task else Path.cwd()
    return collect_system_snapshot(
        work_path,
        spill_paths=params.spill_paths if params.disk_guard_strategy == "transfer" else (),
        sampler=sampler,
        min_free_gb=params.disk_guard_min_free_gb,
        min_free_percent=params.disk_guard_min_free_percent,
    )


def _compact_system_snapshot_text(snapshot: SystemSnapshot) -> str:
    cpu = "CPU: --"
    if snapshot.cpu.percent is not None:
        cpu = f"CPU: {snapshot.cpu.percent:.1f}%"
        if snapshot.cpu.per_core:
            core_text = ",".join(f"{value:.0f}" for value in snapshot.cpu.per_core[:16])
            if len(snapshot.cpu.per_core) > 16:
                core_text += ",..."
            cpu += f" cores[{core_text}]"
    memory = "内存: --"
    if snapshot.memory.percent is not None and snapshot.memory.used_bytes is not None and snapshot.memory.total_bytes is not None:
        memory = f"内存: {snapshot.memory.percent:.1f}% {_format_bytes(snapshot.memory.used_bytes)}/{_format_bytes(snapshot.memory.total_bytes)}"
    disk = "工作盘: --"
    if snapshot.work_disk:
        disk = _disk_status_text("工作盘", snapshot.work_disk)
    spill = "  ".join(_disk_status_text("转移盘", item) for item in snapshot.spill_disks)
    return "  ".join(part for part in [cpu, memory, disk, spill] if part)


def _disk_status_text(label: str, disk: DiskSnapshot) -> str:
    state = {"ok": "OK", "warning": "WARN", "critical": "CRIT"}.get(disk.warning_level, disk.warning_level.upper())
    return f"{label}: {state} {disk.percent:.1f}% used free={_format_bytes(disk.free_bytes)} path={disk.path}"


def _system_snapshot_text(snapshot: SystemSnapshot, params: TaskParams, task: TaskWorkspace | None, color: bool = True) -> str:
    lines = [
        _compact_system_snapshot_text(snapshot),
        "",
        f"当前任务: {task.root if task else '未选择'}",
        f"资源智能预警: {'开启' if params.resource_guard_enabled else '关闭'}",
        f"触发阈值: 剩余 <= {params.disk_guard_min_free_gb:g}GB 或 <= {params.disk_guard_min_free_percent:g}%",
        f"处理策略: {'取消并终止' if params.disk_guard_strategy == 'cancel' else '后续大产物写入转移路径'}",
    ]
    if params.disk_guard_strategy == "transfer":
        lines.append(f"大产物重定向: {'开启' if params.spill_large_outputs else '关闭'}")
        lines.append("转移路径: " + ("; ".join(params.spill_paths) if params.spill_paths else "未配置"))
    return "\n".join(lines)


def _artifact_locations_path(task: TaskWorkspace) -> Path:
    return task.metadata_dir / "artifact_locations.json"


def _activate_spill_output_root(
    task: TaskWorkspace,
    context: RunContext,
    samples: list[Sample],
    output_dir: Path,
    statuses: dict[tuple[str, str], str] | None = None,
) -> int:
    lock = context.config.get("_output_root_lock")
    if lock is None:
        lock = threading.Lock()
        context.config["_output_root_lock"] = lock
    with lock:
        context.output_dir = output_dir
        moved = 0
        for sample in samples:
            sample.metadata["_workflow_output_dir"] = str(output_dir)
            if _sample_has_active_step(sample.sample_id, statuses or {}):
                continue
            if _migrate_sample_outputs_to_root(task, sample, output_dir):
                moved += 1
        return moved


def _sample_has_active_step(sample_id: str, statuses: dict[tuple[str, str], str]) -> bool:
    return any(current_sample == sample_id and status == StepStatus.RUNNING.value for (current_sample, _step), status in statuses.items())


def _sync_sample_output_root(sample: Sample, context: RunContext) -> None:
    task = context.config.get("task_workspace")
    if not isinstance(task, TaskWorkspace):
        return
    root = _context_output_root(context, task)
    sample.metadata["_workflow_output_dir"] = str(root)
    if root == task.task_output_dir:
        return
    lock = context.config.get("_output_root_lock")
    if lock is None:
        lock = threading.Lock()
        context.config["_output_root_lock"] = lock
    with lock:
        context.output_dir = root
        _migrate_sample_outputs_to_root(task, sample, root)


def _context_output_root(context: RunContext, task: TaskWorkspace) -> Path:
    if context.output_dir != task.task_output_dir:
        return context.output_dir
    existing = _existing_output_root(task)
    if existing is not None:
        context.output_dir = existing
        return existing
    return task.task_output_dir


def _migrate_sample_outputs_to_root(task: TaskWorkspace, sample: Sample, target_root: Path) -> bool:
    source_sample = task.samples_dir / sample.sample_id
    target_sample = target_root / "samples" / sample.sample_id
    if source_sample == target_sample:
        return False
    if not source_sample.exists():
        _rewrite_sample_paths_for_output_root(sample, task.task_output_dir, target_root)
        _rewrite_progress_paths_for_output_root(task, sample.sample_id, task.task_output_dir, target_root)
        return False
    moved_any = False
    target_sample.mkdir(parents=True, exist_ok=True)
    for source in sorted(source_sample.iterdir(), key=lambda item: item.name):
        target = target_sample / source.name
        if target.exists():
            _record_artifact_location(task, source, target, reason="disk_guard_spill_existing")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(source), str(target))
        except OSError:
            continue
        _record_artifact_location(task, source, target, reason="disk_guard_spill_existing")
        moved_any = True
    _rewrite_sample_paths_for_output_root(sample, task.task_output_dir, target_root)
    _rewrite_progress_paths_for_output_root(task, sample.sample_id, task.task_output_dir, target_root)
    try:
        if source_sample.exists() and not any(source_sample.iterdir()):
            source_sample.rmdir()
    except OSError:
        pass
    return moved_any


def _rewrite_sample_paths_for_output_root(sample: Sample, original_root: Path, target_root: Path) -> None:
    rewritten = [_rewrite_path_root(path, original_root, target_root) for path in sample.source_paths]
    sample.source_paths = rewritten
    sample.source_path = rewritten[0] if rewritten else sample.source_path


def _rewrite_path_root(path: Path, original_root: Path, target_root: Path) -> Path:
    try:
        rel = path.relative_to(original_root)
    except ValueError:
        return path
    candidate = target_root / rel
    return candidate if candidate.exists() else path


def _rewrite_progress_paths_for_output_root(task: TaskWorkspace, sample_id: str, original_root: Path, target_root: Path) -> None:
    path = task.progress_path
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    sample_data = data.get("samples", {}).get(sample_id)
    if not isinstance(sample_data, dict):
        return
    changed = False
    source_path = sample_data.get("source_path")
    if isinstance(source_path, str):
        rewritten_source = _rewrite_progress_path_value(source_path, original_root, target_root)
        if rewritten_source != source_path:
            sample_data["source_path"] = rewritten_source
            changed = True
    for step_data in sample_data.get("steps", {}).values():
        if not isinstance(step_data, dict):
            continue
        for field in ("inputs", "outputs"):
            values = step_data.get(field)
            if not isinstance(values, list):
                continue
            rewritten = [_rewrite_progress_path_value(value, original_root, target_root) for value in values]
            if rewritten != values:
                step_data[field] = rewritten
                changed = True
    if changed:
        _write_json_atomic(path, data)


def _rewrite_progress_path_value(value: Any, original_root: Path, target_root: Path) -> Any:
    if not isinstance(value, str) or not value:
        return value
    return str(_rewrite_path_root(Path(value), original_root, target_root))


def _spill_stable_task_artifacts(task: TaskWorkspace, params: TaskParams, statuses: dict[tuple[str, str], str]) -> tuple[int, str]:
    target_root = _choose_spill_target(params.spill_paths, params)
    if target_root is None:
        return 0, ""
    moved = 0
    active_samples = {sample_id for (sample_id, _step), status in statuses.items() if status == StepStatus.RUNNING.value}
    for source in _stable_artifact_candidates(task, active_samples):
        rel = source.relative_to(task.root)
        target = _spill_task_output_dir(target_root, task) / rel
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(source), str(target))
        except OSError:
            continue
        _record_artifact_location(task, source, target, reason="disk_guard_spill")
        moved += 1
    return moved, str(target_root)


def _choose_spill_target(paths: list[str], params: TaskParams) -> Path | None:
    for raw in paths:
        if not str(raw or "").strip():
            continue
        path = Path(raw).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        disk = collect_system_snapshot(path, min_free_gb=params.disk_guard_min_free_gb, min_free_percent=params.disk_guard_min_free_percent).work_disk
        if disk and disk.warning_level != "critical":
            return path
    return None


def _stable_artifact_candidates(task: TaskWorkspace, active_samples: set[str]) -> list[Path]:
    candidates: list[Path] = []
    if task.samples_dir.exists():
        for sample_dir in task.samples_dir.iterdir():
            if not sample_dir.is_dir() or sample_dir.name in active_samples:
                continue
            reports = [path for path in sample_dir.rglob("*") if path.is_file() and path.suffix.lower() in {".html", ".zip", ".txt", ".log"}]
            if reports:
                candidates.extend(reports)
    return sorted(candidates, key=lambda item: item.stat().st_size if item.exists() else 0, reverse=True)


def _record_artifact_location(task: TaskWorkspace, source: Path, target: Path, reason: str) -> None:
    path = _artifact_locations_path(task)
    records = _read_artifact_location_records(task)
    records.append(
        {
            "original_path": str(source),
            "current_path": str(target),
            "task_id": task.task_id,
            "reason": reason,
            "moved_at": datetime.now().isoformat(timespec="seconds"),
        }
    )
    _write_json_atomic(path, records)


def _record_output_root(task: TaskWorkspace, original_root: Path, current_root: Path, reason: str) -> None:
    path = _artifact_locations_path(task)
    records = _read_artifact_location_records(task)
    key = {
        "original_path": str(original_root / "samples"),
        "current_path": str(current_root / "samples"),
        "task_id": task.task_id,
        "reason": reason,
    }
    for record in records:
        if all(record.get(field) == value for field, value in key.items()):
            return
    records.append({**key, "mapped_at": datetime.now().isoformat(timespec="seconds")})
    _write_json_atomic(path, records)


def _read_artifact_location_records(task: TaskWorkspace) -> list[dict[str, Any]]:
    path = _artifact_locations_path(task)
    if not path.exists():
        return []
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return loaded if isinstance(loaded, list) else []


def _write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def _sample_pipeline_step_detail(sample: Sample, step_id: str, status: str, message: str) -> str:
    if step_id == "download":
        return _sample_download_detail(sample, status, str(message or ""))
    detail = _compact_progress_detail(message)
    if status == StepStatus.FAILED.value:
        detail = _summarize_step_failure(step_id, str(message or ""))
    if _is_waiting_slot_message(detail):
        return detail
    output_dir = _pipeline_step_output_dir(step_id, Path(sample.metadata.get("_workflow_output_dir", "")) if sample.metadata.get("_workflow_output_dir") else None, sample)
    if output_dir is None:
        return detail
    activity = _sample_activity_text(status, output_dir, sample.source_paths).strip()
    if detail and not detail.startswith("已获得处理槽位"):
        return f"{detail}  {activity}"
    return activity


def _sample_current_stage_percent(sample: Sample, step_id: str, status: str, raw_detail: str, detail: str) -> float:
    if status in {StepStatus.COMPLETED.value, StepStatus.SKIPPED.value}:
        return 100.0
    if status in {StepStatus.FAILED.value, StepStatus.CANCELLED.value, StepStatus.PENDING.value}:
        return 0.0
    if _is_waiting_slot_message(str(detail)):
        return 0.0
    if step_id == "download":
        progress = _parse_download_detail(raw_detail)
        percent = _estimated_percent(
            int(progress["downloaded"]) if progress["downloaded"] is not None else 0,
            0,
            int(progress["expected"]) if progress["expected"] is not None else _coerce_positive_int(sample.metadata.get("expected_size_bytes")),
        )
        if percent is not None:
            return max(0.0, min(100.0, percent))
    return 50.0 if status == StepStatus.RUNNING.value else 0.0


def _sample_download_detail(sample: Sample, status: str, detail: str) -> str:
    if _is_waiting_slot_message(detail):
        return detail
    if status == StepStatus.FAILED.value or _download_detail_is_failure(detail):
        return _summarize_download_failure(detail)
    progress = _parse_download_detail(detail)
    sample_expected = _coerce_positive_int(sample.metadata.get("expected_size_bytes"))
    downloaded = progress["downloaded"]
    expected = progress["expected"] or sample_expected
    speed = progress["speed"]
    if downloaded is None or not expected:
        return detail
    parts: list[str] = []
    percent = _estimated_percent(int(downloaded), 0, int(expected)) if expected else None
    if percent is not None:
        parts.append(f"{percent:.1f}%")
    size = _format_bytes(int(downloaded))
    if expected:
        size += f"/{_format_bytes(int(expected))}"
    parts.append(size)
    if speed is not None:
        parts.append(f"{_format_bytes(float(speed))}/s")
    if speed is not None and expected:
        eta = _download_eta(int(downloaded), float(speed), int(expected), percent)
        if eta:
            parts.append(f"剩余:{eta}")
    message = _download_status_message(detail)
    if message:
        parts.append(message)
    return " ".join(parts)


def _download_detail_is_failure(detail: str) -> bool:
    text = str(detail or "").lower()
    return any(token in text for token in ("failed", "cannot ", " rc(", "validation failed", "download stalled"))


def _summarize_download_failure(detail: str) -> str:
    text = " ".join(str(detail or "").split())
    if "current preference is set to retrieve sra normalized format" in text.lower() and not _download_detail_is_failure(text):
        return "prefetch 未返回具体失败原因；请查看容器日志或重试"
    patterns = (
        ("Cannot CreateFile", "无法创建下载临时文件"),
        ("Cannot keep transaction file", "无法写入下载事务文件"),
        ("HTTPS download failed", "HTTPS 下载失败"),
        ("failed to download", "下载失败"),
        ("SRA validation failed", "SRA 校验失败"),
        ("download stalled", "下载停滞"),
        ("path not found", "路径不存在"),
    )
    reasons = [label for marker, label in patterns if marker.lower() in text.lower()]
    if not reasons:
        reasons = ["下载失败"]
    path = _extract_interesting_path(text)
    suffix = f": {path}" if path else ""
    return f"{'；'.join(dict.fromkeys(reasons))}{suffix}"


def _summarize_step_failure(step_id: str, detail: str) -> str:
    text = " ".join(str(detail or "").split())
    if step_id == "trim_galore":
        summary = _last_matching_message(text, ("Failed to write", "No such file", "ERROR", "Error", "failed", "cannot", "not found"))
        if summary:
            return summary
    return _compact_progress_detail(detail)


def _last_matching_message(text: str, markers: tuple[str, ...]) -> str:
    parts = re.split(r"(?<=[.!?])\s+|\s+\|\s+", text)
    for part in reversed([item.strip() for item in parts if item.strip()]):
        lowered = part.lower()
        if any(marker.lower() in lowered for marker in markers):
            return _compact_progress_detail(part)
    return ""


def _extract_interesting_path(text: str) -> str:
    match = re.search(r"(/workspace/[^\s)]+)", text)
    if not match:
        match = re.search(r"([A-Za-z]:\\[^\s)]+)", text)
    if not match:
        return ""
    return Path(match.group(1).replace("\\", "/")).name or match.group(1)[-80:]


def _download_status_message(detail: str) -> str:
    words: list[str] = []
    for part in str(detail or "").split():
        if part.endswith("%"):
            continue
        if part.startswith("剩余:"):
            continue
        if part.endswith("/s") and _parse_size_text(part[:-2]) is not None:
            continue
        if "/" in part and not part.endswith("/s"):
            left, right = part.split("/", 1)
            if _parse_size_text(left) is not None and _parse_size_text(right) is not None:
                continue
        if any(part.upper().endswith(unit) for unit in ("B", "KB", "MB", "GB", "TB")) and _parse_size_text(part) is not None:
            continue
        words.append(part)
    return " ".join(words)


def _pipeline_step_output_dir(step_id: str, root: Path | None, sample: Sample) -> Path | None:
    if root is None:
        return None
    sample_root = root / "samples" / sample.sample_id
    mapping = {
        "sra_to_fastq": sample_root / "raw_fastq",
        "fastqc": sample_root / "qc_raw",
        "trim_galore": sample_root / "trimmed_fastq",
        "hisat2": sample_root / "alignment",
        "samtools_sort": sample_root / "alignment",
        "featurecounts": sample_root / "quantification",
    }
    return mapping.get(step_id)


def _sample_pipeline_download_summary(
    samples: list[Sample],
    statuses: dict[tuple[str, str], str],
    messages: dict[tuple[str, str], str],
) -> str:
    if not any((sample.sample_id, "download") in statuses for sample in samples):
        return ""
    downloaded = 0
    expected = 0
    known_expected = False
    speed = 0.0
    active = 0
    completed = failed = cancelled = 0
    for sample in samples:
        key = (sample.sample_id, "download")
        status = statuses.get(key, StepStatus.PENDING.value)
        if status == StepStatus.RUNNING.value:
            active += 1
        elif status in {StepStatus.COMPLETED.value, StepStatus.SKIPPED.value}:
            completed += 1
        elif status == StepStatus.FAILED.value:
            failed += 1
        elif status == StepStatus.CANCELLED.value:
            cancelled += 1
        progress = _parse_download_detail(messages.get(key, ""))
        sample_expected = _coerce_positive_int(sample.metadata.get("expected_size_bytes"))
        actual_downloaded = _sample_downloaded_bytes(sample)
        progress_downloaded = int(progress["downloaded"]) if progress["downloaded"] is not None else 0
        if status in {StepStatus.COMPLETED.value, StepStatus.SKIPPED.value}:
            downloaded += actual_downloaded or progress_downloaded or sample_expected or 0
        elif status in {StepStatus.RUNNING.value, StepStatus.CANCELLED.value}:
            downloaded += max(progress_downloaded, actual_downloaded)
        elif progress_downloaded:
            downloaded += progress_downloaded
        if progress["expected"] is not None:
            expected += int(progress["expected"])
            known_expected = True
        elif sample_expected:
            expected += sample_expected
            known_expected = True
        if progress["speed"] is not None:
            speed += float(progress["speed"])
    percent = _estimated_percent(downloaded, 0, expected if known_expected else None)
    expected_text = f"/{_format_bytes(expected)}" if known_expected else ""
    eta = _download_eta(downloaded, speed, expected if known_expected else None, percent)
    percent_text = f" {_text_progress_bar(percent)} {percent:.1f}%" if percent is not None else ""
    eta_text = f"  剩余: {eta}" if eta else ""
    return (
        f"下载汇总:{percent_text}  总大小: {_format_bytes(downloaded)}{expected_text}  "
        f"总速度: {_format_bytes(speed)}/s{eta_text}  "
        f"active={active} completed={completed} failed={failed} cancelled={cancelled}"
    )


def _sample_downloaded_bytes(sample: Sample) -> int:
    total = 0
    seen: set[str] = set()
    paths = [Path(path) for path in sample.metadata.get("download_paths") or []]
    if not paths:
        paths = [
            path
            for path in sample.source_paths
            if _path_is_under_any(path, [Path(value) for value in sample.metadata.get("download_roots") or []])
        ]
    for path in paths:
        if not path.exists() or not path.is_file():
            continue
        if not (_is_fastq_path(path) or path.name.lower().endswith(".sra")):
            continue
        try:
            resolved = str(path.resolve())
        except OSError:
            resolved = str(path)
        if resolved in seen:
            continue
        seen.add(resolved)
        try:
            total += path.stat().st_size
        except OSError:
            continue
    return total


def _path_is_under_any(path: Path, roots: list[Path]) -> bool:
    if not roots:
        return False
    for root in roots:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def _compact_progress_detail(detail: str, max_len: int = 150) -> str:
    text = " ".join(line.strip() for line in str(detail or "").splitlines() if line.strip())
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def _parse_download_detail(detail: str) -> dict[str, float | int | None]:
    result: dict[str, float | int | None] = {"downloaded": None, "expected": None, "speed": None}
    for part in detail.split():
        if "/" in part and not part.endswith("/s"):
            left, right = part.split("/", 1)
            downloaded = _parse_size_text(left)
            expected = _parse_size_text(right)
            if downloaded is not None:
                result["downloaded"] = downloaded
            if expected is not None:
                result["expected"] = expected
            continue
        if part.endswith("/s"):
            speed = _parse_size_text(part[:-2])
            if speed is not None:
                result["speed"] = float(speed)
            continue
        if result["downloaded"] is None:
            downloaded = _parse_size_text(part)
            if downloaded is not None:
                result["downloaded"] = downloaded
    return result


def _parse_size_text(value: str) -> int | None:
    raw = value.strip()
    if not raw:
        return None
    units = {
        "B": 1,
        "KB": 1024,
        "MB": 1024**2,
        "GB": 1024**3,
        "TB": 1024**4,
    }
    for unit, multiplier in sorted(units.items(), key=lambda item: len(item[0]), reverse=True):
        if raw.upper().endswith(unit):
            number = raw[: -len(unit)]
            try:
                return int(float(number) * multiplier)
            except ValueError:
                return None
    try:
        return int(float(raw))
    except ValueError:
        return None


def _run_done_message(cancelled: int = 0, failed: int = 0) -> str:
    if cancelled:
        return "已取消。按 q 返回。"
    if failed:
        return "未完成。按 q 返回。"
    return "已完成。按 q 返回。"


def _is_waiting_slot_message(message: str) -> bool:
    return str(message).startswith(("等待下载槽位", "排队等待下载槽位", "等待处理槽位", "排队等待处理槽位"))


def _current_or_new_task(state: TuiState) -> TaskWorkspace | None:
    if state.task:
        state.task.ensure()
        return state.task
    user_id = _ensure_user(state)
    if not user_id:
        return None
    user = state.workspace.ensure_user(user_id)
    has_tasks = bool(user.list_tasks())
    choices = []
    if has_tasks:
        choices.append(("select", "选择已有任务"))
    choices.append(("new", "创建新任务"))
    choices.append(("back", "返回"))
    choice = _menu("当前没有任务", "请选择已有任务，或创建新任务后继续。", choices)
    if choice == "select":
        return _select_task(state)
    if choice == "new":
        return _create_task(state)
    return None


def _config_menu(state: TuiState) -> None:
    while True:
        choice = _menu(
            "配置",
            f"当前 config: {state.config}",
            [
                ("select", "选择 config 文件"),
                ("init", "创建 config 模板"),
                ("form", "配置表单"),
                ("show", "查看配置"),
                ("validate", "校验配置"),
                ("plan", "查看运行计划"),
                ("back", "返回"),
            ],
        )
        if choice in (None, "back"):
            return
        if choice == "select":
            path = _path_input("选择 config 文件", state.config)
            if path:
                state.config = path
        elif choice == "init":
            output = _path_input("输出 config", state.config)
            if not output:
                continue
            project_id = _input("project_id", "项目 ID", "rnaseq_project")
            if project_id is None:
                continue
            try:
                write_config_template(output, ConfigTemplateOptions(project_id=project_id), overwrite=_yes_no("覆盖已有文件", False))
                state.config = output
                _message("完成", f"已写入 {output}")
            except FileExistsError as exc:
                _message("错误", str(exc))
        elif choice == "form":
            _config_form_menu(state)
        elif choice == "show":
            cfg = _load_config(state)
            if cfg:
                _capture_output(state, lambda console: print_config_summary(console, cfg), "配置摘要")
        elif choice == "validate":
            cfg = _load_config(state)
            if cfg:
                result = validate_project_config(cfg, check_files=_yes_no("检查文件是否存在", True))
                _capture_output(state, lambda console: print_validation_result(console, result), "配置校验")
        elif choice == "plan":
            cfg = _load_config(state)
            if cfg:
                samples = samples_from_config(cfg.samples, cfg.project_id)
                _capture_output(state, lambda console: print_workflow_plan(console, cfg, samples), "运行计划")


def _system_resource_menu(state: TuiState) -> None:
    while True:
        task = state.task
        params = _load_task_params_defaults(task) if task else default_task_params()
        snapshot = _system_snapshot_for_params(params, task)
        choice = _menu(
            "系统信息与资源策略",
            _system_snapshot_text(snapshot, params, task, color=False),
            [
                ("refresh", "刷新系统信息"),
                ("config", "配置资源预警策略"),
                ("records", "查看跨路径产物记录"),
                ("back", "返回"),
            ],
        )
        if choice in (None, "back"):
            return
        if choice == "refresh":
            continue
        if choice == "config":
            _resource_policy_page(state)
        elif choice == "records":
            _show_artifact_location_records(state)


def _resource_policy_page(state: TuiState) -> None:
    task = _current_or_new_task(state)
    if not task:
        return
    defaults = _load_task_params_defaults(task)
    fields = [
        ("resource_guard_enabled", "资源智能预警", "开启后正式运行会实时检查工作目录所在盘，空间不足时按策略自动处理。", "choice", None, (("yes", "开启"), ("no", "关闭"))),
        ("disk_guard_min_free_gb", "磁盘最低剩余 GB", "工作盘剩余空间低于该值时触发预警。", "float", 0, ()),
        ("disk_guard_min_free_percent", "磁盘最低剩余百分比", "工作盘剩余比例低于该值时触发预警。", "float", 0, ()),
        ("disk_guard_strategy", "空间不足处理策略", "默认取消并终止当前运行；转移策略会把后续样本大产物直接写到转移路径。", "choice", None, (("cancel", "取消并终止当前运行"), ("transfer", "大产物写入转移路径"))),
        ("spill_large_outputs", "后续大产物写入转移路径", "开启后 SRA 转 FASTQ、FastQC、Trim、比对和定量的 samples 产物会写到转移路径的项目结构中。", "choice", None, (("yes", "开启"), ("no", "关闭"))),
        ("spill_paths", "产物转移路径", "转移策略使用。可填写多个路径，用分号分隔。", "str", None, ()),
    ]
    values = _tool_run_wizard(
        "资源预警策略",
        {
            "resource_guard_enabled": "yes" if defaults.resource_guard_enabled else "no",
            "disk_guard_min_free_gb": defaults.disk_guard_min_free_gb,
            "disk_guard_min_free_percent": defaults.disk_guard_min_free_percent,
            "disk_guard_strategy": defaults.disk_guard_strategy,
            "spill_large_outputs": "yes" if defaults.spill_large_outputs else "no",
            "spill_paths": "; ".join(defaults.spill_paths),
        },
        fields,
    )
    if values is None:
        return
    data = defaults.to_dict()
    data.update(
        {
            "resource_guard_enabled": values["resource_guard_enabled"] == "yes",
            "disk_guard_min_free_gb": float(values["disk_guard_min_free_gb"]),
            "disk_guard_min_free_percent": float(values["disk_guard_min_free_percent"]),
            "disk_guard_strategy": str(values["disk_guard_strategy"]),
            "spill_large_outputs": values["spill_large_outputs"] == "yes",
            "spill_paths": _parse_spill_paths(str(values.get("spill_paths") or "")),
        }
    )
    params = TaskParams(**data)
    issues = validate_task_params(params)
    if issues:
        _message("参数校验失败", "\n".join(f"{issue.field}: {issue.message}" for issue in issues))
        return
    path = write_task_params(params, task.metadata_dir / "params.json")
    _message("资源策略已保存", str(path))


def _show_artifact_location_records(state: TuiState) -> None:
    task = state.task
    if not task:
        _message("未选择任务", "请先选择任务。")
        return
    path = _artifact_locations_path(task)
    if not path.exists():
        _message("跨路径产物记录", "暂无记录。")
        return
    _message("跨路径产物记录", _truncate_output(path.read_text(encoding="utf-8")))


def _config_form_menu(state: TuiState) -> None:
    while True:
        data = _read_config_data(state.config)
        choice = _menu(
            "配置表单",
            _config_form_summary(state.config, data),
            [
                ("project", f"1 项目与目录  project_id={data.get('project_id', '未设置')}"),
                ("execution", f"2 执行环境与并发  mode={data.get('execution_mode', 'docker')} workers={data.get('max_workers', DEFAULT_TUI_CONCURRENCY)}"),
                ("reference", f"3 Reference 与注释  reference={data.get('reference_id', '未设置')}"),
                ("samples", f"4 样本表  samples={len(data.get('samples') or [])}"),
                ("validate", "保存后校验"),
                ("back", "返回"),
            ],
        )
        if choice in (None, "back"):
            return
        if choice == "project":
            _edit_config_project_page(state, data)
        elif choice == "execution":
            _edit_config_execution_page(state, data)
        elif choice == "reference":
            _edit_config_reference_page(state, data)
        elif choice == "samples":
            _edit_config_samples_page(state, data)
        elif choice == "validate":
            cfg = _load_config(state)
            if cfg:
                result = validate_project_config(cfg, check_files=False)
                _capture_output(state, lambda console: print_validation_result(console, result), "配置校验")


def _read_config_data(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    return data if isinstance(data, dict) else {}


def _write_config_data(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(data, handle, allow_unicode=True, sort_keys=False)


def _config_form_summary(path: Path, data: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"文件: {path}",
            f"project_id: {data.get('project_id', '未设置')}",
            f"asset_root: {data.get('asset_root', 'workspace')}",
            f"execution_mode: {data.get('execution_mode', 'docker')}",
            f"samples: {len(data.get('samples') or [])}",
            "选择一个分组进入表单；每页保存后会回到这里。",
        ]
    )


def _edit_config_project_page(state: TuiState, data: dict[str, Any]) -> None:
    form = {
        "project_id": str(data.get("project_id") or "rnaseq_project"),
        "output_mode": "auto",
        "output_dir": str(data.get("output_dir") or ""),
    }
    if form["output_dir"]:
        form["output_mode"] = "custom"
    fields = [
        ("project_id", "项目 ID", "用于报告、日志和输出命名。"),
        ("output_mode", "输出目录方式", "自动使用任务目录；需要固定路径时选择自定义。"),
        ("output_dir", "输出目录", "仅在选择自定义时填写。建议优先使用任务目录。"),
    ]
    index = 0
    while True:
        key, title, help_text = fields[index]
        actions = [("edit", "编辑")]
        if index > 0:
            actions.append(("prev", "上一步"))
        if index < len(fields) - 1:
            actions.append(("next", "下一步"))
        actions.append(("save", "保存"))
        actions.append(("back", "返回"))
        choice = _menu(
            f"项目与目录 {index + 1}/{len(fields)}",
            f"{title}\n当前: {_format_project_config_value(key, form.get(key))}\n\n{help_text}",
            actions,
        )
        if choice in (None, "back"):
            return
        if choice == "prev":
            index -= 1
            continue
        if choice == "next":
            if key == "output_dir" and form.get("output_mode") == "custom" and not str(form.get("output_dir") or "").strip():
                _message("需要补充", "请填写自定义输出目录。")
                continue
            index += 1
            continue
        if choice == "save":
            if not str(form.get("project_id") or "").strip():
                _message("无法保存", "请先填写项目 ID。")
                continue
            data["project_id"] = str(form["project_id"]).strip()
            data["asset_root"] = str(state.asset_root)
            if state.user_id:
                data["user_id"] = state.user_id
            if state.task_id:
                data["task_id"] = state.task_id
            if form.get("output_mode") == "custom" and str(form.get("output_dir") or "").strip():
                data["output_dir"] = str(form["output_dir"]).strip()
            else:
                data.pop("output_dir", None)
            _write_config_data(state.config, data)
            _message("已保存", "项目与目录配置已更新。")
            return
        if choice == "edit":
            _edit_project_config_field(key, form, state)


def _format_project_config_value(key: str, value: Any) -> str:
    if key == "output_mode":
        return "自定义" if value == "custom" else "自动使用任务目录"
    if not value:
        return "未设置"
    return str(value)


def _edit_project_config_field(key: str, data: dict[str, Any], state: TuiState) -> None:
    if key == "project_id":
        value = _input("项目 ID", "用于报告、日志和输出命名。", str(data.get(key) or "rnaseq_project"))
        if value is not None:
            data[key] = value.strip()
    elif key == "output_mode":
        value = _menu(
            "输出目录方式",
            "自动模式会使用任务目录；自定义模式允许指定固定路径。",
            [("auto", "自动使用任务目录"), ("custom", "自定义输出目录"), ("back", "返回")],
        )
        if value in {"auto", "custom"}:
            data[key] = value
            if value == "auto":
                data["output_dir"] = ""
    elif key == "output_dir":
        if data.get("output_mode") != "custom":
            _message("提示", "当前不是自定义模式。")
            return
        default = data.get(key) or (state.task.task_output_dir if state.task else Path.cwd())
        value = _path_input("输出目录", default, directory=True)
        if value is not None:
            data[key] = str(value)


def _edit_config_execution_page(state: TuiState, data: dict[str, Any]) -> None:
    form = {
        "execution_mode": str(data.get("execution_mode") or "docker"),
        "docker_image": str(data.get("docker_image") or "rnaseq-workflow:tools"),
        "max_workers": int(data.get("max_workers") or DEFAULT_TUI_CONCURRENCY),
        "fastqc_threads": int(data.get("fastqc_threads") or 2),
        "trim_galore_cores": int(data.get("trim_galore_cores") or 1),
        "hisat2_threads": int(data.get("hisat2_threads") or 4),
        "samtools_threads": int(data.get("samtools_threads") or 2),
        "featurecounts_threads": int(data.get("featurecounts_threads") or 2),
    }
    fields = [
        ("execution_mode", "执行模式", "Docker 使用容器工具；Local 使用本机工具。"),
        ("docker_image", "Docker 镜像", "仅 Docker 模式使用。"),
        ("max_workers", "样本并发数", "同时处理的样本数。"),
        ("fastqc_threads", "FastQC 线程数", "单个 FastQC 任务使用的线程数。"),
        ("trim_galore_cores", "Trim Galore cores", "单个样本修剪时使用的核心数。"),
        ("hisat2_threads", "HISAT2 线程数", "单个样本比对时使用的线程数。"),
        ("samtools_threads", "Samtools 线程数", "BAM 排序和索引使用的线程数。"),
        ("featurecounts_threads", "featureCounts 线程数", "定量计数使用的线程数。"),
    ]
    index = 0
    while True:
        key, title, help_text = fields[index]
        actions = [("edit", "编辑")]
        if index > 0:
            actions.append(("prev", "上一步"))
        if index < len(fields) - 1:
            actions.append(("next", "下一步"))
        actions.append(("save", "保存"))
        actions.append(("back", "返回"))
        choice = _menu(
            f"执行环境与并发 {index + 1}/{len(fields)}",
            f"{title}\n当前: {_format_execution_config_value(key, form.get(key))}\n\n{help_text}",
            actions,
        )
        if choice in (None, "back"):
            return
        if choice == "prev":
            index -= 1
            continue
        if choice == "next":
            if int(form.get(key) or 0) < 1 and key != "execution_mode":
                _message("需要补充", f"{title} 必须大于 0。")
                continue
            index += 1
            continue
        if choice == "save":
            if int(form.get("max_workers") or 0) < 1:
                _message("无法保存", "样本并发数必须大于 0。")
                continue
            data.update(form)
            _write_config_data(state.config, data)
            _message("已保存", "执行环境与并发配置已更新。")
            return
        if choice == "edit":
            _edit_execution_config_field(key, form)


def _format_execution_config_value(key: str, value: Any) -> str:
    if not value:
        return "未设置"
    return str(value)


def _edit_execution_config_field(key: str, data: dict[str, Any]) -> None:
    if key == "execution_mode":
        value = _execution_mode_input(str(data.get(key) or "docker"))
        if value is not None:
            data[key] = value
    elif key == "docker_image":
        value = _docker_image_input(str(data.get(key) or "rnaseq-workflow:tools"))
        if value is not None:
            data[key] = value
    else:
        defaults = {
            "max_workers": DEFAULT_TUI_CONCURRENCY,
            "fastqc_threads": 2,
            "trim_galore_cores": 1,
            "hisat2_threads": 4,
            "samtools_threads": 2,
            "featurecounts_threads": 2,
        }
        value = _int_input(title=key, default=int(data.get(key) or defaults[key]), minimum=1, cancel_returns_default=False)
        if value is not None:
            data[key] = int(value)


def _edit_config_reference_page(state: TuiState, data: dict[str, Any]) -> None:
    reference_choice = _choose_reference_asset(state)
    if reference_choice:
        reference_dir, reference_id = reference_choice
        data["reference_id"] = reference_id
        data["reference_dir"] = str(reference_dir)
        try:
            asset = load_reference(reference_id, reference_dir)
            data.update(reference_config_values(asset))
        except FileNotFoundError:
            pass
    form = {
        "featurecounts_feature_type": str(data.get("featurecounts_feature_type") or "exon"),
        "featurecounts_attribute_type": str(data.get("featurecounts_attribute_type") or "gene_id"),
        "featurecounts_strandness": int(data.get("featurecounts_strandness") or 0),
    }
    fields = [
        ("featurecounts_feature_type", "featureCounts 特征类型", "GTF 常用 exon；GFF 可按注释选择 gene。"),
        ("featurecounts_attribute_type", "featureCounts 属性字段", "GTF 常用 gene_id；部分 GFF 使用 gene 或 ID。"),
        ("featurecounts_strandness", "链特异性", "0 非链特异，1 正向，2 反向。"),
    ]
    index = 0
    while True:
        key, title, help_text = fields[index]
        actions = [("edit", "编辑")]
        if index > 0:
            actions.append(("prev", "上一步"))
        if index < len(fields) - 1:
            actions.append(("next", "下一步"))
        actions.append(("save", "保存"))
        actions.append(("back", "返回"))
        choice = _menu(
            f"Reference 与注释 {index + 1}/{len(fields)}",
            f"{title}\n当前: {_format_reference_config_value(key, form.get(key))}\n\n{help_text}",
            actions,
        )
        if choice in (None, "back"):
            return
        if choice == "prev":
            index -= 1
            continue
        if choice == "next":
            index += 1
            continue
        if choice == "save":
            if not data.get("reference_id"):
                _message("无法保存", "请先选择 reference。")
                continue
            data.update(form)
            _write_config_data(state.config, data)
            _message("已保存", "Reference 与注释配置已更新。")
            return
        if choice == "edit":
            _edit_reference_config_field(key, form)


def _format_reference_config_value(key: str, value: Any) -> str:
    if not value and value != 0:
        return "未设置"
    if key == "featurecounts_strandness":
        return {0: "unstranded / 非链特异", 1: "forward / 正向链特异", 2: "reverse / 反向链特异"}.get(int(value), str(value))
    return str(value)


def _edit_reference_config_field(key: str, data: dict[str, Any]) -> None:
    if key == "featurecounts_feature_type":
        value = _choice_with_custom_input(
            "featureCounts 特征类型",
            "GTF 常用 exon；GFF 可按注释选择 gene。",
            [("exon", "exon"), ("gene", "gene")],
            "输入自定义特征类型，例如 CDS、transcript 或 exon。",
            str(data.get(key) or "exon"),
        )
        if value is not None:
            data[key] = value
    elif key == "featurecounts_attribute_type":
        value = _choice_with_custom_input(
            "featureCounts 属性字段",
            "GTF 常用 gene_id；部分 GFF 使用 gene 或 ID。",
            [("gene_id", "gene_id"), ("gene", "gene"), ("ID", "ID")],
            "输入自定义属性字段，例如 Parent 或 Name。",
            str(data.get(key) or "gene_id"),
        )
        if value is not None:
            data[key] = value
    elif key == "featurecounts_strandness":
        value = _menu(
            "链特异性",
            "0 非链特异，1 正向，2 反向。选错会明显影响计数。",
            [("0", "unstranded / 非链特异"), ("1", "forward / 正向链特异"), ("2", "reverse / 反向链特异"), ("back", "返回")],
        )
        if value in {"0", "1", "2"}:
            data[key] = int(value)


def _edit_config_samples_page(state: TuiState, data: dict[str, Any]) -> None:
    raw = _multiline_input(
        "样本表",
        "粘贴 YAML/JSON 样本列表。每个样本至少包含 sample_id 和 source_path。",
        yaml.safe_dump(data.get("samples") or [{"sample_id": "S1", "source_path": "data/S1.fastq.gz", "layout": "single"}], allow_unicode=True, sort_keys=False),
    )
    if raw is None:
        return
    try:
        samples = yaml.safe_load(raw) or []
    except yaml.YAMLError as exc:
        _message("样本表错误", str(exc))
        return
    if not isinstance(samples, list):
        _message("样本表错误", "样本表必须是列表。")
        return
    data["samples"] = samples
    _write_config_data(state.config, data)
    _message("已保存", f"已保存 {len(samples)} 个样本。")


def _reference_menu(state: TuiState) -> None:
    while True:
        choice = _menu(
            "参考基因组",
            "管理参考基因组、注释和 HISAT2 index。",
            [
                ("prepare", "一条龙下载 FASTA+GTF 并构建 index"),
                ("list", "浏览 reference"),
                ("register", "登记本地 FASTA/GTF"),
                ("build", "构建 HISAT2 index"),
                ("check", "检查 reference 资产"),
                ("use", "写入当前 config"),
                ("cleanup", "清理失效 reference 记录"),
                ("back", "返回"),
            ],
        )
        if choice in (None, "back"):
            return
        reference_dir = _select_reference_scope_dir(state, for_write=choice in {"prepare", "register", "build"})
        if not reference_dir:
            continue
        if choice == "list":
            cleanup_stale_reference_records(reference_dir, state.workspace.database_path)
            _browse_references(reference_dir)
        elif choice == "register":
            _register_reference(reference_dir, state)
        elif choice == "build":
            _build_reference_index(reference_dir, state)
        elif choice == "check":
            _check_reference(reference_dir)
            cleanup_stale_reference_records(reference_dir, state.workspace.database_path)
        elif choice == "use":
            _use_reference(reference_dir, state.config)
        elif choice == "prepare":
            _prepare_reference(reference_dir, state.config, state=state)
        elif choice == "cleanup":
            removed = cleanup_stale_reference_records(reference_dir, state.workspace.database_path)
            _message("清理完成", "未找到失效记录。" if not removed else "已移除:\n" + "\n".join(removed))


def _select_reference_scope_dir(state: TuiState, for_write: bool = False) -> Path | None:
    state.workspace.ensure()
    if not state.user_id:
        return state.workspace.global_reference_dir
    selected = _menu(
        "资产库",
        "选择要使用的资产库。",
        [("mine", "我的资产"), ("shared", "公共资产"), ("back", "返回")],
    )
    if selected in (None, "back"):
        return None
    if selected == "mine":
        user = state.workspace.user(state.user_id)
        user.ensure()
        return user.user_reference_dir
    return state.workspace.global_reference_dir


def _reference_download_cache_dir(reference_dir: Path) -> Path:
    parts = reference_dir.parts
    if "users" in parts:
        return reference_dir.parent / "reference_downloads"
    if reference_dir.name == "references":
        return reference_dir.parent / "reference_downloads"
    return Path("workspace") / "shared" / "reference_downloads"


def _docker_workspace_for_asset_dir(reference_dir: Path) -> Path:
    parts = reference_dir.resolve().parts
    if "workspace" in parts:
        idx = parts.index("workspace")
        return Path(*parts[: idx + 1])
    return Path(".")


def _ensembl_division_input() -> str | None:
    return _choice_with_custom_input(
        "Ensembl 分库",
        "植物参考选择 plants；人、鼠等脊椎动物选择 vertebrates。其他分库可自定义输入。",
        [("plants", "plants"), ("vertebrates", "vertebrates")],
        "可填 fungi、metazoa、protists 等。返回后仍停留在分库选择。",
    )


def _choice_with_custom_input(
    title: str,
    text: str,
    options: list[tuple[str, str]],
    custom_prompt: str,
    current_value: str = "",
) -> str | None:
    custom = current_value.strip()
    while True:
        custom_label = f"自定义: {custom}" if custom else "自定义"
        selected = _menu(title, text, options + [("__custom__", custom_label), ("back", "返回")])
        if selected in (None, "back"):
            return None
        if selected != "__custom__":
            return selected
        raw = _input(title, custom_prompt, custom)
        if raw is None:
            continue
        custom = raw.strip()
        if custom:
            return custom


def _execution_mode_input(default: str = "docker") -> str | None:
    values = [("docker", "docker"), ("local", "local"), ("back", "返回")]
    selected = _menu(
        "execution_mode",
        "Docker 更稳定；Local 使用本机已安装工具。",
        values,
    )
    if selected in (None, "back"):
        return None
    return selected


def _docker_image_input(default: str = "rnaseq-workflow:tools") -> str | None:
    return _choice_with_custom_input(
        "Docker 镜像",
        "选择工具镜像。默认镜像适合标准流程。",
        [(default, default)],
        "输入自定义 Docker 镜像名。",
        default,
    )


def _prepare_reference(reference_dir: Path, config: Path, state: TuiState | None = None) -> None:
    form = _prepare_reference_wizard()
    if not form:
        return
    reference_id = str(form["reference_id"])
    mode = str(form["source_mode"])
    if mode == "ensembl":
        try:
            fasta_url, annotation_url = build_ensembl_reference_urls(
                str(form["species"]),
                division=str(form["division"]),
                release=str(form["release"]),
            )
        except (FileNotFoundError, ValueError) as exc:
            _message("查找失败", str(exc))
            return
    else:
        fasta_url = str(form["fasta_url"])
        annotation_url = str(form["annotation_url"])
    provider = str(form["provider"])
    annotation_provider = str(form["annotation_provider"])
    download_dir = _reference_download_cache_dir(reference_dir)
    docker_workspace = _docker_workspace_for_asset_dir(reference_dir)
    context = RunContext(
        project_id=f"reference_{reference_id}",
        work_dir=Path.cwd(),
        output_dir=reference_dir / reference_id,
        config={
            "execution_mode": str(form["execution_mode"]),
            "docker_image": str(form["docker_image"]),
            "docker_workspace": str(docker_workspace),
        },
        dry_run=not bool(form["actual_run"]),
    )
    try:
        prepared = _run_reference_task_with_tui_progress(
            title=f"准备 Reference: {reference_id}",
            reference_id=reference_id,
            reference_dir=reference_dir,
            download_dir=download_dir,
            dry_run=context.dry_run,
            worker=lambda: prepare_reference_from_urls(
                reference_id,
                fasta_url,
                annotation_url,
                reference_dir,
                download_dir,
                context=context,
                threads=int(form["threads"]),
                build_index=bool(form["build_index"]),
                force=bool(form["force"]),
                provider=provider,
                annotation_provider=annotation_provider,
                species=str(form["species"] or "") or None,
                release=str(form["release"] or "") or None,
                created_by=state.username if state and state.username else "download",
            ),
        )
        if _confirm_yes(f"写入当前 config: {config}", True):
            for key, value in reference_config_values(prepared.asset).items():
                set_config_value(config, key, value)
        if state and state.task:
            _set_task_reference(state.task, state, prepared.asset)
        _message("完成", f"Reference 已准备好:\n{prepared.asset.root}")
    except (FileExistsError, FileNotFoundError, RuntimeError, ValueError) as exc:
        _message("错误", str(exc))


def _prepare_reference_wizard() -> dict[str, Any] | None:
    data: dict[str, Any] = {
        "reference_id": "",
        "source_mode": "ensembl",
        "species": "glycine_max",
        "division": "plants",
        "release": "current",
        "fasta_url": "",
        "annotation_url": "",
        "provider": "ensembl",
        "annotation_provider": "ensembl",
        "execution_mode": "docker",
        "docker_image": "rnaseq-workflow:tools",
        "build_index": True,
        "actual_run": False,
        "threads": 4,
        "force": False,
    }
    index = 0
    while True:
        fields = _prepare_reference_fields(data)
        index = max(0, min(index, len(fields) - 1))
        key, title, help_text = fields[index]
        actions = [("edit", "编辑")]
        if index > 0:
            actions.append(("prev", "上一步"))
        if index < len(fields) - 1:
            actions.append(("next", "下一步"))
        actions.append(("save", "开始准备"))
        actions.append(("back", "返回"))
        choice = _menu(
            f"准备 Reference {index + 1}/{len(fields)}",
            f"{title}\n当前: {_format_prepare_reference_value(key, data.get(key))}\n\n{help_text}",
            actions,
        )
        if choice in (None, "back"):
            return None
        if choice == "prev":
            index -= 1
            continue
        if choice == "next":
            valid, message = _validate_prepare_reference_field(key, data)
            if not valid:
                _message("需要补充", message)
                continue
            index += 1
            continue
        if choice == "save":
            valid, message = _validate_prepare_reference(data)
            if not valid:
                _message("无法开始", message)
                continue
            return data
        if choice == "edit":
            _edit_prepare_reference_field(key, data)


def _prepare_reference_fields(data: dict[str, Any]) -> list[tuple[str, str, str]]:
    fields = [
        ("reference_id", "Reference 名称", "为这套参考资产命名。建议包含物种、来源和版本。"),
        ("source_mode", "获取方式", "选择从 Ensembl 自动定位文件，或直接提供 FASTA 与 GTF/GFF URL。"),
    ]
    if data.get("source_mode") == "ensembl":
        fields.extend(
            [
                ("species", "物种名称", "使用 Ensembl 接受的物种名。大豆为 glycine_max。"),
                ("division", "Ensembl 分库", "大豆等植物选择 plants；人、鼠等选择 vertebrates。"),
                ("release", "Ensembl 版本", "current 使用当前发布版；需要复现时可指定版本号。"),
            ]
        )
    else:
        fields.extend(
            [
                ("fasta_url", "Genome FASTA URL", "基因组 FASTA 下载地址。支持 http、https 或 ftp。"),
                ("annotation_url", "GTF/GFF URL", "注释文件下载地址。应与 FASTA 属于同一来源和版本。"),
            ]
        )
    fields.extend(
        [
            ("provider", "参考来源", "记录 FASTA 来源。用于追踪资产，不影响命令执行。"),
            ("annotation_provider", "注释来源", "通常与参考来源一致。混用不同来源可能导致基因 ID 不匹配。"),
            ("execution_mode", "执行方式", "Docker 使用容器工具；Local 使用本机工具。"),
            ("docker_image", "Docker 镜像", "仅 Docker 模式使用。默认镜像包含 HISAT2 等工具。"),
            ("build_index", "构建 HISAT2 index", "开启后准备完成即构建比对索引。后续 HISAT2 需要该索引。"),
            ("actual_run", "实际执行", "关闭时只做 dry-run，用于检查命令和路径。"),
            ("threads", "hisat2-build 线程数", "构建索引使用的线程数。大基因组会占用较多内存。"),
            ("force", "覆盖已有文件", "同名资产或索引已存在时是否重建。默认保留已有结果。"),
        ]
    )
    return fields


def _format_prepare_reference_value(key: str, value: Any) -> str:
    if key in {"build_index", "actual_run", "force"}:
        return "是" if value else "否"
    if value is None or str(value).strip() == "":
        return "未设置"
    return str(value)


def _edit_prepare_reference_field(key: str, data: dict[str, Any]) -> None:
    if key == "reference_id":
        value = _input("Reference 名称", "建议包含物种、来源和版本，例如 glycine_max_ensembl_current。", str(data[key]))
        if value is not None:
            data[key] = value.strip()
    elif key == "source_mode":
        value = _menu(
            "获取方式",
            "Ensembl 会自动选择匹配的 FASTA 和 GTF；URL 适合自有或非 Ensembl 来源。",
            [("ensembl", "Ensembl 自动获取"), ("url", "自定义 URL"), ("back", "返回")],
        )
        if value in {"ensembl", "url"}:
            data[key] = value
            if value == "ensembl" and data.get("provider") == "custom":
                data["provider"] = "ensembl"
                data["annotation_provider"] = "ensembl"
            if value == "url" and data.get("provider") == "ensembl":
                data["provider"] = "custom"
                data["annotation_provider"] = "custom"
    elif key == "species":
        value = _choice_with_custom_input(
            "物种名称",
            "选择常用物种，或输入来源数据库接受的物种名。",
            [("glycine_max", "glycine_max（大豆）"), ("arabidopsis_thaliana", "arabidopsis_thaliana"), ("homo_sapiens", "homo_sapiens"), ("mus_musculus", "mus_musculus")],
            "输入物种名，例如 glycine_max。返回后仍停留在当前步骤。",
            str(data.get(key) or ""),
        )
        if value is not None:
            data[key] = value
    elif key == "division":
        value = _ensembl_division_input()
        if value is not None:
            data[key] = value
    elif key == "release":
        value = _choice_with_custom_input(
            "Ensembl 版本",
            "current 使用当前发布版；指定版本有利于复现。",
            [("current", "current")],
            "输入版本号，例如 110。返回后仍停留在当前步骤。",
            str(data.get(key) or "current"),
        )
        if value is not None:
            data[key] = value
    elif key in {"fasta_url", "annotation_url"}:
        title = "Genome FASTA URL" if key == "fasta_url" else "GTF/GFF URL"
        value = _input(title, "仅支持 http、https 或 ftp。", str(data.get(key) or ""))
        if value is not None:
            data[key] = value.strip()
    elif key == "provider":
        value = _choice_with_custom_input(
            "参考来源",
            "记录 FASTA 来源。用于追踪资产，不影响命令执行。",
            [("ensembl", "ensembl"), ("refseq", "refseq"), ("custom", "custom")],
            "输入自定义来源名。返回后仍停留在当前步骤。",
            str(data.get(key) or "custom"),
        )
        if value is not None:
            data[key] = value
            if data.get("annotation_provider") in {"", "same"}:
                data["annotation_provider"] = value
    elif key == "annotation_provider":
        provider = str(data.get("provider") or "custom")
        value = _choice_with_custom_input(
            "注释来源",
            "通常与参考来源一致。混用来源时需确认基因 ID 体系一致。",
            [("same", f"同参考来源: {provider}"), ("ensembl", "ensembl"), ("refseq", "refseq"), ("custom", "custom")],
            "输入自定义注释来源名。返回后仍停留在当前步骤。",
            provider,
        )
        if value is not None:
            data[key] = provider if value == "same" else value
    elif key == "execution_mode":
        value = _execution_mode_input(str(data.get(key) or "docker"))
        if value is not None:
            data[key] = value
    elif key == "docker_image":
        value = _docker_image_input(str(data.get(key) or "rnaseq-workflow:tools"))
        if value is not None:
            data[key] = value
    elif key in {"build_index", "actual_run", "force"}:
        value = _optional_yes_no(_prepare_reference_bool_title(key), bool(data.get(key)))
        if value is not None:
            data[key] = bool(value)
    elif key == "threads":
        value = _int_input("hisat2-build 线程数", int(data.get(key) or 4), minimum=1, cancel_returns_default=False)
        if value is not None:
            data[key] = value


def _prepare_reference_bool_title(key: str) -> str:
    return {
        "build_index": "构建 HISAT2 index",
        "actual_run": "实际执行",
        "force": "覆盖已有文件",
    }.get(key, key)


def _optional_yes_no(title: str, default: bool) -> bool | None:
    try:
        return _yes_no(title, default, cancel_returns_default=False)
    except TypeError:
        return _yes_no(title, default)


def _validate_prepare_reference_field(key: str, data: dict[str, Any]) -> tuple[bool, str]:
    if key in {"reference_id", "species", "division", "release", "fasta_url", "annotation_url"}:
        if not str(data.get(key) or "").strip():
            return False, f"请先填写 {_prepare_reference_field_label(key)}。"
    if key in {"fasta_url", "annotation_url"}:
        return _validate_reference_url(str(data.get(key) or ""), _prepare_reference_field_label(key))
    return True, ""


def _validate_prepare_reference(data: dict[str, Any]) -> tuple[bool, str]:
    for key, _title, _help in _prepare_reference_fields(data):
        valid, message = _validate_prepare_reference_field(key, data)
        if not valid:
            return valid, message
    if int(data.get("threads") or 0) < 1:
        return False, "hisat2-build 线程数必须大于 0。"
    return True, ""


def _prepare_reference_field_label(key: str) -> str:
    return {
        "reference_id": "Reference 名称",
        "species": "物种名称",
        "division": "Ensembl 分库",
        "release": "Ensembl 版本",
        "fasta_url": "Genome FASTA URL",
        "annotation_url": "GTF/GFF URL",
    }.get(key, key)


def _validate_reference_url(url: str, label: str) -> tuple[bool, str]:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https", "ftp"} or not parsed.netloc:
        return False, f"{label} 需要使用 http、https 或 ftp URL。"
    lowered = parsed.path.lower()
    allowed = (".fa", ".fasta", ".fna", ".fa.gz", ".fasta.gz", ".fna.gz", ".gtf", ".gff", ".gff3", ".gtf.gz", ".gff.gz", ".gff3.gz")
    if not lowered.endswith(allowed):
        return False, f"{label} 文件后缀不受支持。"
    return True, ""


def _register_reference(reference_dir: Path, state: TuiState | None = None) -> None:
    form = _register_reference_wizard()
    if not form:
        return
    try:
        asset = register_reference(
            form["reference_id"],
            fasta=form["fasta"],
            annotation=form["annotation"],
            hisat2_index=form["hisat2_index"],
            reference_dir=reference_dir,
            overwrite=bool(form["overwrite"]),
            provider=str(form["provider"]),
            annotation_provider=str(form["annotation_provider"]),
            created_by=state.username if state and state.username else "manual",
            notes=str(form["description"]),
        )
        if state and state.user_id and state.task:
            _set_task_reference(state.task, state, asset)
        _message("完成", f"已登记 {asset.reference_id}\n{asset.root}")
    except (FileExistsError, FileNotFoundError, ValueError) as exc:
        _message("错误", str(exc))


def _register_reference_wizard() -> dict[str, Any] | None:
    data: dict[str, Any] = {
        "reference_id": "",
        "fasta": None,
        "annotation": None,
        "hisat2_index": None,
        "provider": "custom",
        "annotation_provider": "custom",
        "description": "",
        "overwrite": False,
    }
    fields = [
        ("reference_id", "Reference 名称", "给这套参考资产起一个稳定名称，例如 glycine_max_ensembl_current。"),
        ("fasta", "Genome FASTA", "HISAT2 建索引使用的基因组 FASTA 文件，必须存在。"),
        ("annotation", "GTF/GFF 注释", "featureCounts/StringTie 使用的注释文件；没有可跳过，但定量会受限。"),
        ("hisat2_index", "已有 HISAT2 index", "已有 index 时填写 prefix；不是 .ht2 单文件。没有可跳过，之后可构建。"),
        ("provider", "参考来源", "记录 FASTA 来源，例如 custom、ensembl、refseq。"),
        ("annotation_provider", "注释来源", "通常与参考来源一致；混用 Ensembl 与 RefSeq 容易造成 ID 不一致。"),
        ("description", "描述", "记录物种、版本、来源或用途，方便以后复用。"),
        ("overwrite", "覆盖同名资产", "同名 reference 已存在时是否覆盖。默认不覆盖。"),
    ]
    index = 0
    while True:
        key, title, help_text = fields[index]
        value = _format_register_reference_value(key, data.get(key))
        actions = [("edit", "编辑")]
        if index > 0:
            actions.append(("prev", "上一步"))
        if index < len(fields) - 1:
            actions.append(("next", "下一步"))
        actions.append(("save", "保存登记"))
        actions.append(("back", "返回"))
        choice = _menu(
            f"登记本地 Reference {index + 1}/{len(fields)}",
            f"{title}\n当前: {value}\n\n{help_text}",
            actions,
        )
        if choice in (None, "back"):
            return None
        if choice == "prev":
            index = max(0, index - 1)
            continue
        if choice == "next":
            valid, message = _validate_register_reference_progress(data, require_all=False)
            if not valid and key in {"reference_id", "fasta"}:
                _message("需要补充", message)
                continue
            index = min(len(fields) - 1, index + 1)
            continue
        if choice == "save":
            valid, message = _validate_register_reference_progress(data, require_all=True)
            if not valid:
                _message("无法保存", message)
                continue
            return data
        if choice == "edit":
            _edit_register_reference_field(key, data)


def _format_register_reference_value(key: str, value: Any) -> str:
    if key in {"fasta", "annotation", "hisat2_index"}:
        return str(value) if value else "未设置"
    if key == "overwrite":
        return "是" if value else "否"
    return str(value) if str(value or "").strip() else "未设置"


def _edit_register_reference_field(key: str, data: dict[str, Any]) -> None:
    if key == "reference_id":
        value = _input("Reference 名称", "只用于识别资产；建议包含物种、来源和版本。", str(data[key]))
        if value is not None:
            data[key] = value.strip()
    elif key == "fasta":
        value = _path_input("Genome FASTA", data.get(key), must_exist=True)
        if value is not None:
            data[key] = value
    elif key == "annotation":
        value = _path_input("GTF/GFF 注释", data.get(key), must_exist=True)
        if value is not None:
            data[key] = value
    elif key == "hisat2_index":
        value = _path_input("HISAT2 index prefix", data.get(key), must_exist=False)
        if value is not None:
            data[key] = value
    elif key == "provider":
        value = _choice_with_custom_input(
            "参考来源",
            "记录 FASTA 来源。",
            [("custom", "custom"), ("ensembl", "ensembl"), ("refseq", "refseq")],
            "输入自定义来源名。返回后仍停留在当前步骤。",
            str(data.get(key) or "custom"),
        )
        if value is not None:
            data[key] = value
            if data.get("annotation_provider") in {"", "same"}:
                data["annotation_provider"] = value
    elif key == "annotation_provider":
        provider = str(data.get("provider") or "custom")
        value = _choice_with_custom_input(
            "注释来源",
            "通常与参考来源一致。",
            [("same", f"同参考来源: {provider}"), ("ensembl", "ensembl"), ("refseq", "refseq"), ("custom", "custom")],
            "输入自定义注释来源名。返回后仍停留在当前步骤。",
            provider,
        )
        if value is not None:
            data[key] = provider if value == "same" else value
    elif key == "description":
        value = _input("描述", "简短记录物种、版本、来源或用途。", str(data.get(key) or ""))
        if value is not None:
            data[key] = value.strip()
    elif key == "overwrite":
        value = _yes_no("覆盖同名资产", bool(data.get(key)), cancel_returns_default=False)
        if value is not None:
            data[key] = bool(value)


def _validate_register_reference_progress(data: dict[str, Any], require_all: bool) -> tuple[bool, str]:
    if not str(data.get("reference_id") or "").strip():
        return False, "请先填写 Reference 名称。"
    fasta = data.get("fasta")
    if not fasta:
        return False, "请先选择 Genome FASTA 文件。"
    if require_all and not Path(fasta).exists():
        return False, f"FASTA 文件不存在：{fasta}"
    annotation = data.get("annotation")
    if require_all and annotation and not Path(annotation).exists():
        return False, f"注释文件不存在：{annotation}"
    return True, ""


def _build_reference_index(reference_dir: Path, state: TuiState) -> None:
    selected = _browse_references(reference_dir, select_mode=True)
    if not selected:
        return
    reference_id = selected.reference_id
    form = _build_reference_index_wizard()
    if not form:
        return
    docker_workspace = _docker_workspace_for_asset_dir(reference_dir)
    context = RunContext(
        project_id=f"reference_{reference_id}",
        work_dir=Path.cwd(),
        output_dir=reference_dir / reference_id,
        config={
            "execution_mode": str(form["execution_mode"]),
            "docker_image": str(form["docker_image"]),
            "docker_workspace": str(docker_workspace),
        },
        dry_run=not bool(form["actual_run"]),
    )
    try:
        asset, result = _run_reference_task_with_tui_progress(
            title=f"HISAT2 index: {reference_id}",
            reference_id=reference_id,
            reference_dir=reference_dir,
            download_dir=None,
            dry_run=context.dry_run,
            worker=lambda: build_hisat2_index_for_reference(
                reference_id,
                reference_dir,
                context,
                threads=int(form["threads"]),
                force=bool(form["force"]),
            ),
        )
        _capture_output(state, lambda console: console.print(" ".join(result.command)), "HISAT2 build command")
        _message("完成" if result.ok else "失败", f"{asset.reference_id}\nreturn_code={result.return_code}")
        if state.task and state.task.read_metadata() and state.task.read_metadata().reference_id == asset.reference_id:
            _set_task_reference(state.task, state, asset)
    except (FileExistsError, FileNotFoundError) as exc:
        _message("错误", str(exc))


def _build_reference_index_wizard() -> dict[str, Any] | None:
    data: dict[str, Any] = {
        "execution_mode": "docker",
        "docker_image": "rnaseq-workflow:tools",
        "actual_run": False,
        "threads": 4,
        "force": False,
    }
    fields = [
        ("execution_mode", "执行方式", "Docker 使用容器工具；Local 使用本机工具。"),
        ("docker_image", "Docker 镜像", "仅 Docker 模式使用。默认镜像包含 hisat2-build。"),
        ("actual_run", "实际执行", "关闭时只做 dry-run，用于检查命令和路径。"),
        ("threads", "hisat2-build 线程数", "构建索引使用的线程数。大基因组会占用较多内存。"),
        ("force", "强制重建", "索引已存在时是否覆盖重建。默认保留已有 index。"),
    ]
    index = 0
    while True:
        key, title, help_text = fields[index]
        actions = [("edit", "编辑")]
        if index > 0:
            actions.append(("prev", "上一步"))
        if index < len(fields) - 1:
            actions.append(("next", "下一步"))
        actions.append(("save", "开始构建"))
        actions.append(("back", "返回"))
        choice = _menu(
            f"构建 HISAT2 index {index + 1}/{len(fields)}",
            f"{title}\n当前: {_format_prepare_reference_value(key, data.get(key))}\n\n{help_text}",
            actions,
        )
        if choice in (None, "back"):
            return None
        if choice == "prev":
            index -= 1
        elif choice == "next":
            index += 1
        elif choice == "save":
            if int(data.get("threads") or 0) < 1:
                _message("无法开始", "hisat2-build 线程数必须大于 0。")
                continue
            return data
        elif choice == "edit":
            _edit_build_reference_index_field(key, data)


def _edit_build_reference_index_field(key: str, data: dict[str, Any]) -> None:
    if key == "execution_mode":
        value = _execution_mode_input(str(data.get(key) or "docker"))
        if value is not None:
            data[key] = value
    elif key == "docker_image":
        value = _docker_image_input(str(data.get(key) or "rnaseq-workflow:tools"))
        if value is not None:
            data[key] = value
    elif key in {"actual_run", "force"}:
        value = _optional_yes_no("实际执行" if key == "actual_run" else "强制重建", bool(data.get(key)))
        if value is not None:
            data[key] = bool(value)
    elif key == "threads":
        value = _int_input("hisat2-build 线程数", int(data.get(key) or 4), minimum=1, cancel_returns_default=False)
        if value is not None:
            data[key] = value


def _check_reference(reference_dir: Path) -> None:
    selected = _browse_references(reference_dir, select_mode=True)
    if not selected:
        return
    asset = selected
    report = check_reference_asset(asset)
    lines = [f"{issue.level.upper()}: {issue.field} - {issue.message}" for issue in report.issues]
    _message("Reference 检查", "\n".join(lines) if lines else "all checks passed")


def _run_reference_task_with_tui_progress(
    title: str,
    reference_id: str,
    reference_dir: Path,
    download_dir: Path | None,
    dry_run: bool,
    worker: Callable[[], object],
):
    status_area = TextArea(
        text=_reference_progress_text(title, reference_id, reference_dir, download_dir, dry_run, "RUNNING", 0.0),
        read_only=True,
        scrollbar=True,
        focusable=False,
        wrap_lines=False,
    )
    kb = KeyBindings()
    result_holder = {"result": None, "error": None, "done": False}
    started_at = time.monotonic()

    @kb.add("q")
    def _quit_if_done(event) -> None:
        if result_holder["done"]:
            event.app.exit()

    def run_worker() -> None:
        try:
            result_holder["result"] = worker()
        except BaseException as exc:
            result_holder["error"] = exc
        finally:
            result_holder["done"] = True

    app = Application(
        layout=Layout(
            Box(
                Frame(
                    HSplit([status_area, Label(text=""), Label(text="任务完成后按 q 返回。")]),
                    title=title,
                ),
                padding=1,
            )
        ),
        key_bindings=kb,
        style=STYLE,
        full_screen=True,
    )

    def refresher() -> None:
        while not result_holder["done"]:
            status_area.text = _reference_progress_text(
                title,
                reference_id,
                reference_dir,
                download_dir,
                dry_run,
                "RUNNING",
                time.monotonic() - started_at,
            )
            app.invalidate()
            time.sleep(0.5)
        final_status = "FAILED" if result_holder["error"] else "COMPLETED"
        status_area.text = _reference_progress_text(
            title,
            reference_id,
            reference_dir,
            download_dir,
            dry_run,
            final_status,
            time.monotonic() - started_at,
            result_holder["error"],
        )
        app.invalidate()

    threading.Thread(target=run_worker, daemon=True).start()
    threading.Thread(target=refresher, daemon=True).start()
    app.run()
    if result_holder["error"]:
        raise result_holder["error"]
    return result_holder["result"]


def _reference_progress_text(
    title: str,
    reference_id: str,
    reference_dir: Path,
    download_dir: Path | None,
    dry_run: bool,
    status: str,
    elapsed: float,
    error: BaseException | None = None,
) -> str:
    ref_root = reference_dir / reference_id
    lines = [
        title,
        f"状态: {status}",
        f"模式: {'dry-run' if dry_run else '实际运行'}",
        f"reference_id: {reference_id}",
        f"reference_dir: {reference_dir}",
        f"reference_root: {ref_root}",
        f"elapsed: {elapsed:.0f}s",
        "",
        "文件活动:",
        f"  download_cache: {_reference_dir_activity(download_dir / reference_id) if download_dir else '(none)'}",
        f"  reference_root:  {_reference_dir_activity(ref_root)}",
        f"  hisat2_index:    {_reference_index_activity(ref_root / 'hisat2' / 'genome')}",
    ]
    metadata = ref_root / "reference.json"
    if metadata.exists():
        lines.append(f"  metadata:        {metadata}")
    if error:
        lines.extend(["", f"错误: {error}"])
    if status != "RUNNING":
        lines.extend(["", "任务已结束，按 q 返回。"])
    return "\n".join(lines)


def _reference_dir_activity(path: Path) -> str:
    if not path.exists():
        return "(waiting)"
    files = [item for item in path.rglob("*") if item.is_file()]
    if not files:
        return "0 files"
    size = sum(item.stat().st_size for item in files)
    latest = max(files, key=lambda item: item.stat().st_mtime)
    idle = max(time.time() - latest.stat().st_mtime, 0.0)
    return f"{len(files)} files {_format_bytes(size)} idle={idle:.0f}s last={latest.name}"


def _reference_index_activity(prefix: Path) -> str:
    parent = prefix.parent
    if not parent.exists():
        return "(waiting)"
    files = sorted(parent.glob(prefix.name + ".*.ht2*"))
    if not files:
        return "(waiting)"
    size = sum(item.stat().st_size for item in files)
    latest = max(files, key=lambda item: item.stat().st_mtime)
    return f"{len(files)}/8 files {_format_bytes(size)} last={latest.name}"


def _use_reference(reference_dir: Path, config: Path) -> None:
    selected = _browse_references(reference_dir, select_mode=True)
    if not selected:
        return
    try:
        asset = selected
        report = check_reference_asset(asset)
        if not report.ok:
            _message("错误", "\n".join(f"{issue.field}: {issue.message}" for issue in report.issues))
            return
        for key, value in reference_config_values(asset).items():
            set_config_value(config, key, value)
        _message("完成", f"已写入 {config}")
    except FileNotFoundError as exc:
        _message("错误", str(exc))


def _download_menu(state: TuiState) -> None:
    form = _download_wizard(
        "下载 SRA",
        state,
        advanced=False,
    )
    if form is None:
        return
    target = str(form["target"])
    output_dir = Path(form["output_dir"])
    downloader = PrefetchDownloader(
        max_size=str(form["max_size"]),
        execution_mode=str(form["execution_mode"]),
        docker_image=str(form["docker_image"]),
        docker_workspace=Path("."),
        resume_partial=True,
    )
    downloader = AutoDownloader(sra_downloader=downloader, ena_downloader=EnaFastqDownloader())
    try:
        requests = build_smart_download_requests(target, output_dir, fetch_expected_sizes=False)
        if not _preflight_sra_metadata_for_download(requests, output_dir, state):
            return
        manager = DownloadManager(downloader=downloader, max_workers=int(form["max_workers"]))
        summary = _run_download_with_tui_progress(manager, requests, dry_run=not bool(form["actual_run"]), title=f"下载: {target}")
    except ValueError as exc:
        _message("下载目标错误", str(exc))
        return
    except KeyboardInterrupt:
        _message("下载已中断", "已保留半成品，可从“继续未完成下载”恢复。")
        return
    _capture_output(state, lambda console: print_download_results(console, summary.results, title=f"Download: {target}"), "下载结果")


def _advanced_download_menu(state: TuiState) -> None:
    form = _download_wizard(
        "高级下载",
        state,
        advanced=True,
    )
    if form is None:
        return
    target = str(form["target"])
    output_dir = Path(form["output_dir"])
    sra_downloader = PrefetchDownloader(
        max_size=str(form["max_size"]),
        force=bool(form["force"]),
        retries=int(form["retries"]),
        execution_mode=str(form["execution_mode"]),
        docker_image=str(form["docker_image"]),
        docker_workspace=Path("."),
        resume_partial=True,
    )
    source = str(form["source"])
    if source == "ena":
        downloader = EnaFastqDownloader()
    elif source == "sra":
        downloader = sra_downloader
    else:
        downloader = AutoDownloader(sra_downloader=sra_downloader, ena_downloader=EnaFastqDownloader())
    try:
        requests = build_smart_download_requests(target, output_dir, fetch_expected_sizes=False)
        if not _preflight_sra_metadata_for_download(requests, output_dir, state):
            return
        manager = DownloadManager(downloader=downloader, max_workers=int(form["max_workers"]))
        summary = _run_download_with_tui_progress(
            manager,
            requests,
            dry_run=not bool(form["actual_run"]),
            title=f"下载: {target}",
        )
    except ValueError as exc:
        _message("下载目标错误", str(exc))
        return
    except KeyboardInterrupt:
        _message("下载已中断", "已保留半成品，可从“继续未完成下载”恢复。")
        return
    _capture_output(state, lambda console: print_download_results(console, summary.results, title=f"Download: {target}"), "下载结果")


def _download_wizard(title: str, state: TuiState, advanced: bool) -> dict[str, Any] | None:
    defaults: dict[str, Any] = {
        "target": "",
        "output_dir": state.task.downloads_dir if state.task else Path("downloads"),
        "max_size": "5G",
        "source": "auto",
        "execution_mode": "docker",
        "docker_image": "rnaseq-workflow:tools",
        "max_workers": DEFAULT_TUI_CONCURRENCY,
        "actual_run": True,
    }
    fields: list[tuple[str, str, str, str, int | None, tuple[tuple[str, str], ...]]] = [
        ("target", "下载目标", "输入 SRA accession，或 TXT/CSV/JSON 清单路径。多个编号可用逗号或空格分隔。", "str", None, ()),
        ("output_dir", "输出目录", "下载结果会写入当前任务 downloads 目录，或这里指定的目录。", "path", None, ()),
        ("max_size", "下载大小上限", "SRA Toolkit 的 max-size 参数。样本较大时可设为 20G 或 50G。", "str", None, ()),
        ("source", "下载来源", "Auto 会优先选择可用来源。ENA 下载 FASTQ；SRA 下载 .sra 后再转换。", "choice", None, (("auto", "自动"), ("ena", "ENA FASTQ"), ("sra", "SRA Toolkit"))),
        ("execution_mode", "执行方式", "Docker 使用容器工具；Local 使用本机工具。", "choice", None, (("docker", "Docker"), ("local", "Local"))),
        ("docker_image", "Docker 镜像", "仅 Docker 模式使用。", "str", None, ()),
        ("max_workers", "下载并发数", _friendly_field("清单并发数")[1], "int", 1, ()),
        ("actual_run", "实际下载", "关闭时只做 dry-run。", "bool", None, ()),
    ]
    if advanced:
        defaults.update({"force": False, "retries": 0})
        fields.insert(5, ("force", "force 重下", "开启后会重新下载已存在的目标。", "bool", None, ()))
        fields.insert(6, ("retries", "失败重试次数", _friendly_field("失败重试次数")[1], "int", 0, ()))
    return _tool_run_wizard(title, defaults, fields)


def _metadata_menu(state: TuiState) -> None:
    form = _tool_run_wizard(
        "SRA 元数据",
        {
            "target": "",
            "output_dir": state.task.downloads_dir if state.task else Path("downloads"),
        },
        [
            ("target", "SRA 编号", "输入一个或多个 SRR、ERR 或 DRR 编号。", "str", None, ()),
            ("output_dir", "元数据输出目录", "RunInfo sidecar 会写入这里。", "path", None, ()),
        ],
    )
    if form is None:
        return
    target = str(form["target"])
    output_dir = Path(form["output_dir"])
    accessions = split_sra_targets(target)
    if not accessions and looks_like_sra_accession(target):
        accessions = [target.strip().upper()]
    if not accessions:
        _message("输入错误", "请输入一个或多个 SRA run accession。")
        return
    try:
        metadata = fetch_sra_metadata(accessions)
    except OSError as exc:
        _message("元数据获取失败", str(exc))
        return
    written = write_sra_metadata_sidecars(metadata, output_dir)
    _capture_output(
        state,
        lambda console: _print_sra_metadata_report(console, metadata, written),
        "SRA 元数据分组",
    )


def _print_sra_metadata_report(console: Console, metadata, written: list[Path]) -> None:
    table = Table(title="SRA Metadata")
    table.add_column("Run")
    table.add_column("BioProject")
    table.add_column("BioSample")
    table.add_column("Organism")
    table.add_column("TaxID")
    table.add_column("Strategy")
    table.add_column("Source")
    table.add_column("Layout")
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
        )
    console.print(table)
    groups = group_sra_metadata(metadata)
    group_table = Table(title="Groups")
    group_table.add_column("Group")
    group_table.add_column("Runs")
    group_table.add_column("Organism")
    group_table.add_column("BioProject")
    group_table.add_column("Layout")
    group_table.add_column("Source")
    for idx, group in enumerate(groups, start=1):
        group_table.add_row(
            str(idx),
            ", ".join(record.run for record in group.runs),
            group.scientific_name,
            group.bioproject,
            group.library_layout,
            group.library_source,
        )
    console.print(group_table)
    if len(groups) > 1:
        console.print("[bold red]Mixed groups detected. Split samples before reference/alignment/quantification.[/bold red]")
    console.print(f"[green]metadata sidecars written:[/green] {len(written)}")


def _preflight_sra_metadata_for_download(requests: list[DownloadRequest], output_dir: Path, state: TuiState) -> bool:
    accessions = sorted({request.accession.upper() for request in requests if looks_like_sra_accession(request.accession)})
    if not accessions:
        return True
    try:
        metadata = fetch_sra_metadata(accessions)
    except OSError as exc:
        _message("元数据预检跳过", f"无法获取 SRA RunInfo，下载仍可继续。\n{exc}")
        return True
    if not metadata:
        _message("元数据预检跳过", "NCBI RunInfo 没有返回记录，下载仍可继续。")
        return True
    written = write_sra_metadata_sidecars(metadata, output_dir)
    _capture_output(
        state,
        lambda console: _print_sra_metadata_report(console, metadata, written),
        "下载前 SRA 元数据预检",
    )
    if len(group_sra_metadata(metadata)) <= 1:
        return True
    return _yes_no(
        "检测到混合分组，仍继续下载？",
        False,
    )


def _resume_download_menu(state: TuiState) -> None:
    form = _tool_run_wizard(
        "继续未完成下载",
        {
            "accession": "",
            "output_dir": state.task.downloads_dir if state.task else Path("downloads"),
            "max_size": "20G",
            "execution_mode": "docker",
            "docker_image": "rnaseq-workflow:tools",
            "actual_run": False,
        },
        [
            ("accession", "SRA 编号", "输入要继续下载的 SRR、ERR 或 DRR 编号。", "str", None, ()),
            ("output_dir", "输出目录", "选择包含半成品或目标下载目录。", "path", None, ()),
            ("max_size", "下载大小上限", "可使用 20G、50G 等格式。", "str", None, ()),
            ("execution_mode", "执行方式", "Docker 使用容器工具；Local 使用本机工具。", "choice", None, (("docker", "Docker"), ("local", "Local"))),
            ("docker_image", "Docker 镜像", "仅 Docker 模式使用。", "str", None, ()),
            ("actual_run", "实际继续下载", "关闭时只做 dry-run。", "bool", None, ()),
        ],
    )
    if form is None:
        return
    accession = str(form["accession"])
    output_dir = Path(form["output_dir"])
    downloader = PrefetchDownloader(
        max_size=str(form["max_size"]) or None,
        execution_mode=str(form["execution_mode"]),
        docker_image=str(form["docker_image"]),
        docker_workspace=Path("."),
        resume_partial=True,
    )
    result = downloader.download(
        DownloadRequest(accession=accession, output_dir=output_dir),
        dry_run=not bool(form["actual_run"]),
    )
    _capture_output(state, lambda console: print_download_results(console, [result], title=f"Resume download: {accession}"), "续传结果")


def _run_download_with_tui_progress(
    manager: DownloadManager,
    requests: list[DownloadRequest],
    dry_run: bool,
    title: str,
):
    if not requests:
        from rnaseq_workflow.steps.download.models import BatchDownloadSummary

        return BatchDownloadSummary()

    status_area = TextArea(
        text=_download_progress_text(manager, requests, title, dry_run, done=False),
        read_only=True,
        scrollbar=True,
        focusable=False,
        wrap_lines=False,
    )
    kb = KeyBindings()
    result_holder = {"summary": None, "error": None, "done": False}

    @kb.add("c")
    def _cancel(event) -> None:
        manager.cancel_all()
        status_area.text = _download_progress_text(manager, requests, title, dry_run, done=False, note="正在取消...")
        event.app.invalidate()

    @kb.add("q")
    def _quit_if_done(event) -> None:
        if result_holder["done"]:
            event.app.exit()

    def worker() -> None:
        try:
            result_holder["summary"] = manager.download_many(requests, dry_run=dry_run)
        except BaseException as exc:
            result_holder["error"] = exc
        finally:
            result_holder["done"] = True

    app = Application(
        layout=Layout(
            Box(
                Frame(
                    HSplit(
                        [
                            status_area,
                            Label(text=""),
                            Label(text="按 c 取消下载。完成后按 q 返回。"),
                        ]
                    ),
                    title=title,
                ),
                padding=1,
            )
        ),
        key_bindings=kb,
        style=STYLE,
        full_screen=True,
    )

    def refresher() -> None:
        while not result_holder["done"]:
            status_area.text = _download_progress_text(manager, requests, title, dry_run, done=False)
            app.invalidate()
            time.sleep(0.5)
        status_area.text = _download_progress_text(manager, requests, title, dry_run, done=True)
        app.invalidate()

    threading.Thread(target=worker, daemon=True).start()
    threading.Thread(target=refresher, daemon=True).start()
    app.run()
    if result_holder["error"]:
        raise result_holder["error"]
    return result_holder["summary"]


def _download_progress_text(
    manager: DownloadManager,
    requests: list[DownloadRequest],
    title: str,
    dry_run: bool,
    done: bool,
    note: str = "",
) -> str:
    overall = manager.overall_progress()
    total = len(requests)
    finished = overall.completed + overall.failed + overall.cancelled + overall.skipped
    total_eta = _download_total_eta(manager, requests)
    total_eta_text = f"  剩余: {total_eta}" if total_eta else ""
    lines = [
        title,
        f"模式: {'dry-run' if dry_run else '实际下载'}",
        f"总进度: {finished}/{total}  completed={overall.completed} failed={overall.failed} cancelled={overall.cancelled} skipped={overall.skipped}",
        f"总大小: {_format_bytes(overall.downloaded_bytes)}  总速度: {_format_bytes(overall.speed_bps)}/s{total_eta_text}",
    ]
    if note:
        lines.append(f"提示: {note}")
    lines.append("")
    for request in requests:
        row = manager.get_progress(request.accession)
        partial = find_partial_sra_artifacts(request.accession, request.output_dir)
        partial_size = sum(path.stat().st_size for path in partial if path.exists())
        partial_hint = f" partial={_format_bytes(partial_size)}" if partial_size else ""
        expected = request.expected_size_bytes
        if row is None:
            bar = _text_progress_bar(_estimated_percent(0, partial_size, expected))
            lines.append(f"{request.accession}: PENDING {bar} 0B{partial_hint}")
            continue
        estimated_percent = row.percent if row.percent is not None else _estimated_percent(row.downloaded_bytes, partial_size, expected)
        percent = "" if estimated_percent is None else f" {estimated_percent:.1f}%"
        bar = _text_progress_bar(estimated_percent)
        expected_hint = f"/{_format_bytes(expected)}" if expected else ""
        eta = _download_eta(row.downloaded_bytes, row.speed_bps, expected, estimated_percent)
        eta_hint = f" 剩余:{eta}" if eta else ""
        lines.append(
            f"{request.accession}: {row.status.value} {bar}{percent} "
            f"{_format_bytes(row.downloaded_bytes)}{expected_hint}{partial_hint} {_format_bytes(row.speed_bps)}/s{eta_hint} {_compact_progress_detail(row.message)}"
        )
    if done:
        lines.append("")
        lines.append(_download_done_message(cancelled=overall.cancelled, failed=overall.failed))
    return "\n".join(lines)


def _download_done_message(cancelled: int = 0, failed: int = 0) -> str:
    if cancelled:
        return "下载已取消，已保留半成品。按 q 返回。"
    if failed:
        return "下载未完成。按 q 返回。"
    return "下载已完成。按 q 返回。"


def _estimated_percent(downloaded_bytes: int, partial_bytes: int, expected_size_bytes: int | None) -> float | None:
    if not expected_size_bytes or expected_size_bytes <= 0:
        return None
    current = max(downloaded_bytes, partial_bytes)
    return min(current / expected_size_bytes * 100, 100.0)


def _download_total_eta(manager: DownloadManager, requests: list[DownloadRequest]) -> str:
    remaining = 0
    known = False
    speed = 0.0
    for request in requests:
        row = manager.get_progress(request.accession)
        if row is None:
            if request.expected_size_bytes:
                remaining += request.expected_size_bytes
                known = True
            continue
        speed += max(row.speed_bps, 0.0)
        expected = _download_expected_bytes(row.downloaded_bytes, request.expected_size_bytes, row.percent)
        if expected is not None:
            remaining += max(0, expected - row.downloaded_bytes)
            known = True
    if not known or speed <= 0:
        return ""
    return _format_duration(remaining / speed)


def _download_eta(downloaded_bytes: int, speed_bps: float, expected_size_bytes: int | None, percent: float | None) -> str:
    expected = _download_expected_bytes(downloaded_bytes, expected_size_bytes, percent)
    if expected is None or speed_bps <= 0:
        return ""
    return _format_duration(max(0, expected - downloaded_bytes) / speed_bps)


def _download_expected_bytes(downloaded_bytes: int, expected_size_bytes: int | None, percent: float | None) -> int | None:
    if expected_size_bytes and expected_size_bytes > 0:
        return expected_size_bytes
    if percent and percent > 0:
        return int(downloaded_bytes / (percent / 100.0))
    return None


def _format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"


def _text_progress_bar(percent: float | None, width: int = 24) -> str:
    if percent is None:
        return "[" + "." * width + "]"
    filled = int(width * max(0.0, min(percent, 100.0)) / 100.0)
    return "[" + "#" * filled + "." * (width - filled) + "]"


def _scan_inputs(state: TuiState) -> None:
    form = _tool_run_wizard(
        "扫描输入",
        {
            "input_dir": state.task.downloads_dir if state.task else Path("data"),
            "project_id": state.task_id or "",
        },
        [
            ("input_dir", "输入目录", "选择包含 FASTQ 或 SRA 文件的目录。", "path", None, ()),
            ("project_id", "项目 ID", "可留空。用于扫描结果中的样本归属。", "str", None, ()),
        ],
    )
    if form is None:
        return
    input_dir = Path(form["input_dir"])
    result = scan_inputs(input_dir, project_id=str(form.get("project_id") or "") or None)
    _capture_output(state, lambda console: _print_scan_result(console, result.samples), f"扫描完成，发现样本数: {len(result.samples)}")


def _print_scan_result(console: Console, samples: list[Sample]) -> None:
    if not samples:
        console.print("没有发现 FASTQ/SRA 输入文件。")
        return
    table = Table(title="输入扫描结果")
    table.add_column("Sample")
    table.add_column("Type")
    table.add_column("Layout")
    table.add_column("Organism")
    table.add_column("BioProject")
    table.add_column("TaxID")
    table.add_column("Files", justify="right")
    table.add_column("Paths")
    for sample in samples:
        paths = [str(path) for path in sample.source_paths]
        if len(paths) > 2:
            path_text = f"{paths[0]}; {paths[1]}; ..."
        else:
            path_text = "; ".join(paths)
        table.add_row(
            sample.sample_id,
            str(sample.metadata.get("input_type", "")),
            sample.layout.value,
            str(sample.metadata.get("scientific_name", "")),
            str(sample.metadata.get("bioproject", "")),
            str(sample.metadata.get("taxid", "")),
            str(len(sample.source_paths)),
            path_text,
        )
    console.print(table)


def _sra_to_fastq_menu(state: TuiState) -> None:
    task = state.task
    form = _tool_run_wizard(
        "SRA 转 FASTQ",
        {
            "input_dir": task.downloads_dir if task else Path("downloads"),
            "project_id": "sra_to_fastq_test",
            "output_dir": task.task_output_dir if task else Path("runtime_logs") / "sra_to_fastq",
            "threads": 4,
            "max_workers": DEFAULT_HEAVY_STEP_CONCURRENCY,
            "actual_run": True,
        },
        [
            ("input_dir", "SRA 输入目录", "选择包含 .sra 文件的目录。", "path", None, ()),
            ("project_id", "项目 ID", "用于输出目录命名。", "str", None, ()),
            ("output_dir", "输出目录", "转换后的 FASTQ 会写入这里。", "path", None, ()),
            ("threads", "fasterq-dump 线程数", _friendly_field("fasterq-dump 线程数")[1], "int", 1, ()),
            ("max_workers", "样本并发数", _friendly_field("样本并发数")[1], "int", 1, ()),
            ("actual_run", "实际运行", "关闭时只做 dry-run。", "bool", None, ()),
        ],
    )
    if form is None:
        return
    input_dir = Path(form["input_dir"])
    project_id = str(form["project_id"] or "sra_to_fastq_test")
    output_dir = Path(form["output_dir"])
    try:
        scan = scan_inputs(input_dir, project_id=project_id)
    except (FileNotFoundError, NotADirectoryError) as exc:
        _message("扫描失败", str(exc))
        return
    sra_samples = [sample for sample in scan.samples if sample.metadata.get("input_type") == "sra"]
    if not sra_samples:
        _message("未发现 SRA", f"{input_dir} 下没有 .sra 文件。")
        return
    selected = _choose_sra_target(sra_samples)
    if selected is None:
        return
    if not selected:
        _message("选择错误", "没有选中 SRA 样本。")
        return
    context = RunContext(
        project_id=project_id or "sra_to_fastq_test",
        work_dir=Path.cwd(),
        output_dir=output_dir,
        config={
            "fasterq_dump_threads": int(form["threads"]),
            "fasterq_dump_split_files": True,
            "fasterq_dump_progress": False,
            "cleanup_on_fail": True,
            "execution_mode": "docker",
            "docker_image": "rnaseq-workflow:tools",
            "docker_workspace": str(Path(".")),
        },
        dry_run=not bool(form["actual_run"]),
    )
    results = _run_step_with_tui_progress(selected, context, SraToFastqStep(), title="SRA 转 FASTQ", max_workers=int(form["max_workers"]))
    _capture_output(state, lambda console: _print_step_results(console, results, title="SRA to FASTQ Results"), "SRA 转 FASTQ 结果")


def _choose_sra_target(samples: list[Sample]) -> list[Sample] | None:
    return _sample_multiselect("选择 SRA 样本", samples)


def _tool_run_wizard(
    title: str,
    defaults: dict[str, Any],
    fields: list[tuple[str, str, str, str, int | None, tuple[tuple[str, str], ...]]],
) -> dict[str, Any] | None:
    data = dict(defaults)
    index = 0
    while True:
        key, field_title, help_text, kind, minimum, choices = fields[index]
        changed = _collect_tool_run_field(title, index, len(fields), key, field_title, help_text, kind, minimum, choices, data)
        if changed == "back":
            return None
        if changed == "prev":
            index = max(0, index - 1)
            continue
        if changed != "ok":
            continue
        valid, message = _validate_tool_run_value(field_title, data.get(key), kind, minimum)
        if not valid:
            _message("需要补充", message)
            continue
        if index == len(fields) - 1:
            return data
        index += 1


def _collect_tool_run_field(
    title: str,
    index: int,
    total: int,
    key: str,
    field_title: str,
    help_text: str,
    kind: str,
    minimum: int | None,
    choices: tuple[tuple[str, str], ...],
    data: dict[str, Any],
) -> str:
    page_title = f"{title} {index + 1}/{total}"
    current = _format_tool_run_value(kind, data.get(key), choices)
    text = f"当前: {current}\n\n{help_text}"
    if kind in {"choice", "choice_custom"}:
        menu_choices = list(choices)
        if kind == "choice_custom":
            custom_label = f"自定义: {data.get(key)}" if str(data.get(key) or "").strip() else "自定义"
            menu_choices.append(("__custom__", custom_label))
        selected = _tool_choice_page(page_title, field_title, text, menu_choices, current_value=str(data.get(key) or ""), has_previous=index > 0, is_last=index == total - 1)
        if selected is None:
            return "back"
        if selected == "__prev__":
            return "prev"
        if selected == "__custom__":
            value = _tool_input_page(page_title, field_title, "输入自定义值。", str(data.get(key) or ""), has_previous=index > 0)
            if value == "__prev__":
                return "prev"
            if value is None:
                return "retry"
            data[key] = value.strip()
            return "ok"
        data[key] = selected
        return "ok"
    if kind == "bool":
        selected = _tool_choice_page(page_title, field_title, text, [("yes", "是"), ("no", "否")], current_value="yes" if data.get(key) else "no", has_previous=index > 0, is_last=index == total - 1)
        if selected is None:
            return "back"
        if selected == "__prev__":
            return "prev"
        data[key] = selected == "yes"
        return "ok"
    if kind == "int":
        value = _int_input(field_title, int(data.get(key) or minimum or 0), minimum=minimum, cancel_returns_default=False)
        if value is None:
            return "prev" if index > 0 else "back"
        data[key] = value
        return "ok"
    if kind == "float":
        value = _tool_input_page(page_title, field_title, help_text, str(data.get(key) or minimum or 0), has_previous=index > 0)
        if value == "__prev__":
            return "prev"
        if value is None:
            return "back"
        data[key] = value.strip()
        return "ok"
    if kind == "path":
        value = _path_input(field_title, data.get(key), directory=True)
        if value is None:
            return "prev" if index > 0 else "back"
        data[key] = value
        return "ok"
    value = _tool_input_page(page_title, field_title, help_text, str(data.get(key) or ""), has_previous=index > 0)
    if value == "__prev__":
        return "prev"
    if value is None:
        return "back"
    data[key] = value.strip()
    return "ok"


def _tool_choice_page(
    title: str,
    field_title: str,
    text: str,
    values: list[tuple[str, str]],
    current_value: str = "",
    has_previous: bool = False,
    is_last: bool = False,
) -> str | None:
    if _use_line_dialogs():
        line_values = list(values)
        if has_previous:
            line_values.append(("__prev__", "上一步"))
        return _line_menu(field_title, text, line_values)

    selected = {"index": 0}
    for idx, (value, _label) in enumerate(values):
        if str(value) == str(current_value):
            selected["index"] = idx
            break

    kb = KeyBindings()
    result = {"value": None}
    dialog_width = max(64, min(92, max(get_cwidth(field_title) + 24, *[get_cwidth(label) + 18 for _value, label in values], 64)))
    menu_width = min(56, max([get_cwidth(label) for _value, label in values] + [20]) + 10)
    menu_indent = max(0, (dialog_width - menu_width - 6) // 2)

    def choose(index: int, event=None) -> None:
        selected["index"] = max(0, min(index, len(values) - 1))
        result["value"] = values[selected["index"]][0]
        if event is not None:
            event.app.exit(result=result["value"])

    def move(delta: int, event=None) -> None:
        selected["index"] = (selected["index"] + delta) % len(values)
        if event is not None:
            event.app.invalidate()

    def exit_with(value: str | None):
        def handle(mouse_event: MouseEvent) -> None:
            if mouse_event.event_type == MouseEventType.MOUSE_UP:
                from prompt_toolkit.application.current import get_app

                if value == "__accept__":
                    result["value"] = values[selected["index"]][0]
                    get_app().exit(result=result["value"])
                else:
                    get_app().exit(result=value)

        return handle

    def option_mouse_handler(index: int):
        def handle(mouse_event: MouseEvent) -> None:
            if mouse_event.event_type == MouseEventType.MOUSE_UP:
                choose(index)
                from prompt_toolkit.application.current import get_app

                get_app().exit(result=result["value"])

        return handle

    def render_options():
        fragments: list[Any] = []
        fragments.append(("class:menu.border", f"{field_title}\n"))
        current_line = str(text).splitlines()[0] if str(text).strip() else ""
        if current_line:
            fragments.append(("class:dialog.body", current_line + "\n"))
        fragments.append(("", "\n"))
        for index, (_value, label) in enumerate(values):
            active = index == selected["index"]
            handler = option_mouse_handler(index)
            label_text = str(label)
            padding = max(0, menu_width - get_cwidth(label_text) - 8)
            indent = " " * menu_indent
            if active:
                fragments.extend(
                    [
                        ("class:menu", indent, handler),
                        ("class:menu.border", " > ", handler),
                        ("class:menu.marker", "* ", handler),
                        ("class:menu.selected", label_text, handler),
                        ("class:menu", " " * padding, handler),
                        ("class:menu.border", " <\n", handler),
                    ]
                )
            else:
                fragments.append(("class:menu", f"{indent}   {label_text}\n", handler))
        _value, active_label = values[selected["index"]]
        fragments.append(("", "\n"))
        fragments.append(("class:menu.border", "说明: "))
        hint = _menu_item_hint(active_label, fallback="\n".join(str(text).splitlines()[2:]))
        hint_lines = _wrap_display_text(hint, max(24, dialog_width - 12))
        fragments.append(("class:dialog.body", hint_lines[0]))
        for line in hint_lines[1:]:
            fragments.append(("", "\n"))
            fragments.append(("class:dialog.body", "      " + line))
        return FormattedText(fragments)

    def render_buttons():
        next_label = "保存 Enter" if is_last else "下一步 Enter"
        fragments: list[Any] = []
        if has_previous:
            fragments.append(("class:menu.border", "< 上一步 PgUp >", exit_with("__prev__")))
        else:
            fragments.append(("class:dialog.body", "  上一步 PgUp  "))
        fragments.append(("class:dialog.body", "  "))
        fragments.append(("class:menu.border", f"< {next_label} >", exit_with("__accept__")))
        fragments.append(("class:dialog.body", "  "))
        fragments.append(("class:menu.border", "< 返回 Esc >", exit_with(None)))
        return FormattedText(fragments)

    control = FormattedTextControl(render_options, focusable=True)
    button_control = FormattedTextControl(render_buttons, focusable=False)

    @kb.add("enter")
    @kb.add("pagedown")
    def _accept(event) -> None:
        choose(selected["index"], event)

    @kb.add("pageup")
    def _prev(event) -> None:
        event.app.exit(result="__prev__" if has_previous else None)

    @kb.add("down")
    @kb.add("right")
    def _down(event) -> None:
        move(1, event)

    @kb.add("up")
    @kb.add("left")
    def _up(event) -> None:
        move(-1, event)

    @kb.add("escape")
    @kb.add("c-c")
    def _cancel(event) -> None:
        event.app.exit(result=None)

    dialog = Dialog(
        title=HTML(f"<b><ansicyan>{title}</ansicyan></b>"),
        body=HSplit(
            [
                Window(content=control, always_hide_cursor=True, dont_extend_height=True),
                Window(content=button_control, always_hide_cursor=True, height=1, dont_extend_height=True, align=WindowAlign.CENTER),
            ],
            padding=1,
        ),
        buttons=[],
        width=Dimension(min=dialog_width, preferred=dialog_width, max=dialog_width),
        with_background=True,
    )
    app = Application(layout=Layout(dialog, focused_element=control), key_bindings=kb, style=STYLE, mouse_support=True, full_screen=True)
    return app.run()


def _tool_input_page(
    title: str,
    field_title: str,
    text: str,
    default: str = "",
    has_previous: bool = False,
) -> str | None:
    if _use_line_dialogs():
        return _line_input(field_title, text, default)

    result = {"value": None}
    text_area = TextArea(
        text=default,
        multiline=False,
        width=Dimension(preferred=72),
        style="class:input",
    )
    kb = KeyBindings()

    def exit_with(value: str | None):
        def handle(mouse_event: MouseEvent) -> None:
            if mouse_event.event_type == MouseEventType.MOUSE_UP:
                from prompt_toolkit.application.current import get_app

                if value == "__accept__":
                    result["value"] = text_area.text
                    get_app().exit(result=result["value"])
                else:
                    get_app().exit(result=value)

        return handle

    def render_buttons():
        fragments: list[Any] = []
        if has_previous:
            fragments.append(("class:menu.border", "< 上一步 PgUp >", exit_with("__prev__")))
        else:
            fragments.append(("class:dialog.body", "  上一步 PgUp  "))
        fragments.append(("class:dialog.body", "  "))
        fragments.append(("class:menu.border", "< 确认 Enter >", exit_with("__accept__")))
        fragments.append(("class:dialog.body", "  "))
        fragments.append(("class:menu.border", "< 返回 Esc >", exit_with(None)))
        return FormattedText(fragments)

    button_control = FormattedTextControl(render_buttons, focusable=False)

    @kb.add("enter")
    def _accept(event) -> None:
        result["value"] = text_area.text
        event.app.exit(result=result["value"])

    @kb.add("pageup")
    def _prev(event) -> None:
        event.app.exit(result="__prev__" if has_previous else None)

    @kb.add("escape")
    @kb.add("c-c")
    def _cancel(event) -> None:
        event.app.exit(result=None)

    dialog = Dialog(
        title=HTML(f"<b><ansicyan>{title}</ansicyan></b>"),
        body=HSplit(
            [
                Frame(text_area, title=HTML(f"<ansicyan>{field_title}</ansicyan>"), width=Dimension(preferred=76)),
                Label(text=f"说明: {_short_hint(text)}"),
                Window(content=button_control, always_hide_cursor=True, height=1, dont_extend_height=True, align=WindowAlign.CENTER),
            ],
            padding=1,
        ),
        buttons=[],
        width=Dimension(min=72, preferred=86, max=96),
        with_background=True,
    )
    app = Application(layout=Layout(dialog, focused_element=text_area), key_bindings=kb, style=STYLE, mouse_support=True, full_screen=True)
    return app.run()


def _format_tool_run_value(kind: str, value: Any, choices: tuple[tuple[str, str], ...]) -> str:
    if kind == "bool":
        return "是" if value else "否"
    if kind in {"choice", "choice_custom"}:
        return next((label for key, label in choices if str(key) == str(value)), str(value))
    if value is None or str(value).strip() == "":
        return "未设置"
    return str(value)


def _validate_tool_run_value(label: str, value: Any, kind: str, minimum: int | None) -> tuple[bool, str]:
    if label in {"下载代理", "项目 ID", "project_id，可留空"}:
        return True, ""
    if kind in {"str", "path"} and not str(value or "").strip():
        return False, f"请填写 {label}。"
    if kind == "int":
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return False, f"{label} 必须是整数。"
        if minimum is not None and parsed < minimum:
            return False, f"{label} 不能小于 {minimum}。"
    if kind == "float":
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return False, f"{label} 必须是数字。"
        if minimum is not None and parsed < minimum:
            return False, f"{label} 不能小于 {minimum}。"
    return True, ""


def _fastqc_menu(state: TuiState) -> None:
    task = state.task
    form = _tool_run_wizard(
        "FastQC",
        {
            "input_dir": task.downloads_dir if task else Path("downloads"),
            "project_id": "fastqc_test",
            "output_dir": task.task_output_dir if task else Path("runtime_logs") / "fastqc_test",
            "threads": 2,
            "max_workers": DEFAULT_HEAVY_STEP_CONCURRENCY,
            "extract": False,
            "actual_run": True,
        },
        [
            ("input_dir", "FASTQ 输入目录", "选择包含 FASTQ 文件的目录。", "path", None, ()),
            ("project_id", "项目 ID", "用于输出目录命名。", "str", None, ()),
            ("output_dir", "输出目录", "FastQC 结果会写入这里。", "path", None, ()),
            ("threads", "FastQC 线程数", _friendly_field("FastQC 线程数")[1], "int", 1, ()),
            ("max_workers", "样本并发数", _friendly_field("样本并发数")[1], "int", 1, ()),
            ("extract", "解压 FastQC 结果", "开启后保留解压后的 FastQC 目录。", "bool", None, ()),
            ("actual_run", "实际运行", "关闭时只做 dry-run。", "bool", None, ()),
        ],
    )
    if form is None:
        return
    input_dir = Path(form["input_dir"])
    project_id = str(form["project_id"] or "fastqc_test")
    output_dir = Path(form["output_dir"])
    try:
        scan = scan_inputs(input_dir, project_id=project_id)
    except (FileNotFoundError, NotADirectoryError) as exc:
        _message("扫描失败", str(exc))
        return
    fastq_samples = [sample for sample in scan.samples if sample.metadata.get("input_type") == "fastq"]
    if not fastq_samples:
        _message("未发现 FASTQ", f"{input_dir} 下没有可用于 FastQC 的 FASTQ 文件。")
        return
    selected = _choose_fastqc_target(fastq_samples)
    if selected is None:
        return
    if not selected:
        _message("选择错误", "没有选中样本。")
        return
    context = RunContext(
        project_id=project_id or "fastqc_test",
        work_dir=Path.cwd(),
        output_dir=output_dir,
        config={
            "fastqc_threads": int(form["threads"]),
            "fastqc_extract": bool(form["extract"]),
            "fastqc_quiet": True,
            "cleanup_on_fail": True,
            "execution_mode": "docker",
            "docker_image": "rnaseq-workflow:tools",
            "docker_workspace": str(Path(".")),
        },
        dry_run=not bool(form["actual_run"]),
    )
    results = _run_step_with_tui_progress(selected, context, FastQCStep(), title="FastQC 质控", max_workers=int(form["max_workers"]))
    _capture_output(state, lambda console: _print_step_results(console, results, title="FastQC Results"), "FastQC 结果")


def _trim_galore_menu(state: TuiState) -> None:
    task = state.task
    form = _tool_run_wizard(
        "Trim Galore",
        {
            "input_dir": task.downloads_dir if task else Path("downloads"),
            "project_id": "trim_test",
            "output_dir": task.task_output_dir if task else Path("runtime_logs") / "trim_test",
            "quality": 20,
            "cores": 1,
            "max_workers": DEFAULT_TUI_CONCURRENCY,
            "actual_run": True,
        },
        [
            ("input_dir", "FASTQ 输入目录", "选择包含 FASTQ 文件的目录。", "path", None, ()),
            ("project_id", "项目 ID", "用于输出目录命名。", "str", None, ()),
            ("output_dir", "输出目录", "修剪后的 FASTQ 会写入这里。", "path", None, ()),
            ("quality", "修剪质量阈值", _friendly_field("quality")[1], "int", 0, ()),
            ("cores", "Trim Galore cores", _friendly_field("Trim Galore cores")[1], "int", 1, ()),
            ("max_workers", "样本并发数", _friendly_field("样本并发数")[1], "int", 1, ()),
            ("actual_run", "实际运行", "关闭时只做 dry-run。", "bool", None, ()),
        ],
    )
    if form is None:
        return
    input_dir = Path(form["input_dir"])
    project_id = str(form["project_id"] or "trim_test")
    output_dir = Path(form["output_dir"])
    try:
        scan = scan_inputs(input_dir, project_id=project_id)
    except (FileNotFoundError, NotADirectoryError) as exc:
        _message("扫描失败", str(exc))
        return
    fastq_samples = [sample for sample in scan.samples if sample.metadata.get("input_type") == "fastq"]
    if not fastq_samples:
        _message("未发现 FASTQ", f"{input_dir} 下没有可用于 Trim Galore 的 FASTQ 文件。")
        return
    selected = _choose_trim_target(fastq_samples)
    if selected is None:
        return
    if not selected:
        _message("选择错误", "没有选中样本。")
        return
    context = RunContext(
        project_id=project_id or "trim_test",
        work_dir=Path.cwd(),
        output_dir=output_dir,
        config={
            "trim_galore_quality": int(form["quality"]),
            "trim_galore_phred": "33",
            "trim_galore_stringency": 3,
            "trim_galore_cores": int(form["cores"]),
            "trim_galore_gzip": True,
            "cleanup_on_fail": True,
            "execution_mode": "docker",
            "docker_image": "rnaseq-workflow:tools",
            "docker_workspace": str(Path(".")),
        },
        dry_run=not bool(form["actual_run"]),
    )
    results = _run_step_with_tui_progress(selected, context, TrimGaloreStep(), title="Trim Galore 修剪", max_workers=int(form["max_workers"]))
    _capture_output(state, lambda console: _print_step_results(console, results, title="Trim Galore Results"), "Trim Galore 结果")


def _choose_trim_target(samples: list[Sample]) -> list[Sample] | None:
    return _sample_multiselect("选择 Trim Galore 样本", samples)


def _hisat2_menu(state: TuiState) -> None:
    task = state.task
    form = _tool_run_wizard(
        "HISAT2",
        {
            "input_dir": task.task_output_dir if task else Path("runtime_logs") / "trim_test",
            "project_id": "hisat2_test",
            "output_dir": task.task_output_dir if task else Path("runtime_logs") / "hisat2_test",
            "threads": 4,
            "max_workers": DEFAULT_HEAVY_STEP_CONCURRENCY,
            "actual_run": True,
        },
        [
            ("input_dir", "FASTQ 输入目录", "选择修剪后或原始 FASTQ 所在目录。", "path", None, ()),
            ("project_id", "项目 ID", "用于输出目录命名。", "str", None, ()),
            ("output_dir", "输出目录", "HISAT2 SAM 和日志会写入这里。", "path", None, ()),
            ("threads", "HISAT2 线程数", _friendly_field("HISAT2 线程数")[1], "int", 1, ()),
            ("max_workers", "样本并发数", _friendly_field("样本并发数")[1], "int", 1, ()),
            ("actual_run", "实际运行", "关闭时只做 dry-run。", "bool", None, ()),
        ],
    )
    if form is None:
        return
    input_dir = Path(form["input_dir"])
    project_id = str(form["project_id"] or "hisat2_test")
    output_dir = Path(form["output_dir"])
    try:
        scan = scan_inputs(input_dir, project_id=project_id)
    except (FileNotFoundError, NotADirectoryError) as exc:
        _message("扫描失败", str(exc))
        return
    fastq_samples = [sample for sample in scan.samples if sample.metadata.get("input_type") == "fastq"]
    if not fastq_samples:
        _message("未发现 FASTQ", f"{input_dir} 下没有可用于 HISAT2 的 FASTQ 文件。")
        return
    selected = _choose_hisat2_target(fastq_samples)
    if selected is None:
        return
    if not selected:
        _message("选择错误", "没有选中样本。")
        return
    reference_choice = _choose_reference_asset(state)
    if reference_choice is None:
        return
    reference_dir, reference_id = reference_choice
    try:
        asset = load_reference(reference_id, reference_dir)
        report = check_reference_asset(asset)
    except FileNotFoundError as exc:
        _message("Reference 错误", str(exc))
        return
    if not report.ok:
        _message("Reference 检查失败", "\n".join(f"{issue.field}: {issue.message}" for issue in report.issues))
        return
    context = RunContext(
        project_id=project_id or "hisat2_test",
        work_dir=Path.cwd(),
        output_dir=output_dir,
        config={
            "hisat2_index": str(asset.hisat2_index),
            "hisat2_threads": int(form["threads"]),
            "execution_mode": "docker",
            "docker_image": "rnaseq-workflow:tools",
            "docker_workspace": str(Path(".")),
        },
        dry_run=not bool(form["actual_run"]),
    )
    results = _run_step_with_tui_progress(selected, context, Hisat2AlignStep(), title="HISAT2 对齐", max_workers=int(form["max_workers"]))
    _capture_output(state, lambda console: _print_step_results(console, results, title="HISAT2 Results"), "HISAT2 结果")


def _choose_hisat2_target(samples: list[Sample]) -> list[Sample] | None:
    return _sample_multiselect("选择 HISAT2 样本", samples)


def _choose_reference_id(reference_dir: Path) -> str | None:
    selected = _browse_references(reference_dir, select_mode=True)
    return selected.reference_id if selected else None


def _choose_reference_asset(state: TuiState, current_reference_id: str = "") -> tuple[Path, str] | None:
    candidates: list[tuple[str, Path, ReferenceAsset]] = []
    search_dirs: list[tuple[str, Path]] = []
    if state.user_id:
        search_dirs.append(("我的资产", state.workspace.user(state.user_id).user_reference_dir))
    search_dirs.append(("公共资产", state.workspace.global_reference_dir))
    if not state.user_id and not state.workspace.global_reference_dir.exists():
        legacy_dir = _path_input("reference_dir", Path("references"), must_exist=True, directory=True)
        if not legacy_dir:
            return None
        reference_id = _choose_reference_id(legacy_dir)
        if reference_id in (None, "back"):
            return None
        return legacy_dir, reference_id
    seen: set[tuple[Path, str]] = set()
    for scope, reference_dir in search_dirs:
        cleanup_stale_reference_records(reference_dir, state.workspace.database_path)
        for asset in list_references(reference_dir):
            key = (reference_dir.resolve(), asset.reference_id)
            if key in seen:
                continue
            seen.add(key)
            candidates.append((scope, reference_dir, asset))
    if not candidates:
        _message("Reference 错误", "当前可用资产库中没有 reference。")
        return None
    ordered_candidates = sorted(candidates, key=lambda row: 0 if row[2].reference_id == current_reference_id else 1)
    selected = _browse_reference_candidates(ordered_candidates, current_reference_id=current_reference_id, select_mode=True)
    if not selected:
        return None
    _scope, reference_dir, asset = selected
    return reference_dir, asset.reference_id


def _load_task_reference_asset(task: TaskWorkspace, reference_id: str) -> ReferenceAsset:
    reference_dirs: list[Path] = []
    if task.user_id:
        reference_dirs.append(task.root.parents[1] / "references")
    reference_dirs.append(task.root.parents[3] / "shared" / "references")
    reference_dirs.append(Path("references"))
    for reference_dir in reference_dirs:
        if reference_dir.exists():
            try:
                return load_reference(reference_id, reference_dir)
            except FileNotFoundError:
                continue
    raise FileNotFoundError(f"reference not found for task {task.task_id}: {reference_id}")


def _samtools_menu(state: TuiState) -> None:
    task = state.task
    form = _tool_run_wizard(
        "Samtools",
        {
            "input_dir": task.task_output_dir if task else Path("runtime_logs") / "hisat2_test",
            "project_id": "samtools_test",
            "output_dir": task.task_output_dir if task else Path("runtime_logs") / "samtools_test",
            "threads": 2,
            "max_workers": DEFAULT_HEAVY_STEP_CONCURRENCY,
            "actual_run": True,
        },
        [
            ("input_dir", "SAM 输入目录", "选择 HISAT2 输出的 SAM 文件目录。", "path", None, ()),
            ("project_id", "项目 ID", "用于输出目录命名。", "str", None, ()),
            ("output_dir", "输出目录", "排序后的 BAM 和索引会写入这里。", "path", None, ()),
            ("threads", "samtools sort 线程数", _friendly_field("samtools sort 线程数")[1], "int", 1, ()),
            ("max_workers", "样本并发数", _friendly_field("样本并发数")[1], "int", 1, ()),
            ("actual_run", "实际运行", "关闭时只做 dry-run。", "bool", None, ()),
        ],
    )
    if form is None:
        return
    input_dir = Path(form["input_dir"])
    project_id = str(form["project_id"] or "samtools_test")
    output_dir = Path(form["output_dir"])
    samples = _scan_sam_samples(input_dir, project_id)
    if not samples:
        _message("未发现 SAM", f"{input_dir} 下没有 .sam 文件。")
        return
    selected = _choose_samtools_target(samples)
    if selected is None:
        return
    if not selected:
        _message("选择错误", "没有选中样本。")
        return
    context = RunContext(
        project_id=project_id or "samtools_test",
        work_dir=Path.cwd(),
        output_dir=output_dir,
        config={
            "samtools_threads": int(form["threads"]),
            "samtools_index": True,
            "execution_mode": "docker",
            "docker_image": "rnaseq-workflow:tools",
            "docker_workspace": str(Path(".")),
        },
        dry_run=not bool(form["actual_run"]),
    )
    results = _run_step_with_tui_progress(selected, context, SamtoolsSortStep(), title="Samtools sort/index", max_workers=int(form["max_workers"]))
    _capture_output(state, lambda console: _print_step_results(console, results, title="Samtools Results"), "Samtools 结果")


def _scan_sam_samples(input_dir: Path, project_id: str) -> list[Sample]:
    samples: list[Sample] = []
    for path in sorted(input_dir.rglob("*.sam")):
        sample_id = path.stem
        log_path = path.with_suffix(".hisat2.log")
        alignment_rate = _parse_hisat2_alignment_rate(log_path)
        samples.append(
            Sample(
                sample_id=sample_id,
                source_path=path,
                source_paths=[path],
                project_id=project_id,
                metadata={
                    "input_type": "sam",
                    "alignment_rate": alignment_rate,
                    "size_bytes": path.stat().st_size if path.exists() else 0,
                },
            )
        )
    return samples


def _choose_samtools_target(samples: list[Sample]) -> list[Sample] | None:
    return _sample_multiselect("选择 SAM 样本", samples)


def _featurecounts_menu(state: TuiState) -> None:
    task = state.task
    base_form = _tool_run_wizard(
        "featureCounts",
        {
            "input_dir": task.task_output_dir if task else Path("runtime_logs") / "samtools_test",
            "project_id": "featurecounts_test",
            "output_dir": task.task_output_dir if task else Path("runtime_logs") / "featurecounts_test",
            "threads": 2,
            "max_workers": DEFAULT_HEAVY_STEP_CONCURRENCY,
            "actual_run": True,
        },
        [
            ("input_dir", "BAM 输入目录", "选择 Samtools 输出的 BAM 文件目录。", "path", None, ()),
            ("project_id", "项目 ID", "用于输出目录命名。", "str", None, ()),
            ("output_dir", "输出目录", "featureCounts 表和矩阵会写入这里。", "path", None, ()),
            ("threads", "featureCounts 线程数", _friendly_field("featureCounts 线程数")[1], "int", 1, ()),
            ("max_workers", "样本并发数", _friendly_field("样本并发数")[1], "int", 1, ()),
            ("actual_run", "实际运行", "关闭时只做 dry-run。", "bool", None, ()),
        ],
    )
    if base_form is None:
        return
    input_dir = Path(base_form["input_dir"])
    project_id = str(base_form["project_id"] or "featurecounts_test")
    output_dir = Path(base_form["output_dir"])
    samples = _scan_bam_samples(input_dir, project_id)
    if not samples:
        _message("未发现 BAM", f"{input_dir} 下没有 .bam 文件。")
        return
    selected = _choose_featurecounts_target(samples)
    if selected is None:
        return
    if not selected:
        _message("选择错误", "没有选中样本。")
        return
    reference_choice = _choose_reference_asset(state)
    if reference_choice is None:
        return
    reference_dir, reference_id = reference_choice
    try:
        asset = load_reference(reference_id, reference_dir)
    except FileNotFoundError as exc:
        _message("Reference 错误", str(exc))
        return
    if not asset.annotation:
        _message("Reference 错误", f"{reference_id} 没有 annotation GTF/GFF。")
        return
    defaults = _featurecounts_defaults_for_reference(asset)
    count_form = _tool_run_wizard(
        "featureCounts 注释",
        {
            "feature_type": defaults["feature_type"],
            "attribute_type": defaults["attribute_type"],
            "strandness": defaults["strandness"],
            "paired": "yes" if _samples_are_paired(selected) else "no",
        },
        [
            ("feature_type", "featureCounts 特征类型", "GTF 常用 exon；GFF 可按注释选择 gene。", "choice", None, (("exon", "exon"), ("gene", "gene"), ("CDS", "CDS"))),
            ("attribute_type", "featureCounts 属性字段", "GTF 常用 gene_id；部分 GFF 使用 gene 或 ID。", "choice", None, (("gene_id", "gene_id"), ("gene", "gene"), ("ID", "ID"))),
            ("strandness", "链特异性", _friendly_field("链特异性")[1], "choice", None, (("0", "非链特异"), ("1", "正向链特异"), ("2", "反向链特异"))),
            ("paired", "按片段计数", _friendly_field("使用 paired fragments (-p)")[1], "choice", None, (("yes", "是"), ("no", "否"))),
        ],
    )
    if count_form is None:
        return
    context = RunContext(
        project_id=project_id or "featurecounts_test",
        work_dir=Path.cwd(),
        output_dir=output_dir,
        config={
            "featurecounts_annotation": str(asset.annotation),
            "featurecounts_threads": int(base_form["threads"]),
            "featurecounts_feature_type": str(count_form["feature_type"]),
            "featurecounts_attribute_type": str(count_form["attribute_type"]),
            "featurecounts_strandness": int(count_form["strandness"]),
            "featurecounts_paired": count_form["paired"] == "yes",
            "execution_mode": "docker",
            "docker_image": "rnaseq-workflow:tools",
            "docker_workspace": str(Path(".")),
        },
        dry_run=not bool(base_form["actual_run"]),
    )
    results = _run_step_with_tui_progress(
        selected,
        context,
        FeatureCountsStep(),
        title="featureCounts 定量",
        max_workers=int(base_form["max_workers"]),
    )
    matrix_path = _write_featurecounts_matrix_if_ready(results, output_dir)
    _capture_output(
        state,
        lambda console: _print_featurecounts_results(console, results, matrix_path),
        "featureCounts 结果",
    )


def _featurecounts_defaults_for_reference(asset: ReferenceAsset) -> dict[str, str]:
    provider = asset.provider.lower()
    annotation = str(asset.annotation or "").lower()
    species = (asset.species or "").lower()
    if "refseq" in provider or annotation.endswith(".gff") or annotation.endswith(".gff3") or "sars" in species:
        return {"feature_type": "gene", "attribute_type": "gene", "strandness": "0"}
    return {"feature_type": "exon", "attribute_type": "gene_id", "strandness": "0"}


def _samples_are_paired(samples: list[Sample]) -> bool:
    return any(sample.layout.value == "paired" or str(sample.metadata.get("library_layout", "")).upper() == "PAIRED" for sample in samples)


def _scan_bam_samples(input_dir: Path, project_id: str) -> list[Sample]:
    samples: list[Sample] = []
    for path in sorted(input_dir.rglob("*.bam")):
        if path.name.endswith(".bai"):
            continue
        sample_id = path.name[:-11] if path.name.endswith(".sorted.bam") else path.stem
        samples.append(
            Sample(
                sample_id=sample_id,
                source_path=path,
                source_paths=[path],
                project_id=project_id,
                metadata={
                    "input_type": "bam",
                    "size_bytes": path.stat().st_size if path.exists() else 0,
                },
            )
        )
    return samples


def _choose_featurecounts_target(samples: list[Sample]) -> list[Sample] | None:
    return _sample_multiselect("选择 BAM 样本", samples)


def _write_featurecounts_matrix_if_ready(results: list[StepResult], output_dir: Path) -> Path | None:
    count_tables = [
        result.outputs[0]
        for result in results
        if result.status == StepStatus.COMPLETED and result.outputs and result.outputs[0].exists()
    ]
    if len(count_tables) < 2:
        return None
    matrix_path = output_dir / "count_matrix.tsv"
    matrix = merge_featurecounts_files(count_tables)
    write_count_matrix_tsv(matrix, matrix_path)
    return matrix_path


def _print_featurecounts_results(console: Console, results: list[StepResult], matrix_path: Path | None) -> None:
    _print_step_results(console, results, title="featureCounts Results")
    if matrix_path:
        console.print(f"[green]Count matrix:[/green] {matrix_path}")


def _report_menu(state: TuiState) -> None:
    try:
        task = state.task
        form = _tool_run_wizard(
            "结果汇总",
            {
                "featurecounts_dir": task.task_output_dir if task else Path("runtime_logs") / "featurecounts_test",
                "project_id": "rnaseq_report",
                "reports_dir": task.reports_dir if task else Path("runtime_logs") / "featurecounts_test" / "reports",
            },
            [
                ("featurecounts_dir", "featureCounts 输出目录", "选择包含 .featureCounts.txt 的目录。", "path", None, ()),
                ("project_id", "项目 ID", "用于报告标题和元数据。", "str", None, ()),
                ("reports_dir", "报告输出目录", "矩阵、JSON 和 Markdown 报告会写入这里。", "path", None, ()),
            ],
        )
        if form is None:
            return
        featurecounts_dir = Path(form["featurecounts_dir"])
        project_id = str(form["project_id"] or "rnaseq_report")
        reports_dir = Path(form["reports_dir"])
        count_tables = _scan_featurecounts_tables(featurecounts_dir)
        if not count_tables:
            _message("未发现 featureCounts 表", f"{featurecounts_dir} 下没有 .featureCounts.txt 文件。")
            return
        matrix_path = reports_dir / "count_matrix.tsv"
        report_json = reports_dir / "report.json"
        report_markdown = reports_dir / "report.md"
        reports_dir.mkdir(parents=True, exist_ok=True)
        matrix = merge_featurecounts_files(count_tables)
        write_count_matrix_tsv(matrix, matrix_path)
        report = build_project_report(
            project_id=project_id or "rnaseq_report",
            output_dir=featurecounts_dir,
            state_path=featurecounts_dir / "progress.json",
            counts_matrix_path=matrix_path,
            artifact_paths=[matrix_path, *count_tables],
        )
        write_report_json(report, report_json)
        write_report_markdown(report, report_markdown)
    except Exception as exc:
        _message("报告生成失败", str(exc))
        return
    output = _capture_output(
        state,
        lambda console: _print_report_outputs(console, count_tables, matrix_path, report_json, report_markdown, report),
        "结果汇总/报告",
    )
    if output is None or not output.strip():
        _message("结果汇总完成", f"已写入:\n{matrix_path}\n{report_json}\n{report_markdown}")
    else:
        _message(
            "结果汇总完成",
            "\n".join(
                [
                    f"featureCounts tables: {len(count_tables)}",
                    f"samples: {len(matrix.sample_ids)}",
                    f"genes: {len(matrix.gene_ids)}",
                    f"count matrix: {matrix_path}",
                    f"report json: {report_json}",
                    f"report markdown: {report_markdown}",
                ]
            ),
        )


def _scan_featurecounts_tables(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*.featureCounts.txt")
        if path.is_file() and not path.name.endswith(".summary")
    )


def _print_report_outputs(
    console: Console,
    count_tables: list[Path],
    matrix_path: Path,
    report_json: Path,
    report_markdown: Path,
    report,
) -> None:
    table = Table(title="结果汇总")
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("featureCounts tables", str(len(count_tables)))
    table.add_row("matrix samples", str(report.counts_matrix.sample_count if report.counts_matrix else 0))
    table.add_row("matrix genes", str(report.counts_matrix.gene_count if report.counts_matrix else 0))
    table.add_row("output exists", str(matrix_path.exists() and report_json.exists() and report_markdown.exists()))
    table.add_row("count matrix", str(matrix_path))
    table.add_row("report json", str(report_json))
    table.add_row("report markdown", str(report_markdown))
    console.print(table)


def _parse_hisat2_alignment_rate(log_path: Path) -> float | None:
    if not log_path.exists():
        return None
    for line in log_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if "overall alignment rate" not in line:
            continue
        raw = line.strip().split("%", 1)[0]
        try:
            return float(raw)
        except ValueError:
            return None
    return None


def _choose_fastqc_target(samples: list[Sample]) -> list[Sample] | None:
    return _sample_multiselect("选择 FastQC 样本", samples)


def _sample_multiselect(title: str, samples: list[Sample]) -> list[Sample] | None:
    values = [(sample.sample_id, _sample_label(sample)) for sample in samples]
    if _use_line_dialogs():
        selected_ids = _line_multiselect(title, values, default_values=[sample.sample_id for sample in samples])
        if selected_ids is None:
            return None
        selected = set(selected_ids)
        selected_samples = [sample for sample in samples if sample.sample_id in selected]
        if len(_sample_metadata_groups(selected_samples)) > 1 and not _yes_no(
            "选择中包含多个元数据分组，仍继续？",
            False,
        ):
            return []
        return selected_samples
    selected_ids = checkboxlist_dialog(
        title=HTML(f"<b><ansicyan>{title}</ansicyan></b>"),
        text=_dialog_text("空格勾选或取消。", include_multiselect=True),
        values=[(value, HTML(_escape_html(label))) for value, label in values],
        default_values=[sample.sample_id for sample in samples],
        ok_text="确认 Enter",
        cancel_text="返回 Esc",
        style=STYLE,
    ).run()
    if selected_ids is None:
        return None
    selected = set(selected_ids)
    selected_samples = [sample for sample in samples if sample.sample_id in selected]
    if len(_sample_metadata_groups(selected_samples)) > 1 and not _yes_no(
        "选择中包含多个元数据分组，仍继续？",
        False,
    ):
        return []
    return selected_samples


def _sample_label(sample: Sample) -> str:
    extras = []
    if sample.metadata.get("scientific_name"):
        extras.append(str(sample.metadata["scientific_name"]))
    if sample.metadata.get("bioproject"):
        extras.append(str(sample.metadata["bioproject"]))
    if "alignment_rate" in sample.metadata and sample.metadata["alignment_rate"] is not None:
        extras.append(f"align={sample.metadata['alignment_rate']:.2f}%")
    if "size_bytes" in sample.metadata:
        extras.append(f"size={_format_bytes(sample.metadata['size_bytes'])}")
    suffix = "  " + "  ".join(extras) if extras else ""
    return f"{sample.sample_id}  {sample.layout.value}  files={len(sample.source_paths)}{suffix}"


def _sample_metadata_groups(samples: list[Sample]) -> set[tuple[str, str, str, str, str]]:
    groups: set[tuple[str, str, str, str, str]] = set()
    for sample in samples:
        metadata = sample.metadata
        key = (
            str(metadata.get("taxid", "")),
            str(metadata.get("scientific_name", "")),
            str(metadata.get("bioproject", "")),
            str(metadata.get("library_layout", "")),
            str(metadata.get("library_source", "")),
        )
        if any(key):
            groups.add(key)
    return groups


def _run_step_with_tui_progress(
    samples: list[Sample],
    context: RunContext,
    step,
    title: str,
    max_workers: int = DEFAULT_TUI_CONCURRENCY,
) -> list[StepResult]:
    status_area = TextArea(
        text=_step_progress_text(samples, {}, context, title, max_workers, done=False),
        read_only=True,
        scrollbar=True,
        focusable=False,
        wrap_lines=False,
    )
    kb = KeyBindings()
    result_holder = {"results": [], "error": None, "done": False}
    statuses: dict[str, str] = {sample.sample_id: "PENDING" for sample in samples}
    cancel_token = CancellationToken()
    context.config["cancellation_token"] = cancel_token
    started_at = time.monotonic()

    @kb.add("c")
    def _cancel(event) -> None:
        cancel_token.cancel()
        for sample in samples:
            if statuses.get(sample.sample_id) in {"PENDING", "QUEUED"}:
                statuses[sample.sample_id] = StepStatus.CANCELLED.value
        status_area.text = _step_progress_text(samples, statuses, context, title, max_workers, done=False, elapsed=time.monotonic() - started_at)
        event.app.invalidate()

    @kb.add("q")
    def _quit_if_done(event) -> None:
        if result_holder["done"]:
            event.app.exit()

    def worker() -> None:
        results: list[StepResult] = []
        try:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                pending = list(samples)
                futures = {}
                while pending or futures:
                    while pending and len(futures) < max_workers and not cancel_token.is_cancelled():
                        sample = pending.pop(0)
                        statuses[sample.sample_id] = "QUEUED"
                        futures[executor.submit(_run_step_for_sample, step, sample, context, statuses, cancel_token)] = sample
                    if cancel_token.is_cancelled():
                        for sample in pending:
                            statuses[sample.sample_id] = StepStatus.CANCELLED.value
                            results.append(
                                StepResult(
                                    sample_id=sample.sample_id,
                                    step_id=step.step_id,
                                    status=StepStatus.CANCELLED,
                                    message="cancelled before start",
                                    inputs=sample.source_paths,
                                )
                            )
                        pending = []
                    if not futures:
                        break
                    done, _ = wait(futures, return_when=FIRST_COMPLETED)
                    for future in done:
                        sample = futures.pop(future)
                        try:
                            result = future.result()
                        except BaseException as exc:
                            result = StepResult(
                                sample_id=sample.sample_id,
                                step_id=step.step_id,
                                status=StepStatus.FAILED,
                                message=str(exc),
                                inputs=sample.source_paths,
                            )
                        statuses[sample.sample_id] = result.status.value
                        results.append(result)
        except BaseException as exc:
            result_holder["error"] = exc
        finally:
            results.sort(key=lambda result: result.sample_id)
            result_holder["results"] = results
            result_holder["done"] = True

    app = Application(
        layout=Layout(
            Box(
                Frame(
                    HSplit([status_area, Label(text=""), Label(text="按 c 取消。完成后按 q 返回。")]),
                    title=title,
                ),
                padding=1,
            )
        ),
        key_bindings=kb,
        style=STYLE,
        full_screen=True,
    )

    def refresher() -> None:
        while not result_holder["done"]:
            status_area.text = _step_progress_text(samples, statuses, context, title, max_workers, done=False, elapsed=time.monotonic() - started_at)
            app.invalidate()
            time.sleep(0.5)
        status_area.text = _step_progress_text(samples, statuses, context, title, max_workers, done=True, elapsed=time.monotonic() - started_at)
        app.invalidate()

    threading.Thread(target=worker, daemon=True).start()
    threading.Thread(target=refresher, daemon=True).start()
    app.run()
    if result_holder["error"]:
        raise result_holder["error"]
    return result_holder["results"]


def _run_step_for_sample(step, sample: Sample, context: RunContext, statuses: dict[str, str], cancel_token: CancellationToken) -> StepResult:
    if cancel_token.is_cancelled():
        return StepResult(
            sample_id=sample.sample_id,
            step_id=step.step_id,
            status=StepStatus.CANCELLED,
            message="cancelled before start",
            inputs=sample.source_paths,
        )
    statuses[sample.sample_id] = StepStatus.RUNNING.value
    try:
        step.validate_inputs(sample, context)
        return step.run(sample, context)
    except (FileNotFoundError, ValueError) as exc:
        return StepResult(
            sample_id=sample.sample_id,
            step_id=step.step_id,
            status=StepStatus.FAILED,
            message=str(exc),
            inputs=sample.source_paths,
        )


def _step_progress_text(
    samples: list[Sample],
    statuses: dict[str, str],
    context: RunContext,
    title: str,
    max_workers: int,
    done: bool,
    elapsed: float = 0.0,
) -> str:
    completed = sum(1 for status in statuses.values() if status == StepStatus.COMPLETED.value)
    failed = sum(1 for status in statuses.values() if status == StepStatus.FAILED.value)
    cancelled = sum(1 for status in statuses.values() if status == StepStatus.CANCELLED.value)
    running = sum(1 for status in statuses.values() if status == StepStatus.RUNNING.value)
    lines = [
        title,
        f"模式: {'dry-run' if context.dry_run else '实际运行'}",
        f"实时总并发: {max_workers}",
        f"输出目录: {context.output_dir}",
        f"进度: completed={completed} failed={failed} cancelled={cancelled} running={running} total={len(samples)} elapsed={elapsed:.0f}s",
        "",
    ]
    for sample in samples:
        status = statuses.get(sample.sample_id, "PENDING")
        output_dir = _step_output_dir(title, context.output_dir, sample)
        activity = _sample_activity_text(status, output_dir, sample.source_paths)
        lines.append(
            f"{sample.sample_id}: {status}  "
            f"{sample.layout.value}  files={len(sample.source_paths)}{activity}"
        )
    if done:
        lines.append("")
        lines.append(_run_done_message(cancelled=cancelled, failed=failed))
    return "\n".join(lines)


def _sample_activity_text(status: str, output_dir: Path, input_paths: list[Path]) -> str:
    input_size = _paths_size(input_paths)
    size, idle_seconds, latest_name = _output_activity(output_dir)
    if status == StepStatus.RUNNING.value:
        if output_dir.exists():
            last = latest_name or "(no files yet)"
            return f" input={_format_bytes(input_size)} output={_format_bytes(size)} idle={idle_seconds:.0f}s last={last}"
        return f" input={_format_bytes(input_size)} output=(waiting)"
    if status in {StepStatus.SKIPPED.value, StepStatus.COMPLETED.value}:
        done = "done=yes" if (output_dir / ".done.json").exists() else "done=no"
        return f" input={_format_bytes(input_size)} output={_format_bytes(size)} {done}"
    if status in {StepStatus.FAILED.value, StepStatus.CANCELLED.value}:
        last = latest_name or "(no files)"
        return f" input={_format_bytes(input_size)} output={_format_bytes(size)} last={last}"
    return f" input={_format_bytes(input_size)} output=(waiting)"


def _step_output_dir(title: str, root: Path, sample: Sample) -> Path:
    sample_root = Path(root) / "samples" / sample.sample_id
    if "Trim" in title:
        return sample_root / "trimmed_fastq"
    if "FastQC" in title:
        return sample_root / "qc_raw"
    if "SRA" in title:
        return sample_root / "raw_fastq"
    if "HISAT2" in title or "Samtools" in title:
        return sample_root / "alignment"
    return sample_root


def _output_activity(output_dir: Path) -> tuple[int, float, str]:
    if not output_dir.exists():
        return 0, 0.0, ""
    files = [path for path in output_dir.rglob("*") if path.is_file() and not _is_internal_progress_file(path)]
    if not files:
        return 0, 0.0, ""
    size = sum(path.stat().st_size for path in files)
    latest = max(files, key=lambda path: path.stat().st_mtime)
    idle_seconds = max(time.time() - latest.stat().st_mtime, 0.0)
    return size, idle_seconds, latest.name


def _is_internal_progress_file(path: Path) -> bool:
    name = path.name
    if name in {".done.json", ".lock", ".error.txt"}:
        return True
    return name.endswith((".vdb_validate.json", ".source.txt"))


def _paths_size(paths: list[Path]) -> int:
    total = 0
    for path in paths:
        try:
            if path.exists() and path.is_file():
                total += path.stat().st_size
        except OSError:
            continue
    return total


def _print_step_results(console: Console, results: list[StepResult], title: str) -> None:
    table = Table(title=title)
    table.add_column("Sample")
    table.add_column("Status")
    table.add_column("Return")
    table.add_column("Output")
    table.add_column("Message")
    for result in results:
        status_style = {
            StepStatus.COMPLETED: "green",
            StepStatus.FAILED: "red",
            StepStatus.CANCELLED: "yellow",
            StepStatus.SKIPPED: "cyan",
        }.get(result.status, "white")
        table.add_row(
            result.sample_id,
            f"[{status_style}]{result.status.value}[/{status_style}]",
            "" if result.return_code is None else str(result.return_code),
            "; ".join(str(path) for path in result.outputs),
            _tail(result.message, 800),
        )
    console.print(table)


def _tail(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[-limit:]


def _run_workflow(state: TuiState) -> None:
    cfg = _load_config(state)
    if not cfg:
        return
    form = _tool_run_wizard(
        "旧 workflow",
        {
            "actual_run": False,
            "max_workers": 1,
        },
        [
            ("actual_run", "实际运行 workflow", "关闭时只做 dry-run。建议先 dry-run 检查配置。", "bool", None, ()),
            ("max_workers", "样本并发数", "旧 workflow 同时处理的样本数量。", "int", 1, ()),
        ],
    )
    if form is None:
        return
    dry_run = not bool(form["actual_run"])
    validation = validate_project_config(cfg, check_files=not dry_run)
    if not validation.ok:
        _capture_output(state, lambda console: print_validation_result(console, validation), "配置校验失败")
        return
    samples = samples_from_config(cfg.samples, cfg.project_id)
    steps = build_pipeline_steps(cfg.steps)
    _capture_output(state, lambda console: print_run_start(console, cfg, samples, [step.step_id for step in steps], dry_run), "运行开始")
    context = RunContext(cfg.project_id, cfg.work_dir, cfg.output_dir, cfg.settings, dry_run=dry_run)
    executor = LocalExecutor(
        Pipeline(steps=steps, repository=JsonStateRepository(cfg.output_dir / "progress.json")),
        max_workers=int(form["max_workers"]),
    )
    run_executor_with_progress(state.console, executor, samples, context)
    _capture_output(state, lambda console: print_run_summary(console, cfg.output_dir / "progress.json"), "运行摘要")


def _change_cwd(state: TuiState) -> None:
    path = _path_input("新的工作目录", Path.cwd(), must_exist=True, directory=True)
    if path:
        os.chdir(path)
        _message("完成", f"当前目录: {Path.cwd()}")


def _load_config(state: TuiState):
    try:
        return load_project_config(state.config)
    except Exception as exc:
        _message("Config 错误", str(exc))
        return None


def _show_references(console: Console, assets: list[ReferenceAsset]) -> None:
    if not assets:
        console.print("[yellow]没有已登记 reference[/yellow]")
        return
    for asset in assets:
        owner = "共享"
        parts = asset.root.parts
        if "users" in parts:
            idx = parts.index("users")
            if idx + 1 < len(parts):
                owner = parts[idx + 1]
        console.print(f"[cyan]{asset.reference_id}[/cyan]  [dim]{asset.provider}/{asset.build_status}[/dim]")
        console.print(f"  来源: {asset.provider}")
        console.print(f"  拥有者: {owner}")
        console.print(f"  状态: {asset.build_status}")
        console.print(f"  说明: {asset.notes or '无'}")


def _browse_references(reference_dir: Path, select_mode: bool = False) -> ReferenceAsset | None:
    assets = list_references(reference_dir)
    candidates = [("", reference_dir, asset) for asset in assets]
    selected = _browse_reference_candidates(candidates, select_mode=select_mode)
    return selected[2] if selected else None


def _browse_reference_candidates(
    candidates: list[tuple[str, Path, ReferenceAsset]],
    current_reference_id: str = "",
    select_mode: bool = False,
    page_size: int = 10,
) -> tuple[str, Path, ReferenceAsset] | None:
    query = ""
    page = 0
    while True:
        filtered = [
            row
            for row in candidates
            if not query
            or query.lower() in row[2].reference_id.lower()
            or query.lower() in (row[2].species or "").lower()
            or query.lower() in (row[2].notes or "").lower()
        ]
        if not filtered:
            values = [("search", "搜索"), ("back", "返回")]
            selected = _menu("Reference", f"没有匹配项。关键词: {query or '无'}", values)
            if selected == "search":
                query = _input("搜索 reference", "输入名称、物种或描述关键词。", query) or ""
                page = 0
                continue
            return None
        page_count = max(1, (len(filtered) + page_size - 1) // page_size)
        page = max(0, min(page, page_count - 1))
        start = page * page_size
        rows = filtered[start : start + page_size]
        values: list[tuple[str, str]] = []
        for offset, (_scope, _reference_dir, asset) in enumerate(rows, start=1):
            marker = "* " if asset.reference_id == current_reference_id else ""
            values.append((str(start + offset - 1), f"{start + offset}. {marker}{asset.reference_id}"))
        if page > 0:
            values.append(("prev", "上一页"))
        if page < page_count - 1:
            values.append(("next", "下一页"))
        values.append(("search", "搜索"))
        values.append(("back", "返回"))
        selected = _menu(
            "Reference",
            f"共 {len(filtered)} 个资产，第 {page + 1}/{page_count} 页。关键词: {query or '无'}",
            values,
        )
        if selected in (None, "back"):
            return None
        if selected == "prev":
            page -= 1
            continue
        if selected == "next":
            page += 1
            continue
        if selected == "search":
            query = _input("搜索 reference", "输入名称、物种或描述关键词。", query) or ""
            page = 0
            continue
        row = filtered[int(selected)]
        detail_choice = _menu(
            row[2].reference_id,
            _reference_asset_detail_text(row[0], row[1], row[2]),
            [("use", "使用此资产"), ("back", "返回列表")],
        )
        if detail_choice == "use":
            return row


def _reference_asset_detail_text(scope: str, reference_dir: Path, asset: ReferenceAsset) -> str:
    report = check_reference_asset(asset)
    issues = "\n".join(f"- {issue.level}: {issue.field} {issue.message}" for issue in report.issues) or "无"
    owner = scope or "公共资产"
    return "\n".join(
        [
            f"名称: {asset.reference_id}",
            f"资产库: {owner}",
            f"目录: {reference_dir}",
            f"来源: {asset.provider}",
            f"物种: {asset.species or '未记录'}",
            f"状态: {asset.build_status}",
            f"描述: {asset.notes or '无'}",
            "",
            f"FASTA: {asset.fasta}",
            f"注释: {asset.annotation or '未登记'}",
            f"HISAT2 index: {asset.hisat2_index}",
            "",
            f"检查: {'通过' if report.ok else '需要处理'}",
            issues,
        ]
    )


def _show_reference_dialog(reference_dir: Path, reference_id: str) -> None:
    try:
        asset = load_reference(reference_id, reference_dir)
    except FileNotFoundError as exc:
        _message("错误", str(exc))
        return
    _message(
        "Reference",
        _workflow_reference_detail_text(asset),
    )


def _menu(title: str, text: str, values: list[tuple[str, str]]) -> str | None:
    if _use_line_dialogs():
        return _line_menu(title, text, values)
    return _keyboard_menu(title, text, values)


def _input(title: str, text: str, default: str = "") -> str | None:
    if _use_line_dialogs():
        return _line_input(title, text, default)
    title, text = _friendly_field(title, text)
    return _text_input_dialog(title, text, default=default, password=False)


def _password_input(title: str, text: str) -> str | None:
    if _use_line_dialogs():
        return _line_password_input(title, text)
    title, text = _friendly_field(title, text)
    return _text_input_dialog(title, text, default="", password=True)


def _text_input_dialog(title: str, text: str, default: str = "", password: bool = False) -> str | None:
    def accept_buffer(_buffer) -> bool:
        from prompt_toolkit.application.current import get_app

        get_app().exit(result=text_area.text)
        return True

    text_area = TextArea(
        text=default,
        multiline=False,
        password=password,
        accept_handler=accept_buffer,
        width=Dimension(preferred=64),
        height=1,
        dont_extend_height=True,
        style="class:input",
        prompt=[("class:menu.border", "> ")],
    )

    button_control = _dialog_button_control(lambda: text_area.text)

    dialog = Dialog(
        title=HTML(f"<b><ansicyan>{title}</ansicyan></b>"),
        body=HSplit(
            [
                Box(
                    Frame(text_area, title=HTML("<ansicyan>输入</ansicyan>"), width=Dimension(preferred=68)),
                    padding_top=0,
                    padding_bottom=1,
                ),
                Label(text=f"说明: {_short_hint(text)}"),
                Window(content=button_control, always_hide_cursor=True, height=1, dont_extend_height=True, align=WindowAlign.CENTER),
            ],
            padding=1,
        ),
        buttons=[],
        width=Dimension(min=72, preferred=82, max=90),
        with_background=True,
    )

    kb = KeyBindings()

    @kb.add("escape")
    @kb.add("c-c")
    def _cancel(event) -> None:
        event.app.exit(result=None)

    app = Application(
        layout=Layout(dialog, focused_element=text_area),
        key_bindings=kb,
        style=STYLE,
        mouse_support=True,
        full_screen=True,
    )
    return app.run()


def _multiline_input(title: str, text: str, default: str = "", completer=None) -> str | None:
    if _use_line_dialogs():
        return _line_multiline_input(title, text, default)

    result = {"value": None}
    text_area = TextArea(
        text=default,
        multiline=True,
        wrap_lines=False,
        scrollbar=True,
        width=Dimension(preferred=84),
        height=Dimension(preferred=14),
        style="class:input",
        completer=completer,
    )

    button_control = _dialog_button_control(lambda: text_area.text, accept_label="确认 F2/Ctrl+S")

    dialog = Dialog(
        title=HTML(f"<b><ansicyan>{title}</ansicyan></b>"),
        body=HSplit(
            [
                text_area,
                Label(text=f"说明: {_short_hint(text)}"),
                Label(text="Enter 会换行。"),
                Window(content=button_control, always_hide_cursor=True, height=1, dont_extend_height=True, align=WindowAlign.CENTER),
            ],
            padding=1,
        ),
        buttons=[],
        width=Dimension(min=72, preferred=92, max=100),
        with_background=True,
    )

    kb = KeyBindings()

    @kb.add("f2")
    @kb.add("c-s")
    def _accept(event) -> None:
        result["value"] = text_area.text
        event.app.exit(result=result["value"])

    @kb.add("escape")
    @kb.add("c-c")
    def _cancel(event) -> None:
        event.app.exit(result=None)

    app = Application(
        layout=Layout(dialog, focused_element=text_area),
        key_bindings=kb,
        style=STYLE,
        mouse_support=True,
        full_screen=True,
    )
    return app.run()


def _path_input(
    title: str,
    default: Path | str | None = None,
    must_exist: bool = False,
    directory: bool = False,
) -> Path | None:
    friendly_title, friendly_text = _friendly_field(title, "输入路径。")
    raw = _input(friendly_title, friendly_text, "" if default is None else str(default))
    if raw is None or raw.strip() == "":
        return None
    path = Path(raw.strip())
    if must_exist and not path.exists():
        _message("路径不存在", str(path))
        return None
    if directory and path.exists() and not path.is_dir():
        _message("不是目录", str(path))
        return None
    return path


def _friendly_field(title: str, text: str = "") -> tuple[str, str]:
    key = str(title).strip()
    mapping = {
        "docker_workspace": ("Docker 工作目录", "容器可访问的项目目录。默认 . 表示当前目录。"),
        "docker_image": ("Docker 镜像", "包含流程工具的容器镜像。"),
        "工具镜像": ("Docker 镜像", "包含流程工具的容器镜像。"),
        "execution_mode": ("执行方式", "Docker 更稳定；Local 使用本机工具。"),
        "download_source": ("下载来源", "Auto 会优先选择可用来源。"),
        "download max_size": ("下载大小上限", "SRA Toolkit 的 max-size 参数。"),
        "download_proxy": ("下载代理", "仅下载阶段使用。留空表示直连；需要代理时填写本机代理地址。"),
        "max_size": ("下载大小上限", "可使用 5G、20G 等格式。"),
        "project_id": ("项目 ID", "用于报告、日志和输出命名。"),
        "project_id，可留空": ("项目 ID", "留空时使用默认名称。"),
        "reference_id": ("Reference ID", "参考资产的唯一名称。"),
        "species": ("物种名称", "使用来源数据库接受的物种名。"),
        "division": ("Ensembl 分库", "植物选择 plants；动物常用 vertebrates。"),
        "release": ("版本", "可使用 current 或指定版本。"),
        "provider": ("来源", "记录参考文件来源。"),
        "annotation_provider": ("注释来源", "默认与参考来源一致。"),
        "accession": ("SRA 编号", "输入一个 SRR、ERR 或 DRR 编号。"),
        "feature type (-t)": ("featureCounts 特征类型", "GTF 常用 exon；GFF 可按注释选择 gene。"),
        "attribute type (-g)": ("featureCounts 属性字段", "GTF 常用 gene_id；部分 GFF 使用 gene 或 ID。"),
        "featureCounts -t": ("featureCounts 特征类型", "GTF 常用 exon；GFF 可按注释选择 gene。"),
        "featureCounts -g": ("featureCounts 属性字段", "GTF 常用 gene_id；部分 GFF 使用 gene 或 ID。"),
        "featureCounts -s": ("链特异性", "0 非链特异，1 正向，2 反向。"),
        "下载并发数": ("下载并发数", "同时下载的样本数量。网络不稳定时使用 1 到 2；链路稳定时再提高。"),
        "工作流样本并发数": ("工作流样本并发数", "同时处理的样本数量。值越大占用 CPU、内存和磁盘 I/O 越多。"),
        "样本并发数": ("样本并发数", "同时处理的样本数量。值越大占用 CPU、内存和磁盘 I/O 越多；默认通常足够。"),
        "fasterq-dump 线程数": ("SRA 转 FASTQ 线程数", "单个样本转换时使用的线程数。提高后会更快，也会增加临时磁盘和 CPU 占用。"),
        "FastQC 线程数": ("FastQC 线程数", "单个 FastQC 任务使用的线程数。FastQC 通常不需要很高，2 到 4 较稳妥。"),
        "Trim quality": ("修剪质量阈值", "Trim Galore 去除低质量碱基的阈值。20 是常用默认值；更高会更严格。"),
        "quality": ("修剪质量阈值", "Trim Galore 去除低质量碱基的阈值。20 是常用默认值；更高会更严格。"),
        "Trim Galore cores": ("Trim Galore 核心数", "单个样本修剪时使用的核心数。该值过高会明显增加内存和 I/O 压力。"),
        "HISAT2 线程数": ("HISAT2 线程数", "单个样本比对时使用的线程数。提高后通常更快，但会增加 CPU 占用。"),
        "Samtools 线程数": ("Samtools 线程数", "BAM 排序和索引时使用的线程数。排序阶段也会占用较多磁盘 I/O。"),
        "samtools sort 线程数": ("Samtools 排序线程数", "BAM 排序使用的线程数。提高后更快，也会增加内存和磁盘 I/O。"),
        "featureCounts 线程数": ("featureCounts 线程数", "定量计数使用的线程数。通常 2 到 4 足够，过高收益有限。"),
        "链特异性": ("链特异性", "featureCounts 的 -s 参数。0 非链特异，1 正向，2 反向；不确定时先用 0。"),
        "按片段计数 paired reads": ("按片段计数", "paired-end 数据通常开启。开启后 featureCounts 以 read pair 作为一个片段计数。"),
        "使用 paired fragments (-p)": ("按片段计数", "paired-end 数据通常开启。开启后 featureCounts 以 read pair 作为一个片段计数。"),
        "失败重试次数": ("失败重试次数", "下载失败后的自动重试次数。网络不稳定时可设为 1 到 3。"),
        "清单并发数": ("下载并发数", "同时下载的目标数量。值越大对网络和磁盘压力越高。"),
        "hisat2-build 线程数": ("HISAT2 建索引线程数", "构建索引时使用的线程数。植物大基因组会占用较多内存。"),
        "线程数": ("线程数", "当前工具使用的线程数量。提高后可能更快，也会占用更多资源。"),
    }
    return mapping.get(key, (title, text))


def _dialog_button_control(get_accept_value: Callable[[], str | None], accept_label: str = "确认 Enter") -> FormattedTextControl:
    def mouse_handler(action: str):
        def handle(mouse_event: MouseEvent) -> None:
            if mouse_event.event_type == MouseEventType.MOUSE_UP:
                from prompt_toolkit.application.current import get_app

                if action == "accept":
                    get_app().exit(result=get_accept_value())
                else:
                    get_app().exit(result=None)

        return handle

    def render_buttons():
        return FormattedText(
            [
                ("class:menu.border", f"< {accept_label} >", mouse_handler("accept")),
                ("class:dialog.body", " "),
                ("class:menu.border", "< 返回 Esc >", mouse_handler("cancel")),
            ]
        )

    return FormattedTextControl(render_buttons, focusable=False)


def _int_input(
    title: str,
    default: int,
    minimum: int | None = None,
    cancel_returns_default: bool = True,
) -> int | None:
    while True:
        friendly_title, friendly_text = _friendly_field(title, "请输入整数。")
        raw = _input(friendly_title, friendly_text, str(default))
        if raw is None:
            return default if cancel_returns_default else None
        try:
            value = int(raw)
        except ValueError:
            _message("输入错误", "请输入整数")
            continue
        if minimum is not None and value < minimum:
            _message("输入错误", f"不能小于 {minimum}")
            continue
        return value


def _yes_no(title: str, default: bool, cancel_returns_default: bool = True) -> bool | None:
    if _use_line_dialogs():
        return _line_yes_no(title, default)

    result = {"value": None}

    def exit_with(value: bool | None) -> None:
        from prompt_toolkit.application.current import get_app

        result["value"] = value
        get_app().exit(result=value)

    def mouse_handler(value: bool | None):
        def handle(mouse_event: MouseEvent) -> None:
            if mouse_event.event_type == MouseEventType.MOUSE_UP:
                exit_with(value)

        return handle

    def render_buttons():
        yes_label = "< 是 Enter >" if default else "< 是 Y >"
        no_label = "< 否 N >" if default else "< 否 Enter >"
        return FormattedText(
            [
                ("class:menu.border", yes_label, mouse_handler(True)),
                ("class:dialog.body", " "),
                ("class:menu.border", no_label, mouse_handler(False)),
                ("class:dialog.body", " "),
                ("class:menu.border", "< 返回 Esc >", mouse_handler(None)),
            ]
        )

    button_control = FormattedTextControl(render_buttons, focusable=False)
    dialog = Dialog(
        title=HTML(f"<b><ansicyan>{title}</ansicyan></b>"),
        body=HSplit(
            [
                Label(text=f"默认: {'是' if default else '否'}"),
                Window(content=button_control, always_hide_cursor=True, height=1, dont_extend_height=True, align=WindowAlign.CENTER),
            ],
            padding=1,
        ),
        buttons=[],
        width=Dimension(min=54, preferred=64, max=72),
        with_background=True,
    )

    kb = KeyBindings()

    @kb.add("enter")
    def _accept_default(event) -> None:
        exit_with(default)

    @kb.add("y")
    def _accept_yes(event) -> None:
        exit_with(True)

    @kb.add("n")
    def _accept_no(event) -> None:
        exit_with(False)

    @kb.add("escape")
    @kb.add("c-c")
    def _cancel(event) -> None:
        exit_with(None)

    app = Application(
        layout=Layout(dialog),
        key_bindings=kb,
        style=STYLE,
        mouse_support=True,
        full_screen=True,
    )
    result_value = app.run()
    result["value"] = result_value
    if result_value is None:
        return default if cancel_returns_default else None
    return bool(result_value)


def _confirm_yes(title: str, default: bool = True) -> bool:
    try:
        return _yes_no(title, default, cancel_returns_default=False) is True
    except TypeError:
        return _yes_no(title, default) is True


def _message(title: str, text: str) -> None:
    if _use_line_dialogs():
        _line_message(title, text)
        return
    message_dialog(
        title=HTML(f"<b><ansicyan>{title}</ansicyan></b>"),
        text=_dialog_text(text, include_multiselect=False),
        ok_text="确认 Enter",
        style=STYLE,
    ).run()


def _show_recent_output(state: TuiState) -> None:
    _message("最近输出", state.output_log or "暂无输出")


def _capture_output(state: TuiState, render: Callable[[Console], None], title: str) -> str:
    buffer = StringIO()
    capture_console = Console(file=buffer, force_terminal=False, width=110, color_system=None)
    render(capture_console)
    output = buffer.getvalue().strip()
    if not output:
        output = "(无输出)"
    state.output_log = output
    _message(title, _truncate_output(output))
    return output


def _truncate_output(output: str, limit: int = 12000) -> str:
    if len(output) <= limit:
        return output
    return output[-limit:]


def _pause_dialog() -> None:
    if _use_line_dialogs():
        input("按 Enter 继续...")
        return
    button_dialog(
        title=HTML("<b><ansicyan>继续</ansicyan></b>"),
        text=_dialog_text("查看上方输出后继续", include_multiselect=False),
        buttons=[("继续 Enter", True)],
        style=STYLE,
    ).run()


def _dialog_text(text: str, include_multiselect: bool) -> HTML:
    hint = "Space 勾选" if include_multiselect else ""
    escaped = _escape_html(text)
    if hint:
        return HTML(f"{escaped}\n\n<ansiyellow>{hint}</ansiyellow>")
    return HTML(escaped)


def _escape_html(text: str) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _short_hint(text: str, limit: int = 86) -> str:
    line = " ".join(str(text).strip().split())
    if len(line) <= limit:
        return line
    return line[: limit - 1].rstrip() + "..."


def _use_line_dialogs() -> bool:
    mode = os.environ.get(LINE_MODE_ENV, "").strip().lower()
    if mode in {"line", "plain", "1", "true"}:
        return True
    if mode in {"dialog", "prompt_toolkit", "0", "false"}:
        return False
    return False


def _line_menu(title: str, text: str, values: list[tuple[str, str]]) -> str | None:
    console = Console()
    table = Table(title=f"[cyan]{title}[/cyan]")
    table.add_column("Key", justify="right")
    table.add_column("Option")
    for index, (_value, label) in enumerate(values, start=1):
        table.add_row(str(index), str(label))
    table.add_row("0", "返回/取消")
    console.print(table)
    if text:
        console.print(f"[dim]{text}[/dim]")
    while True:
        raw = input("选择编号后按 Enter: ").strip()
        if raw in {"", "0", "q", "Q"}:
            return None
        try:
            index = int(raw)
        except ValueError:
            console.print("[red]请输入编号。[/red]")
            continue
        if 1 <= index <= len(values):
            return values[index - 1][0]
        console.print("[red]编号超出范围。[/red]")


def _menu_dialog_width(text: str, values: list[tuple[str, str]]) -> int:
    status_text, _hint_text = _split_menu_text(text)
    content_lines = [line for line in status_text.splitlines() if line.strip()]
    label_widths = [get_cwidth(str(label)) + 12 for _value, label in values]
    widest = max([44, *[get_cwidth(line) for line in content_lines], *label_widths])
    return max(56, min(88, widest + 8))


def _menu_list_width(values: list[tuple[str, str]]) -> int:
    widest = max([0, *[get_cwidth(str(label)) for _value, label in values]])
    return min(52, widest + 8)


def _split_menu_text(text: str) -> tuple[str, str]:
    raw = str(text or "").strip()
    if not raw:
        return "", ""
    lines = [line for line in raw.splitlines() if line.strip()]
    if not lines:
        return "", ""
    status_markers = ("[", "登录:", "任务:", "配置:", "目录:", "文件:", "project_id:", "asset_root:", "execution_mode:", "samples:")
    if any(line.strip().startswith(status_markers) for line in lines) or len(lines) >= 3:
        return raw, ""
    return "", raw


def _menu_item_hint(label: object, fallback: str = "") -> str:
    text = str(label)
    head = text.split("  ", 1)[0].strip()
    hints = {
        "环境检查 doctor": "检查本机环境、Docker 与常用工具。",
        "用户与任务管理": "管理登录状态、任务和任务目录。",
        "基础配置": "配置项目、执行环境、样本和参考文件。",
        "Workflow": "按任务完成清单、参数、检查和运行。",
        "Reference": "管理参考基因组、注释和 HISAT2 索引。",
        "一条龙下载 FASTA+GTF 并构建 index": "从 Ensembl 或 URL 获取 FASTA/GTF，并生成 HISAT2 index，适合新物种或新版本。",
        "浏览 reference": "查看我的资产或公共资产。列表只显示名称，进入后查看来源、物种、索引和检查结果。",
        "登记本地 FASTA/GTF": "把已有本地 FASTA、GTF/GFF 或 HISAT2 index 登记为可复用资产，可复制入库。",
        "构建 HISAT2 index": "对已登记的 FASTA 运行 hisat2-build，生成后续比对使用的 index prefix。",
        "检查 reference 资产": "检查 FASTA、注释文件和 HISAT2 index 是否存在且非空，并清理失效记录。",
        "写入当前 config": "把选中的 reference 路径写入传统 config.yaml，主要用于旧 CLI/调试流程。",
        "清理失效 reference 记录": "移除文件已丢失或索引不完整的 reference 记录，避免列表显示不可用资产。",
        "工具调试": "单独运行某一步，用于排查问题。",
        "查看最近输出": "查看上一次命令或检查结果。",
        "退出": "关闭终端工作台。",
        "登录/注册用户": "进入账号登录或注册。",
        "任务管理": "创建、选择、编辑或删除任务。",
        "创建新任务": "建立新的任务目录和记录。",
        "选择已有任务": "切换当前任务。",
        "修改当前任务名称/描述": "只修改显示信息，不移动目录。",
        "删除当前任务": "删除当前任务目录和数据库记录。",
        "进入 Workflow 向导": "继续完成当前任务的流程配置。",
        "提交清单": "保存待下载的数据清单。",
        "工具配置": "设置本任务使用的工具参数。",
        "资源检查": "检查磁盘、工具和参考资产。",
        "正式运行": "开始执行当前任务。",
        "按样本流水线": "样本完成一步后立即进入下一步，适合正式运行。",
        "按阶段批量": "所有样本完成当前步骤后，再进入下一步骤，适合排错。",
        "Docker": "使用容器中的工具，环境更一致。",
        "Local": "使用本机已安装的工具。",
        "任务完成后清理": "任务成功后清理大体积中间文件。",
        "每步成功后清理": "每一步成功后清理上一步大体积文件。",
        "不自动清理": "保留全部产物，便于复查。",
    }
    return hints.get(head, _short_hint(fallback or head or text, limit=86))


def _wrap_display_text(text: str, width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    current_width = 0
    for char in str(text):
        if char == "\n":
            lines.append(current)
            current = ""
            current_width = 0
            continue
        char_width = get_cwidth(char)
        if current and current_width + char_width > width:
            lines.append(current.rstrip())
            current = char
            current_width = char_width
        else:
            current += char
            current_width += char_width
    lines.append(current.rstrip())
    return lines or [""]


def _keyboard_menu(title: str, text: str, values: list[tuple[str, str]]) -> str | None:
    kb = KeyBindings()
    selected = {"index": 0}
    result = {"value": None}
    status_text, fallback_hint = _split_menu_text(text)
    dialog_width = _menu_dialog_width(text, values)
    menu_width = _menu_list_width(values)
    menu_indent = max(0, (dialog_width - menu_width - 6) // 2)

    def clamp() -> None:
        if values:
            selected["index"] = max(0, min(selected["index"], len(values) - 1))
        else:
            selected["index"] = 0

    def move(delta: int, event=None) -> None:
        if not values:
            return
        selected["index"] = (selected["index"] + delta) % len(values)
        if event is not None:
            event.app.invalidate()

    def choose(index: int, event=None) -> None:
        if not values:
            return
        selected["index"] = max(0, min(index, len(values) - 1))
        result["value"] = values[selected["index"]][0]
        if event is not None:
            event.app.exit(result=result["value"])

    def mouse_handler(index: int):
        def handle(mouse_event: MouseEvent) -> None:
            if mouse_event.event_type == MouseEventType.MOUSE_UP:
                choose(index)
                from prompt_toolkit.application.current import get_app

                get_app().exit(result=result["value"])

        return handle

    def render_menu():
        fragments = [("class:dialog.body", f"{status_text}\n\n" if status_text else "")]
        for index, (_value, label) in enumerate(values):
            active = index == selected["index"]
            handle_click = mouse_handler(index)
            label_text = str(label)
            label_padding = max(0, menu_width - get_cwidth(label_text) - 8)
            indent = " " * menu_indent
            if active:
                fragments.extend(
                    [
                        ("class:menu", indent, handle_click),
                        ("class:menu.border", " > ", handle_click),
                        ("class:menu.marker", "* ", handle_click),
                        ("class:menu.selected", label_text, handle_click),
                        ("class:menu", " " * label_padding, handle_click),
                        ("class:menu.border", " <\n", handle_click),
                    ]
                )
            else:
                fragments.append(("class:menu", f"{indent}   {label_text}\n", handle_click))
        fragments.append(("", "\n"))
        fragments.append(("class:menu.border", "说明: "))
        hint_lines = _wrap_display_text(
            _menu_item_hint(values[selected["index"]][1] if values else "", fallback=fallback_hint),
            max(20, dialog_width - 14),
        )
        fragments.append(("class:dialog.body", hint_lines[0]))
        for line in hint_lines[1:]:
            fragments.append(("", "\n"))
            fragments.append(("class:dialog.body", "      " + line))
        return FormattedText(fragments)

    control = FormattedTextControl(render_menu, focusable=True)

    @kb.add("enter")
    def _accept(event) -> None:
        if not values:
            event.app.exit(result=None)
            return
        clamp()
        choose(selected["index"], event)

    @kb.add("down")
    def _down(event) -> None:
        move(1, event)

    @kb.add("up")
    def _up(event) -> None:
        move(-1, event)

    @kb.add("escape")
    @kb.add("c-c")
    def _cancel(event) -> None:
        event.app.exit(result=None)

    @kb.add("q")
    def _quit(event) -> None:
        event.app.exit(result=None)

    def accept() -> None:
        if values:
            clamp()
            result["value"] = values[selected["index"]][0]
        from prompt_toolkit.application.current import get_app

        get_app().exit(result=result["value"])

    def cancel() -> None:
        from prompt_toolkit.application.current import get_app

        get_app().exit(result=None)

    def button_mouse_handler(value):
        def handle(mouse_event: MouseEvent) -> None:
            if mouse_event.event_type == MouseEventType.MOUSE_UP:
                from prompt_toolkit.application.current import get_app

                if value == "accept":
                    if values:
                        clamp()
                        result["value"] = values[selected["index"]][0]
                    get_app().exit(result=result["value"])
                else:
                    get_app().exit(result=None)

        return handle

    def render_buttons():
        return FormattedText(
            [
                ("class:menu.border", "< 确认 Enter >", button_mouse_handler("accept")),
                ("class:dialog.body", " "),
                ("class:menu.border", "< 返回 Esc >", button_mouse_handler("cancel")),
            ]
        )

    button_control = FormattedTextControl(render_buttons, focusable=False)
    body = HSplit(
        [
            Window(content=control, always_hide_cursor=True, dont_extend_height=True),
            Window(content=button_control, always_hide_cursor=True, height=1, dont_extend_height=True, align=WindowAlign.CENTER),
        ],
        padding=1,
    )
    dialog = Dialog(
        title=HTML(f"<b><ansicyan>{title}</ansicyan></b>"),
        body=body,
        buttons=[],
        width=Dimension(min=dialog_width, preferred=dialog_width, max=dialog_width),
        with_background=True,
    )
    centered_dialog = VSplit(
        [
            Window(char=" ", style="class:dialog"),
            dialog,
            Window(char=" ", style="class:dialog"),
        ],
        height=Dimension(min=18, preferred=26),
    )
    centered_layout = HSplit(
        [
            Window(char=" ", style="class:dialog"),
            centered_dialog,
            Window(char=" ", style="class:dialog"),
        ]
    )
    app = Application(
        layout=Layout(centered_layout, focused_element=control),
        key_bindings=kb,
        style=STYLE,
        mouse_support=True,
        full_screen=True,
    )
    return app.run()


def _line_input(title: str, text: str, default: str = "") -> str | None:
    console = Console()
    console.print(f"[cyan]{title}[/cyan]")
    if text:
        console.print(f"[dim]{text}[/dim]")
    suffix = f" [{default}]" if default else ""
    raw = input(f"> {suffix}: ")
    if raw.strip().lower() in {"cancel", "q"}:
        return None
    return raw if raw.strip() else default


def _line_password_input(title: str, text: str) -> str | None:
    import getpass

    console = Console()
    console.print(f"[cyan]{title}[/cyan]")
    if text:
        console.print(f"[dim]{text}[/dim]")
    raw = getpass.getpass("> ")
    if raw.strip().lower() in {"cancel", "q"}:
        return None
    return raw


def _line_multiline_input(title: str, text: str, default: str = "") -> str | None:
    console = Console()
    console.print(f"[cyan]{title}[/cyan]")
    if text:
        console.print(f"[dim]{text}[/dim]")
    if default:
        console.print("[dim]默认内容如下，直接输入空行会使用默认内容。[/dim]")
        console.print(default)
    console.print("[yellow]粘贴多行后，单独输入一行 END 结束；输入 CANCEL 取消。[/yellow]")
    lines: list[str] = []
    while True:
        raw = input()
        if raw.strip().upper() == "CANCEL":
            return None
        if raw.strip().upper() == "END":
            break
        if raw == "" and not lines and default:
            return default
        lines.append(raw)
    return "\n".join(lines)


def _line_yes_no(title: str, default: bool) -> bool:
    Console().print(f"[cyan]{title}[/cyan]")
    hint = "Y/n" if default else "y/N"
    raw = input(f"{hint}: ").strip().lower()
    if not raw:
        return default
    return raw in {"y", "yes", "是", "1", "true"}


def _line_message(title: str, text: str) -> None:
    console = Console()
    console.print(f"[cyan]{title}[/cyan]")
    console.print(text)
    input("按 Enter 继续...")


def _line_multiselect(
    title: str,
    values: list[tuple[str, str]],
    default_values: list[str] | None = None,
) -> list[str] | None:
    console = Console()
    table = Table(title=f"[cyan]{title}[/cyan]")
    table.add_column("Key", justify="right")
    table.add_column("Selected")
    table.add_column("Option")
    default_list = list(default_values or [])
    defaults = set(default_list)
    for index, (value, label) in enumerate(values, start=1):
        table.add_row(str(index), "*" if value in defaults else "", str(label))
    console.print(table)
    console.print("[yellow]输入编号，用逗号/空格分隔；直接 Enter 使用默认全选；0/q 返回。[/yellow]")
    while True:
        raw = input("选择: ").strip()
        if raw.lower() in {"0", "q", "cancel"}:
            return None
        if not raw:
            return default_list
        parts = [part for part in raw.replace(",", " ").split() if part]
        selected: list[str] = []
        try:
            for part in parts:
                index = int(part)
                if not 1 <= index <= len(values):
                    raise ValueError
                selected.append(values[index - 1][0])
        except ValueError:
            console.print("[red]请输入有效编号。[/red]")
            continue
        return selected


def _is_interactive_terminal() -> bool:
    try:
        return os.isatty(0) and os.isatty(1)
    except OSError:
        return False
