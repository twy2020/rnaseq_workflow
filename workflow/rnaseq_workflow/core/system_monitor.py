from __future__ import annotations

import ctypes
import os
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True, slots=True)
class CpuSnapshot:
    percent: float | None = None
    per_core: tuple[float, ...] = ()


@dataclass(frozen=True, slots=True)
class MemorySnapshot:
    total_bytes: int | None = None
    used_bytes: int | None = None
    available_bytes: int | None = None
    percent: float | None = None


@dataclass(frozen=True, slots=True)
class DiskSnapshot:
    path: str
    total_bytes: int
    used_bytes: int
    free_bytes: int
    percent: float
    warning_level: str = "ok"


@dataclass(frozen=True, slots=True)
class SystemSnapshot:
    captured_at: float
    cpu: CpuSnapshot = field(default_factory=CpuSnapshot)
    memory: MemorySnapshot = field(default_factory=MemorySnapshot)
    work_disk: DiskSnapshot | None = None
    spill_disks: tuple[DiskSnapshot, ...] = ()

    def to_dict(self) -> dict:
        return asdict(self)


class CpuSampler:
    """Lightweight system CPU sampler with psutil when available and a Windows fallback."""

    def __init__(self) -> None:
        self._last_windows_idle: int | None = None
        self._last_windows_total: int | None = None
        try:
            import psutil  # type: ignore
        except Exception:
            self._psutil = None
        else:
            self._psutil = psutil
            try:
                psutil.cpu_percent(interval=None, percpu=True)
            except Exception:
                pass

    def sample(self) -> CpuSnapshot:
        if self._psutil is not None:
            try:
                per_core = tuple(float(value) for value in self._psutil.cpu_percent(interval=None, percpu=True))
                percent = sum(per_core) / len(per_core) if per_core else None
                return CpuSnapshot(percent=percent, per_core=per_core)
            except Exception:
                pass
        if os.name == "nt":
            return self._sample_windows()
        return CpuSnapshot()

    def _sample_windows(self) -> CpuSnapshot:
        idle_time = _FILETIME()
        kernel_time = _FILETIME()
        user_time = _FILETIME()
        if not ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle_time), ctypes.byref(kernel_time), ctypes.byref(user_time)):
            return CpuSnapshot()
        idle = _filetime_to_int(idle_time)
        kernel = _filetime_to_int(kernel_time)
        user = _filetime_to_int(user_time)
        total = kernel + user
        if self._last_windows_idle is None or self._last_windows_total is None:
            self._last_windows_idle = idle
            self._last_windows_total = total
            return CpuSnapshot()
        idle_delta = idle - self._last_windows_idle
        total_delta = total - self._last_windows_total
        self._last_windows_idle = idle
        self._last_windows_total = total
        if total_delta <= 0:
            return CpuSnapshot()
        percent = max(0.0, min(100.0, (1.0 - idle_delta / total_delta) * 100.0))
        return CpuSnapshot(percent=percent)


class _FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", ctypes.c_ulong), ("dwHighDateTime", ctypes.c_ulong)]


def _filetime_to_int(value: _FILETIME) -> int:
    return (int(value.dwHighDateTime) << 32) + int(value.dwLowDateTime)


def collect_system_snapshot(
    work_path: str | Path,
    spill_paths: Iterable[str | Path] = (),
    sampler: CpuSampler | None = None,
    min_free_gb: float = 20.0,
    min_free_percent: float = 10.0,
) -> SystemSnapshot:
    sampler = sampler or CpuSampler()
    work_disk = disk_snapshot(work_path, min_free_gb=min_free_gb, min_free_percent=min_free_percent)
    spill_disks = tuple(
        disk_snapshot(path, min_free_gb=min_free_gb, min_free_percent=min_free_percent)
        for path in spill_paths
        if str(path or "").strip()
    )
    return SystemSnapshot(
        captured_at=time.time(),
        cpu=sampler.sample(),
        memory=memory_snapshot(),
        work_disk=work_disk,
        spill_disks=spill_disks,
    )


def disk_snapshot(path: str | Path, min_free_gb: float = 20.0, min_free_percent: float = 10.0) -> DiskSnapshot:
    resolved = _existing_disk_path(Path(path))
    usage = shutil.disk_usage(resolved)
    percent = usage.used / usage.total * 100.0 if usage.total else 0.0
    free_gb = usage.free / 1024**3
    free_percent = usage.free / usage.total * 100.0 if usage.total else 0.0
    level = "ok"
    if free_gb <= max(float(min_free_gb), 0.0) or free_percent <= max(float(min_free_percent), 0.0):
        level = "critical"
    elif free_gb <= max(float(min_free_gb), 0.0) * 1.5 or free_percent <= max(float(min_free_percent), 0.0) * 1.5:
        level = "warning"
    return DiskSnapshot(
        path=str(resolved),
        total_bytes=usage.total,
        used_bytes=usage.used,
        free_bytes=usage.free,
        percent=percent,
        warning_level=level,
    )


def memory_snapshot() -> MemorySnapshot:
    try:
        import psutil  # type: ignore
    except Exception:
        if os.name == "nt":
            return _windows_memory_snapshot()
        return MemorySnapshot()
    try:
        mem = psutil.virtual_memory()
    except Exception:
        return MemorySnapshot()
    return MemorySnapshot(
        total_bytes=int(mem.total),
        used_bytes=int(mem.used),
        available_bytes=int(mem.available),
        percent=float(mem.percent),
    )


def _windows_memory_snapshot() -> MemorySnapshot:
    status = _MEMORYSTATUSEX()
    status.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return MemorySnapshot()
    total = int(status.ullTotalPhys)
    available = int(status.ullAvailPhys)
    used = max(0, total - available)
    percent = used / total * 100.0 if total else None
    return MemorySnapshot(total_bytes=total, used_bytes=used, available_bytes=available, percent=percent)


class _MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def _existing_disk_path(path: Path) -> Path:
    path = path.expanduser()
    if path.exists():
        return path
    for parent in [path, *path.parents]:
        if parent.exists():
            return parent
    anchor = Path(path.anchor) if path.anchor else Path.cwd()
    return anchor if anchor.exists() else Path.cwd()
