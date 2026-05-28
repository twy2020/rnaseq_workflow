from __future__ import annotations

import json
import re
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path, PureWindowsPath
from threading import Lock
from typing import Any

from rich.console import Console

from rnaseq_workflow.core.models import StepResult


class WorkflowLogger:
    def __init__(self, log_file: str | Path | None = None) -> None:
        self.console = Console()
        self.log_file = Path(log_file) if log_file else None
        if self.log_file:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)

    def info(self, message: str) -> None:
        self._write("INFO", message, "green")

    def warning(self, message: str) -> None:
        self._write("WARNING", message, "yellow")

    def error(self, message: str) -> None:
        self._write("ERROR", message, "red")

    def debug(self, message: str) -> None:
        self._write("DEBUG", message, "cyan")

    def _write(self, level: str, message: str, color: str) -> None:
        self.console.print(f"[{color}][{level}][/{color}] {message}")
        if self.log_file:
            with self.log_file.open("a", encoding="utf-8") as handle:
                handle.write(f"[{level}] {message}\n")


class TaskLogManager:
    """Thread-safe task log writer for workflow events and step command traces."""

    def __init__(self, root: str | Path, task_id: str | None = None, user_id: str | None = None) -> None:
        self.root = Path(root)
        self.logs_dir = self.root if self.root.name == "logs" else self.root / "logs"
        self.task_id = task_id
        self.user_id = user_id
        self._lock = Lock()
        self.ensure()

    def ensure(self) -> None:
        for path in (self.logs_dir, self.logs_dir / "samples", self.logs_dir / "archive"):
            path.mkdir(parents=True, exist_ok=True)

    @property
    def events_path(self) -> Path:
        return self.logs_dir / "events.jsonl"

    @property
    def commands_path(self) -> Path:
        return self.logs_dir / "commands.jsonl"

    @property
    def resource_path(self) -> Path:
        return self.logs_dir / "resource.jsonl"

    @property
    def downloads_path(self) -> Path:
        return self.logs_dir / "downloads.jsonl"

    @property
    def tui_path(self) -> Path:
        return self.logs_dir / "tui.log"

    def event(
        self,
        event: str,
        *,
        level: str = "INFO",
        message: str = "",
        task_id: str | None = None,
        user_id: str | None = None,
        sample_id: str | None = None,
        step_id: str | None = None,
        **fields: Any,
    ) -> dict[str, Any]:
        record = {
            "time": _now(),
            "level": level,
            "event": event,
            "task_id": task_id or self.task_id,
            "user_id": user_id or self.user_id,
            "sample_id": sample_id,
            "step_id": step_id,
            "message": self.sanitize(message),
        }
        record.update({key: self.sanitize(value) for key, value in fields.items()})
        self._append_jsonl(self.events_path, record)
        return record

    def command(
        self,
        *,
        command: Sequence[str] | None,
        sample_id: str,
        step_id: str,
        return_code: int | None,
        duration_seconds: float | None = None,
        status: str = "",
        stdout_log: str | Path | None = None,
        stderr_log: str | Path | None = None,
        command_id: str | None = None,
        execution_mode: str | None = None,
        task_id: str | None = None,
        **fields: Any,
    ) -> dict[str, Any]:
        record = {
            "time": _now(),
            "task_id": task_id or self.task_id,
            "sample_id": sample_id,
            "step_id": step_id,
            "command_id": command_id or self.new_command_id(),
            "execution_mode": execution_mode,
            "command": self.sanitize(list(command or [])),
            "return_code": return_code,
            "duration_seconds": duration_seconds,
            "stdout_log": self.relative(stdout_log) if stdout_log else None,
            "stderr_log": self.relative(stderr_log) if stderr_log else None,
            "status": status,
        }
        record.update({key: self.sanitize(value) for key, value in fields.items()})
        self._append_jsonl(self.commands_path, record)
        return record

    def sample_step_log(
        self,
        result: StepResult,
        *,
        step_name: str = "",
        command_id: str | None = None,
        task_id: str | None = None,
        execution_mode: str | None = None,
    ) -> Path:
        path = self.sample_step_log_path(result.sample_id, result.step_id)
        extra = dict(result.extra or {})
        metadata = {
            "task_id": task_id or self.task_id or "",
            "sample_id": result.sample_id,
            "step_id": result.step_id,
            "step_name": step_name,
            "status": result.status.value,
            "started_at": extra.get("started_at") or "",
            "finished_at": extra.get("finished_at") or "",
            "duration_seconds": extra.get("duration_seconds") or "",
            "return_code": "" if result.return_code is None else result.return_code,
            "command_id": command_id or "",
            "execution_mode": execution_mode or "",
            "dry_run": extra.get("dry_run", ""),
        }
        sections = [
            ("meta", _format_key_values(metadata)),
            ("inputs", _format_lines(str(path) for path in result.inputs)),
            ("outputs", _format_lines(str(path) for path in result.outputs)),
            ("command", _format_lines(result.command or [])),
        ]
        command_results = _command_result_records(extra)
        if command_results:
            sections.append(("command_results", _format_command_result_sections(command_results)))
        sections.extend(
            [
                ("stdout", str(extra.get("stdout") or "")),
                ("stderr", str(extra.get("stderr") or "")),
            ]
        )
        text = "\n\n".join(f"[{title}]\n{self.sanitize(body)}" for title, body in sections)
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text.rstrip() + "\n", encoding="utf-8", errors="replace")
        return path

    def log_step_result(
        self,
        result: StepResult,
        *,
        step_name: str = "",
        execution_mode: str | None = None,
        event_name: str | None = None,
        task_id: str | None = None,
        user_id: str | None = None,
    ) -> StepResult:
        command_results = _command_result_records(result.extra)
        primary_command = result.command or (_command_from_record(command_results[-1]) if command_results else None)
        command_ids = _existing_command_ids(result.extra, len(command_results))
        if command_results:
            command_ids = command_ids or [self.new_command_id() for _ in command_results]
            command_id = command_ids[-1]
        else:
            command_id = str(result.extra.get("command_id") or self.new_command_id()) if primary_command else None
        step_log = self.sample_step_log(
            result,
            step_name=step_name,
            command_id=command_id,
            task_id=task_id,
            execution_mode=execution_mode,
        )
        result.log_file = self.relative(step_log)
        result.extra["log_file"] = result.log_file
        if command_results:
            result.extra["command_ids"] = command_ids
            result.extra["command_id"] = command_id
            result.extra["command_log_file"] = self.relative(self.commands_path)
            for index, command_result in enumerate(command_results, start=1):
                self.command(
                    command=_command_from_record(command_result),
                    sample_id=result.sample_id,
                    step_id=result.step_id,
                    return_code=_int_or_none(command_result.get("return_code")),
                    duration_seconds=_float_or_none(command_result.get("duration_seconds")),
                    status=_command_status(command_result),
                    stdout_log=step_log,
                    stderr_log=step_log,
                    command_id=command_ids[index - 1],
                    execution_mode=execution_mode,
                    task_id=task_id,
                    command_index=index,
                    started_at=command_result.get("started_at"),
                    finished_at=command_result.get("finished_at"),
                    dry_run=command_result.get("dry_run"),
                )
        elif command_id:
            result.extra["command_id"] = command_id
            result.extra["command_log_file"] = self.relative(self.commands_path)
            self.command(
                command=primary_command,
                sample_id=result.sample_id,
                step_id=result.step_id,
                return_code=result.return_code,
                duration_seconds=_float_or_none(result.extra.get("duration_seconds")),
                status=result.status.value,
                stdout_log=step_log,
                stderr_log=step_log,
                command_id=command_id,
                execution_mode=execution_mode,
                task_id=task_id,
            )
        if event_name:
            self.event(
                event_name,
                level=_event_level(result.status.value),
                task_id=task_id,
                user_id=user_id,
                sample_id=result.sample_id,
                step_id=result.step_id,
                message=result.message,
                status=result.status.value,
                return_code=result.return_code,
                log_file=result.log_file,
                command_id=command_id,
            )
        return result

    def resource(self, **fields: Any) -> dict[str, Any]:
        record = {"time": _now(), **{key: self.sanitize(value) for key, value in fields.items()}}
        self._append_jsonl(self.resource_path, record)
        return record

    def download(self, **fields: Any) -> dict[str, Any]:
        record = {"time": _now(), **{key: self.sanitize(value) for key, value in fields.items()}}
        self._append_jsonl(self.downloads_path, record)
        return record

    def tui(self, message: str, **fields: Any) -> None:
        payload = {"time": _now(), "task_id": self.task_id, "user_id": self.user_id, "message": self.sanitize(message)}
        payload.update({key: self.sanitize(value) for key, value in fields.items()})
        with self._lock:
            self.tui_path.parent.mkdir(parents=True, exist_ok=True)
            with self.tui_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")

    def sample_step_log_path(self, sample_id: str, step_id: str) -> Path:
        return self.logs_dir / "samples" / _safe_path_part(sample_id) / f"{_safe_path_part(step_id)}.log"

    def relative(self, path: str | Path) -> str:
        raw = Path(path)
        try:
            return raw.resolve().relative_to(self.logs_dir.parent.resolve()).as_posix()
        except (OSError, ValueError):
            return raw.as_posix()

    def sanitize(self, value: Any) -> Any:
        if isinstance(value, str):
            return sanitize_text(value)
        if isinstance(value, Path):
            return sanitize_text(str(value))
        if isinstance(value, Mapping):
            return {key: self.sanitize(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.sanitize(item) for item in value]
        if isinstance(value, tuple):
            return [self.sanitize(item) for item in value]
        return value

    def new_command_id(self) -> str:
        return f"cmd-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}"

    def _append_jsonl(self, path: Path, record: dict[str, Any]) -> None:
        cleaned = {key: value for key, value in record.items() if value is not None}
        line = json.dumps(cleaned, ensure_ascii=False, sort_keys=True)
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")


def sanitize_text(text: str) -> str:
    text = _PROXY_CREDENTIAL_RE.sub(r"\1***:***@", text)
    text = _SECRET_ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}=***", text)
    text = _AUTH_HEADER_RE.sub(lambda match: f"{match.group(1)} ***", text)
    return text


_PROXY_CREDENTIAL_RE = re.compile(r"\b(https?://)[^/\s:@]+:[^/\s@]+@", re.IGNORECASE)
_SECRET_ASSIGNMENT_RE = re.compile(r"\b([A-Za-z0-9_.-]*(?:token|password|passwd|secret|cookie)[A-Za-z0-9_.-]*)=([^\s]+)", re.IGNORECASE)
_AUTH_HEADER_RE = re.compile(r"\b(authorization:?)\s+\S+", re.IGNORECASE)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _format_key_values(values: Mapping[str, Any]) -> str:
    return "\n".join(f"{key}={value}" for key, value in values.items())


def _format_lines(values: Sequence[str] | Any) -> str:
    if isinstance(values, str):
        return values
    return "\n".join(str(value) for value in values)


def _safe_path_part(value: str) -> str:
    raw = str(PureWindowsPath(value).name if ("\\" in str(value) or ":" in str(value)) else Path(str(value)).name)
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", raw).strip("._") or "unknown"


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _command_result_records(extra: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw = extra.get("command_results") if isinstance(extra, Mapping) else None
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, Mapping)]


def _existing_command_ids(extra: Mapping[str, Any], expected: int) -> list[str]:
    raw = extra.get("command_ids") if isinstance(extra, Mapping) else None
    if not isinstance(raw, list):
        return []
    ids = [str(item) for item in raw if str(item or "").strip()]
    return ids if len(ids) == expected else []


def _command_from_record(record: Mapping[str, Any]) -> list[str]:
    raw = record.get("command")
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw]


def _command_status(record: Mapping[str, Any]) -> str:
    return "COMPLETED" if _int_or_none(record.get("return_code")) == 0 else "FAILED"


def _format_command_result_sections(records: list[Mapping[str, Any]]) -> str:
    blocks = []
    for index, record in enumerate(records, start=1):
        lines = [
            f"command_index={index}",
            f"started_at={record.get('started_at') or ''}",
            f"finished_at={record.get('finished_at') or ''}",
            f"duration_seconds={record.get('duration_seconds') or ''}",
            f"return_code={record.get('return_code') if record.get('return_code') is not None else ''}",
            "command=" + " ".join(_command_from_record(record)),
            "[stdout]",
            str(record.get("stdout") or ""),
            "[stderr]",
            str(record.get("stderr") or ""),
        ]
        blocks.append("\n".join(lines).rstrip())
    return "\n\n".join(blocks)


def _event_level(status: str) -> str:
    if status == "FAILED":
        return "ERROR"
    if status in {"CANCELLED", "SKIPPED"}:
        return "WARNING"
    return "INFO"
