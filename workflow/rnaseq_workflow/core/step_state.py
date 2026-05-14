from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

from rnaseq_workflow.core.models import Sample, StepResult, StepStatus


DONE_FILENAME = ".done.json"
LOCK_FILENAME = ".lock"


def done_marker(output_dir: Path) -> Path:
    return output_dir / DONE_FILENAME


def lock_marker(output_dir: Path) -> Path:
    return output_dir / LOCK_FILENAME


def is_step_done(output_dir: Path) -> bool:
    return done_marker(output_dir).exists()


def skipped_done_result(sample: Sample, step_id: str, output_dir: Path) -> StepResult:
    return StepResult(
        sample_id=sample.sample_id,
        step_id=step_id,
        status=StepStatus.SKIPPED,
        message="already completed; skip",
        outputs=[output_dir],
    )


def write_done_marker(output_dir: Path, result: StepResult) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "sample_id": result.sample_id,
        "step_id": result.step_id,
        "status": result.status.value,
        "return_code": result.return_code,
        "message": result.message,
        "finished_at": datetime.now().isoformat(timespec="seconds"),
    }
    done_marker(output_dir).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def acquire_lock(output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    marker = lock_marker(output_dir)
    if marker.exists():
        raise FileExistsError(f"step output is locked: {marker}")
    marker.write_text(datetime.now().isoformat(timespec="seconds"), encoding="utf-8")
    return marker


def release_lock(marker: Path) -> None:
    try:
        marker.unlink()
    except FileNotFoundError:
        pass


def cleanup_incomplete_output(output_dir: Path) -> None:
    if output_dir.exists():
        shutil.rmtree(output_dir)


def cleanup_incomplete_output_keep_errors(output_dir: Path) -> None:
    if not output_dir.exists():
        return
    for path in output_dir.iterdir():
        if path.name == ".error.txt":
            continue
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()


def write_error_log(output_dir: Path, message: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / ".error.txt"
    path.write_text(message, encoding="utf-8", errors="replace")
    return path
