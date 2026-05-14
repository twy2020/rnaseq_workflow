from __future__ import annotations

import yaml

from rnaseq_workflow.core.config_edit import set_config_value


def test_set_config_value_top_level(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("project_id: demo\nhisat2_threads: 2\n", encoding="utf-8")

    set_config_value(config, "hisat2_threads", "8")

    data = yaml.safe_load(config.read_text(encoding="utf-8"))
    assert data["hisat2_threads"] == 8


def test_set_config_value_nested_list_item(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        """
samples:
  - sample_id: S1
    source_path: old.fastq
""",
        encoding="utf-8",
    )

    set_config_value(config, "samples.0.source_path", "new.fastq")

    data = yaml.safe_load(config.read_text(encoding="utf-8"))
    assert data["samples"][0]["source_path"] == "new.fastq"


def test_set_config_value_bool(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("featurecounts_paired: false\n", encoding="utf-8")

    set_config_value(config, "featurecounts_paired", "true")

    data = yaml.safe_load(config.read_text(encoding="utf-8"))
    assert data["featurecounts_paired"] is True
