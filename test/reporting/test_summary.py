from __future__ import annotations

import json

import pytest

from rnaseq_workflow.steps.reporting.summary import (
    build_project_report,
    summarize_artifacts,
    summarize_counts_matrix,
    summarize_progress_state,
    summarize_quality_notes,
    write_report_json,
    write_report_markdown,
)


def test_summarize_progress_state(tmp_path):
    state_path = tmp_path / "progress.json"
    state_path.write_text(
        json.dumps(
            {
                "samples": {
                    "S1": {
                        "steps": {
                            "fastqc": {"status": "COMPLETED"},
                            "hisat2": {"status": "FAILED"},
                        }
                    },
                    "S2": {"steps": {"fastqc": {"status": "RUNNING"}}},
                }
            }
        ),
        encoding="utf-8",
    )

    sample_count, step_status = summarize_progress_state(state_path)

    assert sample_count == 2
    assert step_status.total == 3
    assert step_status.completed == 1
    assert step_status.failed == 1
    assert step_status.running == 1


def test_summarize_missing_progress_state(tmp_path):
    sample_count, step_status = summarize_progress_state(tmp_path / "missing.json")

    assert sample_count == 0
    assert step_status.total == 0


def test_summarize_counts_matrix(tmp_path):
    matrix_path = tmp_path / "counts.tsv"
    matrix_path.write_text("Geneid\tS1\tS2\ngeneA\t1\t2\ngeneB\t0\t3\n", encoding="utf-8")

    summary = summarize_counts_matrix(matrix_path)

    assert summary is not None
    assert summary.exists
    assert summary.sample_count == 2
    assert summary.gene_count == 2
    assert summary.sample_ids == ["S1", "S2"]


def test_summarize_counts_matrix_rejects_invalid_header(tmp_path):
    matrix_path = tmp_path / "bad.tsv"
    matrix_path.write_text("bad\tS1\n", encoding="utf-8")

    with pytest.raises(ValueError, match="invalid counts matrix header"):
        summarize_counts_matrix(matrix_path)


def test_summarize_artifacts(tmp_path):
    artifact = tmp_path / "report.txt"
    artifact.write_text("hello", encoding="utf-8")

    summaries = summarize_artifacts([artifact, tmp_path / "missing.txt"])

    assert summaries[0].exists
    assert summaries[0].size_bytes == 5
    assert not summaries[1].exists
    assert summaries[1].size_bytes is None


def test_write_report_json_and_markdown(tmp_path):
    state_path = tmp_path / "progress.json"
    state_path.write_text(json.dumps({"samples": {"S1": {"steps": {"fastqc": {"status": "COMPLETED"}}}}}), encoding="utf-8")
    matrix_path = tmp_path / "counts.tsv"
    matrix_path.write_text("Geneid\tS1\ngeneA\t1\n", encoding="utf-8")
    report = build_project_report(
        project_id="demo",
        output_dir=tmp_path / "output",
        state_path=state_path,
        counts_matrix_path=matrix_path,
        tool_versions={"featureCounts": "2.0.6"},
    )

    json_output = tmp_path / "report.json"
    markdown_output = tmp_path / "report.md"
    write_report_json(report, json_output)
    write_report_markdown(report, markdown_output)

    assert json.loads(json_output.read_text(encoding="utf-8"))["project_id"] == "demo"
    markdown = markdown_output.read_text(encoding="utf-8")
    assert "# RNA-seq Workflow Report: demo" in markdown
    assert "| featureCounts | 2.0.6 |" in markdown


def test_report_includes_trimmed_fastqc_quality_notes(tmp_path):
    state_path = tmp_path / "progress.json"
    state_path.write_text(
        json.dumps(
            {
                "samples": {
                    "S1": {
                        "steps": {
                            "fastqc_trimmed": {
                                "status": "PAUSED",
                                "message": "manual review",
                                "extra": {
                                    "quality_policy": "pause_on_fail",
                                    "fastqc_issues": [
                                        {"file": "S1_fastqc.zip", "status": "FAIL", "module": "Adapter Content", "sequence": "S1"}
                                    ],
                                },
                            }
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    notes = summarize_quality_notes(state_path)
    report = build_project_report("demo", tmp_path, state_path=state_path)
    markdown_output = tmp_path / "report.md"
    write_report_markdown(report, markdown_output)

    assert notes[0]["sample_id"] == "S1"
    assert report.quality_notes[0]["issue_count"] == 1
    assert "## Quality Notes" in markdown_output.read_text(encoding="utf-8")
