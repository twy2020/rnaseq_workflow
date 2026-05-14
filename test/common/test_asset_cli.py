from __future__ import annotations

from typer.testing import CliRunner

from rnaseq_workflow.cli.main import app


def test_assets_init_command_creates_workspace(tmp_path):
    runner = CliRunner()

    result = runner.invoke(app, ["assets-init", "--asset-root", str(tmp_path / "workspace")])

    assert result.exit_code == 0
    assert (tmp_path / "workspace" / "shared" / "references").exists()
    assert (tmp_path / "workspace" / "users").exists()
    assert (tmp_path / "workspace" / "app.db").exists()


def test_user_register_login_and_list_commands(tmp_path):
    runner = CliRunner()
    workspace = tmp_path / "workspace"

    register = runner.invoke(
        app,
        ["user-register", "alice", "--display-name", "Alice", "--asset-root", str(workspace)],
        input="secret\nsecret\n",
    )
    assert register.exit_code == 0

    login = runner.invoke(
        app,
        ["user-login", "alice", "--asset-root", str(workspace)],
        input="secret\n",
    )
    assert login.exit_code == 0
    assert (workspace / "session.json").exists()

    users = runner.invoke(app, ["users-list", "--asset-root", str(workspace)])
    assert users.exit_code == 0
    assert "alice" in users.output


def test_cleanup_test_artifacts_dry_run_keeps_files(tmp_path):
    runner = CliRunner()
    runtime = tmp_path / "runtime_logs"
    runtime.mkdir()
    (runtime / "a.txt").write_text("hello", encoding="utf-8")

    result = runner.invoke(app, ["cleanup-test-artifacts", "--root", str(tmp_path)])

    assert result.exit_code == 0
    assert "Dry-run only" in result.output
    assert (runtime / "a.txt").exists()
