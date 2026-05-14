from __future__ import annotations

from rnaseq_workflow.core.doctor import run_doctor_checks


def test_run_doctor_checks_without_docker_image_check():
    checks = run_doctor_checks(check_docker_image=False)

    assert checks[0].name == "python"
    assert checks[0].ok
