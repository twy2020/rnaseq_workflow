from __future__ import annotations

from io import StringIO
from pathlib import Path

from rich.console import Console

from rnaseq_workflow.cli.interactive import _resolve_from_base


def test_resolve_from_base_keeps_absolute_path(tmp_path):
    assert _resolve_from_base(Path("base"), str(tmp_path)) == tmp_path


def test_resolve_from_base_joins_relative_path():
    assert _resolve_from_base(Path("base"), "data") == Path("base") / "data"


def test_interactive_console_exits(monkeypatch):
    from rnaseq_workflow.cli.interactive import run_interactive_console

    console = Console(file=StringIO(), force_terminal=False)
    monkeypatch.setattr(console, "input", lambda _prompt="": "0")

    run_interactive_console(console)

    assert "RNA-seq Workflow Terminal" in console.file.getvalue()
