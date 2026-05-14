from __future__ import annotations

from pathlib import Path

from rnaseq_workflow.core.models import StepResult, StepStatus


def test_step_result_to_record():
    result = StepResult(
        sample_id="S1",
        step_id="qc",
        status=StepStatus.COMPLETED,
        message="done",
        command=["fastqc", "S1.fastq.gz"],
        return_code=0,
        inputs=[Path("S1.fastq.gz")],
        outputs=[Path("qc/S1.html")],
    )

    record = result.to_record("Quality control")

    assert record.sample_id == "S1"
    assert record.step_name == "Quality control"
    assert record.status == StepStatus.COMPLETED
    assert record.inputs == ["S1.fastq.gz"]
    assert record.outputs == [str(Path("qc/S1.html"))]
