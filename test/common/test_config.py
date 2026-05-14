from __future__ import annotations

import pytest

from rnaseq_workflow.core.config import load_project_config
from rnaseq_workflow.core.config_template import ConfigTemplateOptions, build_config_template, write_config_template
from rnaseq_workflow.core.errors import ConfigError


def test_load_project_config(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
project_id: demo
output_dir: output
samples:
  - sample_id: S1
    source_path: data/S1.sra
    layout: paired
steps:
  - data_ingestion
""",
        encoding="utf-8",
    )

    config = load_project_config(config_file)

    assert config.project_id == "demo"
    assert config.output_dir.name == "output"
    assert config.samples[0]["sample_id"] == "S1"
    assert config.steps == ["data_ingestion"]


def test_load_project_config_derives_output_dir_from_asset_workspace(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        f"""
project_id: demo
asset_root: {tmp_path / "workspace"}
user_id: user-1
task_id: task-1
samples:
  - sample_id: S1
    source_path: data/S1.sra
    layout: single
steps:
  - data_ingestion
""",
        encoding="utf-8",
    )

    config = load_project_config(config_file)

    assert config.output_dir == tmp_path / "workspace" / "users" / "user-1" / "tasks" / "task-1"
    assert config.settings["asset_root"] == str(tmp_path / "workspace")
    assert config.settings["user_id"] == "user-1"
    assert config.settings["task_id"] == "task-1"


def test_config_requires_samples(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("project_id: demo\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="at least one sample"):
        load_project_config(config_file)


def test_config_requires_sample_source_path(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
project_id: demo
samples:
  - sample_id: S1
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="source_path"):
        load_project_config(config_file)


def test_build_config_template_can_be_loaded(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        build_config_template(
            ConfigTemplateOptions(
                project_id="demo",
                output_dir="demo_output",
                sample_id="S1",
                source_path="data/S1.fastq",
                layout="single",
            )
        ),
        encoding="utf-8",
    )

    config = load_project_config(config_file)

    assert config.project_id == "demo"
    assert config.output_dir.name == "demo_output"
    assert config.samples[0]["sample_id"] == "S1"
    assert config.settings["execution_mode"] == "docker"
    assert config.settings["featurecounts_annotation"] == "references/demo_reference/annotation.gtf"


def test_write_config_template_refuses_overwrite(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("project_id: existing\n", encoding="utf-8")

    with pytest.raises(FileExistsError):
        write_config_template(config_file)


def test_write_config_template_overwrite(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("project_id: existing\n", encoding="utf-8")

    write_config_template(config_file, options=ConfigTemplateOptions(project_id="new_project"), overwrite=True)

    assert "project_id: new_project" in config_file.read_text(encoding="utf-8")
