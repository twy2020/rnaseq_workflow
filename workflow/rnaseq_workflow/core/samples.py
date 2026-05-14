from __future__ import annotations

from pathlib import Path

from rnaseq_workflow.core.models import Sample, SampleLayout


def sample_from_config(item: dict, project_id: str) -> Sample:
    layout = SampleLayout(item.get("layout", SampleLayout.UNKNOWN.value))
    raw_paths = item.get("source_paths") or [item["source_path"]]
    source_paths = [Path(path) for path in raw_paths]
    return Sample(
        sample_id=str(item["sample_id"]),
        source_path=source_paths[0],
        layout=layout,
        project_id=project_id,
        source_paths=source_paths,
        metadata={key: value for key, value in item.items() if key not in {"sample_id", "source_path", "layout"}},
    )


def samples_from_config(items: list[dict], project_id: str) -> list[Sample]:
    return [sample_from_config(item, project_id) for item in items]
