from __future__ import annotations

import json

from rnaseq_workflow.core.config import load_project_config
from rnaseq_workflow.core.models import RunContext
from rnaseq_workflow.core.pipeline import Pipeline
from rnaseq_workflow.core.samples import samples_from_config
from rnaseq_workflow.core.step_registry import build_pipeline_steps
from rnaseq_workflow.executors.local import LocalExecutor
from rnaseq_workflow.persistence.json_state import JsonStateRepository


def test_sample_level_pipeline_dry_run_writes_real_step_records(tmp_path):
    fastq = tmp_path / "S1.fastq"
    fastq.write_text("@r1\nACGT\n+\nIIII\n", encoding="utf-8")
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        f"""
project_id: demo
output_dir: {tmp_path / "output"}
samples:
  - sample_id: S1
    source_path: {fastq}
    layout: single
steps:
  - quality_control
  - alignment
hisat2_index: {tmp_path / "genome"}
""",
        encoding="utf-8",
    )
    cfg = load_project_config(config_file)
    samples = samples_from_config(cfg.samples, cfg.project_id)
    context = RunContext(
        project_id=cfg.project_id,
        work_dir=cfg.work_dir,
        output_dir=cfg.output_dir,
        config=cfg.settings,
        dry_run=True,
    )
    repository = JsonStateRepository(cfg.output_dir / "progress.json")
    pipeline = Pipeline(steps=build_pipeline_steps(cfg.steps), repository=repository)

    LocalExecutor(pipeline=pipeline).run(samples, context)

    state = json.loads((cfg.output_dir / "progress.json").read_text(encoding="utf-8"))
    steps = state["samples"]["S1"]["steps"]
    assert list(steps) == ["fastqc", "hisat2", "samtools_sort"]
    assert steps["fastqc"]["status"] == "COMPLETED"
    assert steps["hisat2"]["status"] == "COMPLETED"
    assert steps["samtools_sort"]["status"] == "COMPLETED"


def test_pipeline_emits_step_events(tmp_path):
    fastq = tmp_path / "S1.fastq"
    fastq.write_text("@r1\nACGT\n+\nIIII\n", encoding="utf-8")
    cfg = load_project_config(_write_config(tmp_path, fastq))
    samples = samples_from_config(cfg.samples, cfg.project_id)
    context = RunContext(
        project_id=cfg.project_id,
        work_dir=cfg.work_dir,
        output_dir=cfg.output_dir,
        config=cfg.settings,
        dry_run=True,
    )
    events = []
    repository = JsonStateRepository(cfg.output_dir / "progress.json")
    pipeline = Pipeline(
        steps=build_pipeline_steps(cfg.steps),
        repository=repository,
        event_callback=events.append,
    )

    LocalExecutor(pipeline=pipeline).run(samples, context)

    assert [event.event for event in events] == ["started", "finished", "started", "finished", "started", "finished"]
    assert [event.step_id for event in events if event.event == "finished"] == ["fastqc", "hisat2", "samtools_sort"]


def test_pipeline_emits_skipped_completed_event(tmp_path):
    fastq = tmp_path / "S1.fastq"
    fastq.write_text("@r1\nACGT\n+\nIIII\n", encoding="utf-8")
    cfg = load_project_config(_write_config(tmp_path, fastq))
    samples = samples_from_config(cfg.samples, cfg.project_id)
    context = RunContext(
        project_id=cfg.project_id,
        work_dir=cfg.work_dir,
        output_dir=cfg.output_dir,
        config=cfg.settings,
        dry_run=True,
    )
    repository = JsonStateRepository(cfg.output_dir / "progress.json")
    Pipeline(steps=build_pipeline_steps(cfg.steps), repository=repository).run_sample(samples[0], context)
    events = []
    Pipeline(steps=build_pipeline_steps(cfg.steps), repository=repository, event_callback=events.append).run_sample(
        samples[0], context
    )

    assert [event.event for event in events] == ["skipped_completed", "skipped_completed", "skipped_completed"]


def _write_config(tmp_path, fastq):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        f"""
project_id: demo
output_dir: {tmp_path / "output"}
samples:
  - sample_id: S1
    source_path: {fastq}
    layout: single
steps:
  - quality_control
  - alignment
hisat2_index: {tmp_path / "genome"}
""",
        encoding="utf-8",
    )
    return config_file
