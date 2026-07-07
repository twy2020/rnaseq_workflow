from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from rnaseq_workflow.cli import main
from rnaseq_workflow.core.finalize import FinalizeResult


def test_finalize_command_uses_expression_output_formats(monkeypatch, tmp_path):
    output_dir = tmp_path / "output"
    config = tmp_path / "config.yaml"
    config.write_text(
        "\n".join(
            [
                "project_id: demo",
                f"output_dir: {output_dir.as_posix()}",
                "samples:",
                "  - sample_id: S1",
                f"    source_path: {(tmp_path / 'S1.fastq').as_posix()}",
                "expression_output_formats:",
                "  - raw_counts",
                "  - tpm",
                "  - stringtie_tpm",
            ]
        ),
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    def fake_finalize_project(*args, **kwargs):
        captured["output_formats"] = kwargs.get("output_formats")
        return FinalizeResult(
            count_tables=[],
            counts_matrix=Path("raw_counts.tsv"),
            report_json=Path("report.json"),
            report_markdown=Path("report.md"),
            sample_count=1,
            gene_count=1,
            expression_matrices={"raw_counts": Path("raw_counts.tsv"), "tpm": Path("tpm.tsv")},
        )

    monkeypatch.setattr(main, "finalize_project", fake_finalize_project)

    result = CliRunner().invoke(main.app, ["finalize", str(config)])

    assert result.exit_code == 0
    assert captured["output_formats"] == ["raw_counts", "tpm", "stringtie_tpm"]
