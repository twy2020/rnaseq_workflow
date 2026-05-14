from __future__ import annotations

from rnaseq_workflow.core.system_monitor import CpuSnapshot, DiskSnapshot, MemorySnapshot, SystemSnapshot
from rnaseq_workflow.cli import tui


def test_compact_system_snapshot_text_shows_cpu_memory_and_disk():
    snapshot = SystemSnapshot(
        captured_at=1.0,
        cpu=CpuSnapshot(percent=25.0, per_core=(10.0, 40.0)),
        memory=MemorySnapshot(total_bytes=1024, used_bytes=512, available_bytes=512, percent=50.0),
        work_disk=DiskSnapshot(path="C:/work", total_bytes=1000, used_bytes=900, free_bytes=100, percent=90.0, warning_level="critical"),
        spill_disks=(DiskSnapshot(path="D:/spill", total_bytes=1000, used_bytes=100, free_bytes=900, percent=10.0),),
    )

    text = tui._compact_system_snapshot_text(snapshot)

    assert "CPU: 25.0%" in text
    assert "cores[10,40]" in text
    assert "内存: 50.0%" in text
    assert "工作盘: CRIT" in text
    assert "转移盘: OK" in text


def test_parse_spill_paths_accepts_semicolon_and_newline():
    assert tui._parse_spill_paths("D:/a; E:/b\nF:/c") == ["D:/a", "E:/b", "F:/c"]
