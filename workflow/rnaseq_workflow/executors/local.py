from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from rnaseq_workflow.core.models import RunContext, Sample
from rnaseq_workflow.core.pipeline import Pipeline


@dataclass(slots=True)
class LocalExecutor:
    pipeline: Pipeline
    max_workers: int = 1

    def run(self, samples: list[Sample], context: RunContext) -> None:
        if self.max_workers <= 1:
            for sample in samples:
                self.pipeline.run_sample(sample, context)
            return

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = [executor.submit(self.pipeline.run_sample, sample, context) for sample in samples]
            for future in as_completed(futures):
                future.result()
