from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any

from rnaseq_workflow.core.models import Sample, StepRecord, StepResult, StepStatus
from rnaseq_workflow.core.steps import PipelineStep


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class JsonStateRepository:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write({"samples": {}})

    def get_step_record(self, sample_id: str, step_id: str) -> StepRecord | None:
        with self._lock:
            data = self._read()
        raw = data.get("samples", {}).get(sample_id, {}).get("steps", {}).get(step_id)
        if not raw:
            return None
        raw["status"] = StepStatus(raw["status"])
        return StepRecord(**raw)

    def get_sample_pause_record(self, sample_id: str) -> StepRecord | None:
        with self._lock:
            data = self._read()
        steps = data.get("samples", {}).get(sample_id, {}).get("steps", {})
        for raw in steps.values():
            if not isinstance(raw, dict) or raw.get("status") != StepStatus.PAUSED.value:
                continue
            record = dict(raw)
            record["status"] = StepStatus(record["status"])
            return StepRecord(**record)
        return None

    def mark_running(self, sample: Sample, step: PipelineStep) -> None:
        with self._lock:
            data = self._read()
            sample_data = data.setdefault("samples", {}).setdefault(sample.sample_id, {"steps": {}})
            sample_data["source_path"] = str(sample.source_path)
            sample_data["layout"] = sample.layout.value
            sample_data["project_id"] = sample.project_id
            sample_data.setdefault("steps", {})[step.step_id] = {
                "sample_id": sample.sample_id,
                "step_id": step.step_id,
                "step_name": step.name,
                "status": StepStatus.RUNNING.value,
                "message": "",
                "command": None,
                "return_code": None,
                "inputs": [],
                "outputs": [],
                "started_at": _now(),
                "finished_at": None,
                "extra": {},
                "log_file": None,
            }
            self._write(data)

    def save_step_result(self, step: PipelineStep, result: StepResult) -> None:
        with self._lock:
            data = self._read()
            sample_data = data.setdefault("samples", {}).setdefault(result.sample_id, {"steps": {}})
            previous = sample_data.setdefault("steps", {}).get(result.step_id, {})
            record = {
                "sample_id": result.sample_id,
                "step_id": result.step_id,
                "step_name": step.name,
                "status": result.status.value,
                "message": result.message,
                "command": result.command,
                "return_code": result.return_code,
                "inputs": [str(path) for path in result.inputs],
                "outputs": [str(path) for path in result.outputs],
                "started_at": previous.get("started_at") or _now(),
                "finished_at": _now(),
                "extra": result.extra,
                "log_file": result.log_file,
            }
            sample_data["steps"][result.step_id] = record
            self._write(data)

    def make_failed_result(self, sample: Sample, step: PipelineStep, message: str) -> StepResult:
        return StepResult(
            sample_id=sample.sample_id,
            step_id=step.step_id,
            status=StepStatus.FAILED,
            message=message,
        )

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            data = {"samples": {}}
            self._write(data)
            return data
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except json.JSONDecodeError:
            return self._recover_corrupt_state()
        if not isinstance(data, dict):
            return self._recover_corrupt_state()
        data.setdefault("samples", {})
        return data

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_name(f"{self.path.name}.tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
        tmp_path.replace(self.path)

    def _recover_corrupt_state(self) -> dict[str, Any]:
        backup = self._corrupt_backup_path()
        try:
            self.path.replace(backup)
        except OSError:
            pass
        data = {"samples": {}}
        self._write(data)
        return data

    def _corrupt_backup_path(self) -> Path:
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        candidate = self.path.with_name(f"{self.path.name}.corrupt-{timestamp}")
        counter = 1
        while candidate.exists():
            candidate = self.path.with_name(f"{self.path.name}.corrupt-{timestamp}-{counter}")
            counter += 1
        return candidate
