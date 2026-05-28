from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ResourceCheck:
    name: str
    level: str
    ok: bool
    message: str
    recommendation: str = ""


@dataclass(frozen=True, slots=True)
class ResourceEstimate:
    sample_count: int = 0
    input_file_count: int = 0
    input_size_bytes: int = 0
    estimated_output_bytes: int = 0
    estimated_peak_workspace_bytes: int = 0
    recommended_free_bytes: int = 0
    notes: list[str] | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def run_resource_checks(
    workspace_dir: str | Path,
    docker_image: str = "rnaseq-workflow:tools",
    network_host: str = "www.ncbi.nlm.nih.gov",
    estimate: ResourceEstimate | None = None,
    required_docker_tools: list[str] | None = None,
) -> list[ResourceCheck]:
    root = Path(workspace_dir)
    checks = [
        _cpu_check(),
        _memory_check(),
        _disk_check(root, estimate),
        _command_check("docker", ["docker", "--version"]),
        _network_check(network_host),
    ]
    if shutil.which("docker"):
        checks.append(_command_check("docker image", ["docker", "image", "inspect", docker_image]))
        for tool in required_docker_tools or []:
            checks.append(_docker_tool_check(docker_image, tool))
    return checks


def write_resource_checks(checks: list[ResourceCheck], path: str | Path, estimate: ResourceEstimate | None = None) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "checks": [asdict(check) for check in checks],
        "estimate": estimate.to_dict() if estimate else None,
    }
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return output


def estimate_workflow_resources(input_dir: str | Path, sample_count: int = 0) -> ResourceEstimate:
    root = Path(input_dir)
    manifest_files = _read_local_manifest_files(root)
    if manifest_files is not None:
        input_size = sum(int(row.get("size_bytes") or _safe_size(Path(str(row.get("path") or "")))) for row in manifest_files)
        inferred_samples = sample_count or len({str(row.get("sample_id") or Path(str(row.get("path") or "")).stem) for row in manifest_files})
        input_file_count = len(manifest_files)
    else:
        files = [path for path in root.rglob("*") if path.is_file()] if root.exists() else []
        input_size = sum(_safe_size(path) for path in files)
        inferred_samples = sample_count or _infer_sample_count(files)
        input_file_count = len(files)
    estimated_output = int(input_size * 3.0)
    peak_workspace = int(input_size * 4.5)
    recommended_free = max(peak_workspace, 20 * 1024**3)
    notes = [
        "估算基于输入体量和常见 RNA-seq 中间产物倍率。",
        "HISAT2 SAM、排序 BAM 和临时文件可能短时占用较多空间。",
    ]
    return ResourceEstimate(
        sample_count=inferred_samples,
        input_file_count=input_file_count,
        input_size_bytes=input_size,
        estimated_output_bytes=estimated_output,
        estimated_peak_workspace_bytes=peak_workspace,
        recommended_free_bytes=recommended_free,
        notes=notes,
    )


def _read_local_manifest_files(root: Path) -> list[dict] | None:
    record = root / "local_files.json"
    if not record.exists():
        return None
    try:
        data = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    files = data.get("files") if isinstance(data, dict) else None
    if not isinstance(files, list):
        return None
    return [row for row in files if isinstance(row, dict)]


def _cpu_check() -> ResourceCheck:
    count = os.cpu_count() or 1
    if count < 4:
        return ResourceCheck("cpu", "warning", True, f"cores={count}", "建议至少 4 核；可降低样本并发数和工具线程数。")
    return ResourceCheck("cpu", "info", True, f"cores={count}", "CPU 核心数可满足常规小样本验证。")


def _memory_check() -> ResourceCheck:
    total = _total_memory_bytes()
    if total is None:
        return ResourceCheck("memory", "warning", True, "unknown", "无法读取内存信息；大基因组建索引前请人工确认可用内存。")
    total_gb = total / 1024**3
    if total_gb < 8:
        return ResourceCheck("memory", "warning", True, f"total={total_gb:.1f}GB", "建议至少 8GB；HISAT2 建索引和排序 BAM 可能受限。")
    return ResourceCheck("memory", "info", True, f"total={total_gb:.1f}GB", "内存满足常规流程验证。")


def _disk_check(path: Path, estimate: ResourceEstimate | None = None) -> ResourceCheck:
    path.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(path)
    free_gb = usage.free / 1024 / 1024 / 1024
    threshold = estimate.recommended_free_bytes if estimate else 10 * 1024**3
    ok = usage.free >= threshold
    recommended_gb = threshold / 1024**3
    level = "error" if not ok else ("warning" if free_gb < recommended_gb * 1.25 else "info")
    return ResourceCheck(
        name="disk",
        level=level,
        ok=ok,
        message=f"free={free_gb:.1f}GB recommended>={recommended_gb:.1f}GB",
        recommendation="释放磁盘空间或降低自动保留中间产物；正式任务建议保留额外余量。" if not ok else "磁盘空间满足当前估算。",
    )


def _network_check(host: str) -> ResourceCheck:
    try:
        socket.create_connection((host, 443), timeout=5).close()
    except OSError as exc:
        return ResourceCheck("network", "warning", False, str(exc), "网络不可达时下载阶段可能失败；可改用本地数据或稍后重试。")
    return ResourceCheck("network", "info", True, f"{host}:443 reachable", "网络连通。")


def _command_check(name: str, command: list[str]) -> ResourceCheck:
    if shutil.which(command[0]) is None:
        return ResourceCheck(name, "error", False, f"command not found: {command[0]}", f"请安装 {command[0]}，或切换到可用执行环境。")
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip() or f"exit code {completed.returncode}"
        return ResourceCheck(name, "error", False, message, "请检查工具安装、Docker 服务或镜像名称。")
    message = completed.stdout.strip().splitlines()[0] if completed.stdout.strip() else "ok"
    return ResourceCheck(name, "info", True, message, "工具可用。")


def _docker_tool_check(docker_image: str, tool: str) -> ResourceCheck:
    command = ["docker", "run", "--rm", docker_image, tool, "--version"]
    try:
        completed = subprocess.run(command, check=False, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return ResourceCheck(
            f"docker tool:{tool}",
            "error",
            False,
            "tool check timed out",
            f"请检查 Docker 镜像 {docker_image} 是否可正常启动，并确认 {tool} 已安装。",
        )
    except OSError as exc:
        return ResourceCheck(
            f"docker tool:{tool}",
            "error",
            False,
            str(exc),
            "请检查 Docker 服务是否可用。",
        )
    if completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip() or f"exit code {completed.returncode}"
        if f'exec: "{tool}": executable file not found' in message or "executable file not found in $PATH" in message:
            message = f"Docker 镜像缺少 {tool} 可执行文件"
        return ResourceCheck(
            f"docker tool:{tool}",
            "error",
            False,
            message,
            f"请重建镜像：docker build -f docker/Dockerfile.tools -t {docker_image} .",
        )
    message = completed.stdout.strip().splitlines()[0] if completed.stdout.strip() else "ok"
    return ResourceCheck(f"docker tool:{tool}", "info", True, message, "容器内工具可用。")


def _safe_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _infer_sample_count(files: list[Path]) -> int:
    names = set()
    for path in files:
        name = path.name
        for suffix in (".fastq.gz", ".fq.gz", ".fastq", ".fq", ".sra"):
            if name.endswith(suffix):
                name = name[: -len(suffix)]
                break
        name = name.replace("_1", "").replace("_2", "").replace("_R1", "").replace("_R2", "")
        names.add(name)
    return len(names)


def _total_memory_bytes() -> int | None:
    if os.name == "nt":
        try:
            completed = subprocess.run(
                ["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory"],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if completed.returncode == 0:
                return int(completed.stdout.strip())
        except (OSError, ValueError, subprocess.SubprocessError):
            return None
    meminfo = Path("/proc/meminfo")
    if meminfo.exists():
        for line in meminfo.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) * 1024
    return None
