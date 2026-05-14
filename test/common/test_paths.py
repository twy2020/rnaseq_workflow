from __future__ import annotations

from rnaseq_workflow.core.models import Sample
from rnaseq_workflow.core.paths import project_paths


def test_project_paths_generate_standard_directories(tmp_path):
    paths = project_paths(tmp_path / "output")
    sample = Sample(sample_id="S1", source_path=tmp_path / "S1.sra")

    assert paths.logs_dir == tmp_path / "output" / "logs"
    assert paths.raw_fastq_dir(sample) == tmp_path / "output" / "samples" / "S1" / "raw_fastq"
    assert paths.alignment_dir("S1") == tmp_path / "output" / "samples" / "S1" / "alignment"
    assert paths.state_file == tmp_path / "output" / "progress.json"


def test_project_paths_create_directories(tmp_path):
    paths = project_paths(tmp_path / "output")
    paths.ensure_base_dirs()
    paths.ensure_sample_dirs("S1")

    assert paths.reports_dir.is_dir()
    assert paths.quantification_dir("S1").is_dir()
