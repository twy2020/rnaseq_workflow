from __future__ import annotations

import subprocess
import tempfile
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Thread
from typing import TYPE_CHECKING, Callable, Iterator, TextIO

if TYPE_CHECKING:
    from rnaseq_workflow.core.models import RunContext


@dataclass(slots=True)
class CommandResult:
    command: list[str]
    return_code: int
    stdout: str
    stderr: str
    started_at: str
    finished_at: str
    duration_seconds: float
    dry_run: bool = False

    @property
    def ok(self) -> bool:
        return self.return_code == 0


_COMMAND_RESULTS: ContextVar[list[CommandResult] | None] = ContextVar("rnaseq_command_results", default=None)


@dataclass(frozen=True, slots=True)
class DockerMount:
    host_path: Path
    container_path: str


def run_command(
    command: list[str],
    cwd: Path | None = None,
    dry_run: bool = False,
    cancellation_token=None,
    completion_check: Callable[[], bool] | None = None,
    completion_message: str = "command output completed",
    completion_callback: Callable[[], None] | None = None,
) -> CommandResult:
    started_at = datetime.now()
    if dry_run:
        finished_at = datetime.now()
        return CommandResult(
            command=command,
            return_code=0,
            stdout="",
            stderr="",
            started_at=started_at.isoformat(timespec="seconds"),
            finished_at=finished_at.isoformat(timespec="seconds"),
            duration_seconds=(finished_at - started_at).total_seconds(),
            dry_run=True,
        )

    finished_at = datetime.now()
    try:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError as exc:
        return CommandResult(
            command=command,
            return_code=127,
            stdout="",
            stderr=f"command not found: {command[0]} ({exc})",
            started_at=started_at.isoformat(timespec="seconds"),
            finished_at=finished_at.isoformat(timespec="seconds"),
            duration_seconds=(finished_at - started_at).total_seconds(),
        )
    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    stdout_reader = Thread(target=_read_stream, args=(process.stdout, stdout_chunks), daemon=True)
    stderr_reader = Thread(target=_read_stream, args=(process.stderr, stderr_chunks), daemon=True)
    stdout_reader.start()
    stderr_reader.start()
    while process.poll() is None:
        if completion_check is not None and completion_check():
            if completion_callback is not None:
                completion_callback()
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            stdout_reader.join(timeout=2)
            stderr_reader.join(timeout=2)
            finished_at = datetime.now()
            stderr = "".join(stderr_chunks)
            if completion_message:
                stderr = (stderr + "\n" + completion_message).strip()
            return CommandResult(
                command=command,
                return_code=0,
                stdout="".join(stdout_chunks),
                stderr=stderr,
                started_at=started_at.isoformat(timespec="seconds"),
                finished_at=finished_at.isoformat(timespec="seconds"),
                duration_seconds=(finished_at - started_at).total_seconds(),
            )
        if cancellation_token is not None and cancellation_token.is_cancelled():
            if completion_callback is not None:
                completion_callback()
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            stdout_reader.join(timeout=2)
            stderr_reader.join(timeout=2)
            finished_at = datetime.now()
            return CommandResult(
                command=command,
                return_code=130,
                stdout="".join(stdout_chunks),
                stderr="".join(stderr_chunks) + "\ncommand cancelled",
                started_at=started_at.isoformat(timespec="seconds"),
                finished_at=finished_at.isoformat(timespec="seconds"),
                duration_seconds=(finished_at - started_at).total_seconds(),
            )
        time.sleep(0.2)
    stdout_reader.join(timeout=5)
    stderr_reader.join(timeout=5)
    finished_at = datetime.now()
    return CommandResult(
        command=command,
        return_code=process.returncode,
        stdout="".join(stdout_chunks),
        stderr="".join(stderr_chunks),
        started_at=started_at.isoformat(timespec="seconds"),
        finished_at=finished_at.isoformat(timespec="seconds"),
        duration_seconds=(finished_at - started_at).total_seconds(),
    )


def _read_stream(stream: TextIO | None, chunks: list[str]) -> None:
    if stream is None:
        return
    try:
        for chunk in iter(lambda: stream.read(8192), ""):
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        stream.close()


def run_context_command(
    command: list[str],
    context: "RunContext",
    cwd: Path | None = None,
    completion_check: Callable[[], bool] | None = None,
    completion_message: str = "command output completed",
) -> CommandResult:
    cancellation_token = context.config.get("cancellation_token")
    execution_mode = str(context.config.get("execution_mode", "local")).lower()
    if execution_mode in {"docker", "container"}:
        image = str(context.config.get("docker_image", "rnaseq-workflow:tools"))
        workspace = Path(context.config.get("docker_workspace", Path.cwd())).resolve()
        docker_command = build_docker_command(
            command,
            image=image,
            workspace=workspace,
            extra_mounts=[Path(path) for path in context.config.get("docker_extra_mounts", []) if str(path or "").strip()],
            workdir=cwd,
        )
        cidfile = _docker_cidfile() if not context.dry_run else None
        if cidfile is not None:
            docker_command[3:3] = ["--cidfile", str(cidfile)]
        try:
            result = run_command(
                docker_command,
                cwd=cwd,
                dry_run=context.dry_run,
                cancellation_token=cancellation_token,
                completion_check=completion_check,
                completion_message=completion_message,
                completion_callback=(lambda: _stop_docker_container_from_cidfile(cidfile)) if cidfile is not None else None,
            )
            _record_context_command(result)
            return result
        finally:
            if cidfile is not None:
                try:
                    cidfile.unlink()
                except OSError:
                    pass
    result = run_command(
        command,
        cwd=cwd,
        dry_run=context.dry_run,
        cancellation_token=cancellation_token,
        completion_check=completion_check,
        completion_message=completion_message,
    )
    _record_context_command(result)
    return result


@contextmanager
def collect_context_commands() -> Iterator[list[CommandResult]]:
    results: list[CommandResult] = []
    token = _COMMAND_RESULTS.set(results)
    try:
        yield results
    finally:
        _COMMAND_RESULTS.reset(token)


def _record_context_command(result: CommandResult) -> None:
    results = _COMMAND_RESULTS.get()
    if results is not None:
        results.append(result)


def build_docker_command(
    command: list[str],
    image: str,
    workspace: str | Path,
    extra_mounts: list[str | Path] | None = None,
    workdir: str | Path | None = None,
) -> list[str]:
    workspace_path = Path(workspace).resolve()
    mounts = _docker_mounts(workspace_path, extra_mounts or [])
    translated = [translate_arg_for_docker(arg, workspace_path, mounts[1:]) for arg in command]
    docker_workdir = translate_path_for_docker(workdir, workspace_path, mounts[1:]) if workdir else "/workspace"
    docker_command = [
        "docker",
        "run",
        "--rm",
    ]
    for mount in mounts:
        docker_command.extend(
            [
                "-v",
                f"{mount.host_path}:{mount.container_path}",
            ]
        )
    docker_command.extend(
        [
            "-w",
            docker_workdir,
            image,
            *translated,
        ]
    )
    return docker_command


def _docker_mounts(workspace_path: Path, extra_mounts: list[str | Path]) -> list[DockerMount]:
    mounts = [DockerMount(workspace_path.resolve(), "/workspace")]
    seen = {str(workspace_path.resolve()).lower()}
    for raw in extra_mounts:
        host = Path(raw).expanduser().resolve()
        try:
            host.relative_to(workspace_path)
            continue
        except ValueError:
            pass
        key = str(host).lower()
        if key in seen:
            continue
        seen.add(key)
        mounts.append(DockerMount(host, f"/mnt/rnaseq_extra_{len(mounts) - 1}"))
    return mounts


def translate_path_for_docker(path: str | Path, workspace: str | Path, extra_mounts: list[DockerMount] | None = None) -> str:
    workspace_path = Path(workspace).resolve()
    raw_path = Path(path)
    resolved = raw_path.resolve() if raw_path.is_absolute() else (Path.cwd() / raw_path).resolve()
    try:
        relative = resolved.relative_to(workspace_path)
    except ValueError:
        pass
    else:
        return "/workspace/" + relative.as_posix()
    for mount in extra_mounts or []:
        try:
            relative = resolved.relative_to(mount.host_path)
        except ValueError:
            continue
        return f"{mount.container_path}/{relative.as_posix()}"
    return str(path)


def translate_arg_for_docker(arg: str, workspace: str | Path, extra_mounts: list[DockerMount] | None = None) -> str:
    if not _looks_like_path(arg):
        return arg
    return translate_path_for_docker(arg, workspace, extra_mounts)


def _looks_like_path(arg: str) -> bool:
    if not arg or arg.startswith("-"):
        return False
    return "/" in arg or "\\" in arg or arg.startswith(".")


def _docker_cidfile() -> Path:
    return Path(tempfile.gettempdir()) / f"rnaseq_workflow_{uuid.uuid4().hex}.cid"


def _stop_docker_container_from_cidfile(cidfile: Path) -> None:
    try:
        container_id = cidfile.read_text(encoding="utf-8").strip()
    except OSError:
        return
    if not container_id:
        return
    subprocess.run(["docker", "rm", "-f", container_id], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
