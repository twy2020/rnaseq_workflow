from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DoctorCheck:
    name: str
    ok: bool
    message: str


def run_doctor_checks(check_docker_image: bool = True, image: str = "rnaseq-workflow:tools") -> list[DoctorCheck]:
    checks = [
        DoctorCheck("python", True, sys.version.split()[0]),
        _check_command("docker", ["docker", "--version"]),
    ]
    if check_docker_image and shutil.which("docker"):
        checks.append(_check_command("docker image", ["docker", "image", "inspect", image]))
    return checks


def _check_command(name: str, command: list[str]) -> DoctorCheck:
    if shutil.which(command[0]) is None:
        return DoctorCheck(name, False, f"command not found: {command[0]}")
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip() or f"exit code {completed.returncode}"
        return DoctorCheck(name, False, message)
    message = completed.stdout.strip().splitlines()[0] if completed.stdout.strip() else "ok"
    return DoctorCheck(name, True, message)
