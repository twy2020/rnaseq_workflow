from __future__ import annotations

import re
import json
from dataclasses import dataclass
from pathlib import Path

from rnaseq_workflow.core.models import Sample, SampleLayout

FASTQ_SUFFIXES = (".fastq", ".fq", ".fastq.gz", ".fq.gz")
SRA_SUFFIX = ".sra"


@dataclass(frozen=True, slots=True)
class InputScanResult:
    samples: list[Sample]

    @property
    def sample_count(self) -> int:
        return len(self.samples)


def scan_inputs(input_dir: str | Path, project_id: str | None = None) -> InputScanResult:
    root = Path(input_dir)
    if not root.exists():
        raise FileNotFoundError(f"input directory not found: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"input path is not a directory: {root}")

    sra_files = sorted(path for path in root.rglob("*") if path.is_file() and path.name.lower().endswith(SRA_SUFFIX))
    fastq_files = sorted(path for path in root.rglob("*") if path.is_file() and _is_fastq(path))

    samples: list[Sample] = []
    samples.extend(_samples_from_sra(sra_files, project_id))
    samples.extend(_samples_from_fastq(fastq_files, project_id))

    samples.sort(key=lambda sample: sample.sample_id)
    return InputScanResult(samples=samples)


def _samples_from_sra(paths: list[Path], project_id: str | None) -> list[Sample]:
    return [
        Sample(
            sample_id=path.stem,
            source_path=path,
            source_paths=[path],
            layout=SampleLayout.UNKNOWN,
            project_id=project_id,
            metadata=_sample_metadata(path.stem, path, "sra"),
        )
        for path in paths
    ]


def _samples_from_fastq(paths: list[Path], project_id: str | None) -> list[Sample]:
    grouped: dict[str, list[Path]] = {}
    for path in paths:
        sample_id = infer_sample_id_from_fastq(path.name)
        grouped.setdefault(sample_id, []).append(path)

    samples = []
    for sample_id, sample_paths in grouped.items():
        sorted_paths = sorted(sample_paths)
        layout = infer_fastq_layout(sorted_paths)
        samples.append(
            Sample(
                sample_id=sample_id,
                source_path=sorted_paths[0],
                source_paths=sorted_paths,
                layout=layout,
                project_id=project_id,
                metadata=_sample_metadata(sample_id, sorted_paths[0], "fastq"),
            )
        )
    return samples


def _sample_metadata(sample_id: str, source_path: Path, input_type: str) -> dict[str, str]:
    metadata = {"input_type": input_type}
    sidecar = _find_metadata_sidecar(sample_id, source_path)
    if not sidecar:
        return metadata
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return metadata
    for source_key, target_key in (
        ("run", "sra_run"),
        ("bioproject", "bioproject"),
        ("biosample", "biosample"),
        ("taxid", "taxid"),
        ("scientific_name", "scientific_name"),
        ("library_strategy", "library_strategy"),
        ("library_source", "library_source"),
        ("library_layout", "library_layout"),
        ("platform", "platform"),
        ("model", "model"),
        ("metadata_source", "metadata_source"),
        ("metadata_fetched_at", "metadata_fetched_at"),
    ):
        value = data.get(source_key)
        if value:
            metadata[target_key] = str(value)
    metadata["metadata_sidecar"] = str(sidecar)
    return metadata


def _find_metadata_sidecar(sample_id: str, source_path: Path) -> Path | None:
    candidates = [
        source_path.parent / "metadata.json",
        source_path.parent / sample_id / "metadata.json",
        source_path.parent.parent / sample_id / "metadata.json",
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


def infer_fastq_layout(paths: list[Path]) -> SampleLayout:
    if len(paths) >= 2 and _has_pair_markers(paths):
        return SampleLayout.PAIRED
    if len(paths) == 1:
        return SampleLayout.SINGLE
    return SampleLayout.UNKNOWN


def infer_sample_id_from_fastq(filename: str) -> str:
    name = _strip_fastq_suffix(filename)
    patterns = [
        r"(.+?)(?:[_\.-]R?1)(?:[_\.-].*)?$",
        r"(.+?)(?:[_\.-]R?2)(?:[_\.-].*)?$",
    ]
    for pattern in patterns:
        match = re.match(pattern, name, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return name


def _has_pair_markers(paths: list[Path]) -> bool:
    names = [_strip_fastq_suffix(path.name) for path in paths]
    has_r1 = any(re.search(r"(^|[_\.-])R?1([_\.-]|$)", name, flags=re.IGNORECASE) for name in names)
    has_r2 = any(re.search(r"(^|[_\.-])R?2([_\.-]|$)", name, flags=re.IGNORECASE) for name in names)
    return has_r1 and has_r2


def _is_fastq(path: Path) -> bool:
    lower = path.name.lower()
    return any(lower.endswith(suffix) for suffix in FASTQ_SUFFIXES)


def _strip_fastq_suffix(filename: str) -> str:
    lower = filename.lower()
    for suffix in (".fastq.gz", ".fq.gz", ".fastq", ".fq"):
        if lower.endswith(suffix):
            return filename[: -len(suffix)]
    return filename
