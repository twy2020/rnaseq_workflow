from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from rnaseq_workflow.core.models import Sample


@dataclass(frozen=True, slots=True)
class ProjectPaths:
    root: Path

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    @property
    def samples_dir(self) -> Path:
        return self.root / "samples"

    @property
    def reports_dir(self) -> Path:
        return self.root / "reports"

    @property
    def state_file(self) -> Path:
        return self.root / "progress.json"

    def sample_dir(self, sample: Sample | str) -> Path:
        sample_id = sample.sample_id if isinstance(sample, Sample) else sample
        return self.samples_dir / sample_id

    def raw_fastq_dir(self, sample: Sample | str) -> Path:
        return self.sample_dir(sample) / "raw_fastq"

    def raw_qc_dir(self, sample: Sample | str) -> Path:
        return self.sample_dir(sample) / "qc_raw"

    def trimmed_fastq_dir(self, sample: Sample | str) -> Path:
        return self.sample_dir(sample) / "trimmed_fastq"

    def trimmed_qc_dir(self, sample: Sample | str) -> Path:
        return self.sample_dir(sample) / "qc_trimmed"

    def alignment_dir(self, sample: Sample | str) -> Path:
        return self.sample_dir(sample) / "alignment"

    def quantification_dir(self, sample: Sample | str) -> Path:
        return self.sample_dir(sample) / "quantification"

    def ensure_base_dirs(self) -> None:
        for path in (self.root, self.logs_dir, self.samples_dir, self.reports_dir):
            path.mkdir(parents=True, exist_ok=True)

    def ensure_sample_dirs(self, sample: Sample | str) -> None:
        for path in (
            self.raw_fastq_dir(sample),
            self.raw_qc_dir(sample),
            self.trimmed_fastq_dir(sample),
            self.trimmed_qc_dir(sample),
            self.alignment_dir(sample),
            self.quantification_dir(sample),
        ):
            path.mkdir(parents=True, exist_ok=True)


def project_paths(output_dir: Path) -> ProjectPaths:
    return ProjectPaths(root=output_dir)
