from __future__ import annotations

from rnaseq_workflow.core.models import RunContext, Sample
from rnaseq_workflow.steps.quantification.stringtie import StringTieStep, _stringtie_failure_message, build_stringtie_command


def test_build_stringtie_command_with_gene_abundance():
    command = build_stringtie_command(
        "S1.sorted.bam",
        "genes.gtf",
        "S1.stringtie.gtf",
        "S1.gene_abund.tsv",
    )

    assert command == [
        "stringtie",
        "S1.sorted.bam",
        "-p",
        "2",
        "-G",
        "genes.gtf",
        "-o",
        "S1.stringtie.gtf",
        "-e",
        "-A",
        "S1.gene_abund.tsv",
    ]


def test_stringtie_step_dry_run(tmp_path):
    step = StringTieStep()
    sample = Sample("S1", tmp_path / "S1.bam")
    context = RunContext(
        project_id="demo",
        work_dir=tmp_path,
        output_dir=tmp_path / "out",
        config={"stringtie_annotation": str(tmp_path / "genes.gtf"), "stringtie_threads": 4},
        dry_run=True,
    )

    result = step.run(sample, context)

    assert result.status.value == "COMPLETED"
    assert result.command[:4] == ["stringtie", str(tmp_path / "S1.bam"), "-p", "4"]
    assert result.outputs[0].name == "S1.stringtie.gtf"
    assert result.outputs[1].name == "S1.stringtie.gene_abund.tsv"


def test_stringtie_missing_binary_message_is_actionable():
    message = _stringtie_failure_message(
        'docker: error during container init: exec: "stringtie": executable file not found in $PATH'
    )

    assert "Docker 镜像缺少 stringtie" in message
