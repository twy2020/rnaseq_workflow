from __future__ import annotations

import sys
from pathlib import Path

from rnaseq_workflow.core.cancellation import CancellationToken
from rnaseq_workflow.core.command import build_docker_command, run_command, translate_arg_for_docker


def test_run_command_success():
    result = run_command([sys.executable, "-c", "print('ok')"])

    assert result.ok
    assert result.stdout.strip() == "ok"
    assert result.return_code == 0
    assert result.duration_seconds >= 0


def test_run_command_failure():
    result = run_command([sys.executable, "-c", "import sys; sys.exit(2)"])

    assert not result.ok
    assert result.return_code == 2


def test_run_command_dry_run():
    result = run_command(["definitely-not-a-real-command"], dry_run=True)

    assert result.ok
    assert result.dry_run
    assert result.return_code == 0


def test_run_command_can_be_cancelled():
    token = CancellationToken()
    token.cancel()

    result = run_command([sys.executable, "-c", "import time; time.sleep(30)"], cancellation_token=token)

    assert result.return_code == 130
    assert "cancelled" in result.stderr


def test_run_command_drains_large_output_without_deadlock():
    result = run_command(
        [
            sys.executable,
            "-c",
            "import sys; sys.stdout.write('x' * 200000); sys.stderr.write('y' * 200000)",
        ]
    )

    assert result.ok
    assert len(result.stdout) == 200000
    assert len(result.stderr) == 200000


def test_translate_arg_for_docker_inside_workspace(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data = workspace / "data" / "S1.fastq"
    data.parent.mkdir()
    data.write_text("", encoding="utf-8")
    monkeypatch.chdir(workspace)

    assert translate_arg_for_docker("data/S1.fastq", workspace) == "/workspace/data/S1.fastq"


def test_translate_arg_for_docker_leaves_options_and_external_paths(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    assert translate_arg_for_docker("-o", workspace) == "-o"
    assert translate_arg_for_docker(str(tmp_path / "outside.txt"), workspace) == str(tmp_path / "outside.txt")


def test_build_docker_command_translates_workspace_paths(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)

    command = build_docker_command(["fastqc", "reads/S1.fastq"], image="rnaseq-workflow:tools", workspace=workspace)

    assert command[:6] == ["docker", "run", "--rm", "-v", f"{workspace.resolve()}:/workspace", "-w"]
    assert "/workspace/reads/S1.fastq" in command


def test_build_docker_command_mounts_and_translates_extra_paths(tmp_path):
    workspace = tmp_path / "workspace"
    spill = tmp_path / "spill"
    workspace.mkdir()
    spill.mkdir()
    outdir = spill / "users" / "u1" / "tasks" / "t1" / "samples" / "S1" / "raw_fastq"
    outdir.mkdir(parents=True)

    command = build_docker_command(
        ["fasterq-dump", str(workspace / "downloads" / "S1.sra"), "--outdir", str(outdir)],
        image="rnaseq-workflow:tools",
        workspace=workspace,
        extra_mounts=[spill],
        workdir=outdir,
    )

    assert f"{spill.resolve()}:/mnt/rnaseq_extra_0" in command
    assert "/workspace/downloads/S1.sra" in command
    assert "/mnt/rnaseq_extra_0/users/u1/tasks/t1/samples/S1/raw_fastq" in command
    assert command[command.index("-w") + 1] == "/mnt/rnaseq_extra_0/users/u1/tasks/t1/samples/S1/raw_fastq"
