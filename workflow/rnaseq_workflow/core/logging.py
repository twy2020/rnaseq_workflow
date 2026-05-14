from __future__ import annotations

from pathlib import Path

from rich.console import Console


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
