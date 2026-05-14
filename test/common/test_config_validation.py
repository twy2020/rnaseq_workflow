from __future__ import annotations

from rnaseq_workflow.core.config import ProjectConfig
from rnaseq_workflow.core.config_validation import validate_project_config


def test_validate_project_config_requires_alignment_index(tmp_path):
    config = ProjectConfig(
        project_id="demo",
        work_dir=tmp_path,
        output_dir=tmp_path / "output",
        samples=[{"sample_id": "S1", "source_path": "S1.fastq", "layout": "single"}],
        steps=["alignment"],
        settings={},
    )

    result = validate_project_config(config, check_files=False)

    assert not result.ok
    assert result.errors[0].field == "hisat2_index"


def test_validate_project_config_requires_featurecounts_annotation(tmp_path):
    config = ProjectConfig(
        project_id="demo",
        work_dir=tmp_path,
        output_dir=tmp_path / "output",
        samples=[{"sample_id": "S1", "source_path": "S1.fastq", "layout": "single"}],
        steps=["quantification"],
        settings={},
    )

    result = validate_project_config(config, check_files=False)

    assert not result.ok
    assert result.errors[0].field == "featurecounts_annotation"


def test_validate_project_config_checks_sample_files(tmp_path):
    config = ProjectConfig(
        project_id="demo",
        work_dir=tmp_path,
        output_dir=tmp_path / "output",
        samples=[{"sample_id": "S1", "source_path": "missing.fastq", "layout": "single"}],
        steps=["quality_control"],
        settings={},
    )

    result = validate_project_config(config, check_files=True)

    assert not result.ok
    assert result.errors[0].field == "samples[1].source_path"


def test_validate_project_config_checks_paired_sample_file_count(tmp_path):
    config = ProjectConfig(
        project_id="demo",
        work_dir=tmp_path,
        output_dir=tmp_path / "output",
        samples=[{"sample_id": "S1", "source_path": "S1_R1.fastq", "layout": "paired"}],
        steps=["quality_control"],
        settings={},
    )

    result = validate_project_config(config, check_files=False)

    assert not result.ok
    assert result.errors[0].field == "samples[1].source_paths"


def test_validate_project_config_accepts_real_minimal_files(tmp_path):
    fastq = tmp_path / "S1.fastq"
    fastq.write_text("@r\nACGT\n+\nIIII\n", encoding="utf-8")
    annotation = tmp_path / "genes.gtf"
    annotation.write_text("chr1\tdemo\texon\t1\t4\t.\t+\t.\tgene_id \"g1\";\n", encoding="utf-8")
    for idx in range(1, 9):
        (tmp_path / f"genome.{idx}.ht2").write_text("", encoding="utf-8")
    config = ProjectConfig(
        project_id="demo",
        work_dir=tmp_path,
        output_dir=tmp_path / "output",
        samples=[{"sample_id": "S1", "source_path": "S1.fastq", "layout": "single"}],
        steps=["quality_control", "alignment", "quantification"],
        settings={"hisat2_index": "genome", "featurecounts_annotation": "genes.gtf"},
    )

    result = validate_project_config(config, check_files=True)

    assert result.ok


def test_validate_project_config_checks_docker_workspace_contains_paths(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.fastq"
    outside.write_text("@r\nACGT\n+\nIIII\n", encoding="utf-8")
    config = ProjectConfig(
        project_id="demo",
        work_dir=tmp_path,
        output_dir=tmp_path / "output",
        samples=[{"sample_id": "S1", "source_path": str(outside), "layout": "single"}],
        steps=["quality_control"],
        settings={"execution_mode": "docker", "docker_workspace": str(workspace)},
    )

    result = validate_project_config(config, check_files=True)

    assert not result.ok
    assert "docker_workspace" in result.errors[0].message
