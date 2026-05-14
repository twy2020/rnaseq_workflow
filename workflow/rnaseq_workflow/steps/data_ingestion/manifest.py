from __future__ import annotations

import csv
import json
from pathlib import Path

from rnaseq_workflow.core.models import Sample


def write_manifest_json(samples: list[Sample], output_path: str | Path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [_sample_to_dict(sample) for sample in samples]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_manifest_csv(samples: list[Sample], output_path: str | Path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["sample_id", "layout", "input_type", "source_paths"])
        writer.writeheader()
        for sample in samples:
            writer.writerow(
                {
                    "sample_id": sample.sample_id,
                    "layout": sample.layout.value,
                    "input_type": sample.metadata.get("input_type", ""),
                    "source_paths": ";".join(str(path) for path in sample.source_paths),
                }
            )
    return path


def _sample_to_dict(sample: Sample) -> dict:
    return {
        "sample_id": sample.sample_id,
        "layout": sample.layout.value,
        "project_id": sample.project_id,
        "source_path": str(sample.source_path),
        "source_paths": [str(path) for path in sample.source_paths],
        "metadata": sample.metadata,
    }
