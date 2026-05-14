# pipeline/utils.py
import os
import json
import time
import threading
import json
from filelock import FileLock
from rich.console import Console

console = Console()

_PROGRESS_FILE = None
_LOG_FILE = None

def set_progress_file(path):
    global _PROGRESS_FILE
    _PROGRESS_FILE = path

def get_progress_file():
    return _PROGRESS_FILE

def init_log(log_file):
    global _LOG_FILE
    _LOG_FILE = log_file

def log_message(message, level="INFO", extra=None):
    import sys
    from rich.console import Console

    console = Console()
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    level_upper = level.upper()
    # 根据日志级别，仅对标志部分添加颜色标记
    if level_upper == "INFO":
        flag = "[green][INFO][/green]"
    elif level_upper == "ERROR":
        flag = "[red][ERROR][/red]"
    elif level_upper == "WARNING":
        flag = "[yellow][WARNING][/yellow]"
    elif level_upper == "DEBUG":
        flag = "[cyan][DEBUG][/cyan]"
    else:
        flag = f"[{level_upper}]"
    # 构造日志字符串，只有标志部分带有 Rich 标记，其余为普通文本
    log_text = f"[{timestamp}] {flag} {message}"
    if extra:
        log_text += " " + str(extra)
    # 输出到终端，启用 markup 解析
    console.print(log_text, markup=True, overflow="fold")
    # 将纯文本日志写入日志文件，不含颜色标记
    plain_log = f"[{timestamp}] [{level_upper}] {message}"
    if extra:
        plain_log += " " + str(extra)
    if _LOG_FILE:
        try:
            with open(_LOG_FILE, 'a') as lf:
                lf.write(plain_log + "\n")
        except Exception:
            pass

def update_progress_locked(task_id, current_step, total_steps, step_name, status):
    progress_file = get_progress_file()
    lock_file = progress_file + ".lock"
    with FileLock(lock_file):
        try:
            with open(progress_file, 'r') as pf:
                data = json.load(pf)
        except Exception:
            data = {"project_info": {}, "tasks": {}}
        if "tasks" not in data:
            data["tasks"] = {}
        # 如果任务不存在或者没有记录启动时间，并且状态为 Running，则记录启动时间
        if task_id not in data["tasks"]:
            data["tasks"][task_id] = {}
            if status.lower() == "running":
                data["tasks"][task_id]["start_time"] = time.time()
        else:
            if "start_time" not in data["tasks"][task_id] and status.lower() == "running":
                data["tasks"][task_id]["start_time"] = time.time()
        # 更新当前状态
        data["tasks"][task_id].update({
            "current_step": current_step,
            "total_steps": total_steps,
            "step_name": step_name,
            "status": status,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
        })
        with open(progress_file, 'w') as pf:
            json.dump(data, pf, indent=4)

def get_task_progress(task_id):
    progress_file = get_progress_file()
    try:
        with open(progress_file, 'r') as pf:
            data = json.load(pf)
        if "tasks" in data and task_id in data["tasks"]:
            return data["tasks"][task_id].get("current_step", 0)
    except Exception:
        pass
    return 0

def read_all_progress(path: str) -> list[dict]:
    """
    从 progress.json 读出所有任务状态列表
    每个 dict 应当包含:
      task_id, current_step, remaining_steps, status, elapsed
    """
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    tasks = []
    for task_id, info in data.get("tasks", {}).items():
        tasks.append({
            "task_id":       task_id,
            "current_step":  info.get("current_step", ""),
            "remaining_steps": info.get("total_steps", 0) - info.get("current_step_index", 0),
            "status":        info.get("status", ""),
            "elapsed":       info.get("elapsed_time", "")
        })
    return tasks
