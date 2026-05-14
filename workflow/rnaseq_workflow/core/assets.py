from __future__ import annotations

import json
import shutil
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from rnaseq_workflow.core.app_db import AppDatabase


DEFAULT_ASSET_ROOT = Path("workspace")
TASK_METADATA = "task.json"
SESSION_FILE = "session.json"
ALLOWED_CLEANUP_DIRS = (".pytest_cache", "downloads", "reference_downloads", "runtime_logs")


@dataclass(frozen=True, slots=True)
class TaskMetadata:
    user_id: str
    task_id: str
    task_name: str = ""
    description: str = ""
    created_at: str = ""
    status: str = "created"
    reference_id: str | None = None
    sample_groups: list[dict] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class CleanupTarget:
    path: Path
    exists: bool
    file_count: int = 0
    size_bytes: int = 0
    allowed: bool = True


@dataclass(frozen=True, slots=True)
class TaskWorkspace:
    root: Path
    user_id: str
    task_id: str

    @property
    def inputs_dir(self) -> Path:
        return self.root / "inputs"

    @property
    def downloads_dir(self) -> Path:
        return self.root / "downloads"

    @property
    def samples_dir(self) -> Path:
        return self.root / "samples"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    @property
    def reports_dir(self) -> Path:
        return self.root / "reports"

    @property
    def metadata_dir(self) -> Path:
        return self.root / "metadata"

    @property
    def metadata_path(self) -> Path:
        return self.metadata_dir / TASK_METADATA

    @property
    def progress_path(self) -> Path:
        return self.root / "progress.json"

    @property
    def task_output_dir(self) -> Path:
        return self.root

    def ensure(self) -> None:
        for path in (
            self.root,
            self.inputs_dir,
            self.downloads_dir,
            self.samples_dir,
            self.logs_dir,
            self.reports_dir,
            self.metadata_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def write_metadata(
        self,
        task_name: str = "",
        description: str = "",
        status: str = "created",
        reference_id: str | None = None,
        sample_groups: list[dict] | None = None,
    ) -> TaskMetadata:
        metadata = TaskMetadata(
            user_id=self.user_id,
            task_id=self.task_id,
            task_name=task_name,
            description=description,
            created_at=datetime.now().isoformat(timespec="seconds"),
            status=status,
            reference_id=reference_id,
            sample_groups=sample_groups or [],
        )
        self.ensure()
        self.metadata_path.write_text(json.dumps(asdict(metadata), ensure_ascii=False, indent=2), encoding="utf-8")
        return metadata

    def read_metadata(self) -> TaskMetadata | None:
        if not self.metadata_path.exists():
            return None
        data = json.loads(self.metadata_path.read_text(encoding="utf-8"))
        return TaskMetadata(**data)

    def update_metadata(
        self,
        task_name: str | None = None,
        description: str | None = None,
        status: str | None = None,
        reference_id: str | None = None,
        sample_groups: list[dict] | None = None,
    ) -> TaskMetadata:
        current = self.read_metadata()
        metadata = TaskMetadata(
            user_id=self.user_id,
            task_id=self.task_id,
            task_name=current.task_name if task_name is None and current else task_name or "",
            description=current.description if description is None and current else description or "",
            created_at=current.created_at if current else datetime.now().isoformat(timespec="seconds"),
            status=current.status if status is None and current else status or "created",
            reference_id=current.reference_id if reference_id is None and current else reference_id,
            sample_groups=current.sample_groups if sample_groups is None and current else sample_groups or [],
        )
        self.ensure()
        self.metadata_path.write_text(json.dumps(asdict(metadata), ensure_ascii=False, indent=2), encoding="utf-8")
        return metadata


@dataclass(frozen=True, slots=True)
class UserWorkspace:
    root: Path
    user_id: str

    @property
    def tasks_dir(self) -> Path:
        return self.root / "tasks"

    @property
    def user_reference_dir(self) -> Path:
        return self.root / "references"

    def ensure(self) -> None:
        for path in (self.root, self.tasks_dir, self.user_reference_dir):
            path.mkdir(parents=True, exist_ok=True)

    def task(self, task_id: str) -> TaskWorkspace:
        return TaskWorkspace(root=self.tasks_dir / task_id, user_id=self.user_id, task_id=task_id)

    def create_task(self, task_name: str = "", description: str = "") -> TaskWorkspace:
        self.ensure()
        task = self.task(str(uuid.uuid4()))
        task.write_metadata(task_name=task_name, description=description)
        return task

    def list_tasks(self) -> list[TaskWorkspace]:
        if not self.tasks_dir.exists():
            return []
        return [
            self.task(path.name)
            for path in sorted(self.tasks_dir.iterdir())
            if path.is_dir()
        ]

    def delete_task(self, task_id: str) -> None:
        task = self.task(task_id)
        base = self.tasks_dir.resolve()
        target = task.root.resolve()
        if target.parent != base:
            raise ValueError(f"refuse to delete task outside tasks dir: {target}")
        if target.exists():
            shutil.rmtree(target)


@dataclass(frozen=True, slots=True)
class AssetWorkspace:
    root: Path = DEFAULT_ASSET_ROOT

    @property
    def shared_dir(self) -> Path:
        return self.root / "shared"

    @property
    def global_reference_dir(self) -> Path:
        return self.shared_dir / "references"

    @property
    def global_reference_downloads_dir(self) -> Path:
        return self.shared_dir / "reference_downloads"

    @property
    def users_dir(self) -> Path:
        return self.root / "users"

    @property
    def database_path(self) -> Path:
        return self.root / "app.db"

    @property
    def session_path(self) -> Path:
        return self.root / SESSION_FILE

    @property
    def database(self) -> AppDatabase:
        return AppDatabase(self.database_path)

    def ensure(self) -> None:
        for path in (self.root, self.shared_dir, self.global_reference_dir, self.global_reference_downloads_dir, self.users_dir):
            path.mkdir(parents=True, exist_ok=True)
        self.database.init()

    def user(self, user_id: str) -> UserWorkspace:
        return UserWorkspace(root=self.users_dir / user_id, user_id=user_id)

    def ensure_user(self, user_id: str) -> UserWorkspace:
        self.ensure()
        self.database.ensure_user_record(user_id)
        user = self.user(user_id)
        user.ensure()
        return user

    def save_session(self, session_id: str, user_id: str) -> None:
        self.ensure()
        self.session_path.write_text(
            json.dumps({"session_id": session_id, "user_id": user_id}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def load_session(self) -> dict | None:
        if not self.session_path.exists():
            return None
        return json.loads(self.session_path.read_text(encoding="utf-8"))

    def clear_session(self) -> None:
        if self.session_path.exists():
            self.session_path.unlink()


def generate_user_id() -> str:
    return str(uuid.uuid4())


def build_asset_workspace(root: str | Path | None = None) -> AssetWorkspace:
    return AssetWorkspace(root=Path(root) if root else DEFAULT_ASSET_ROOT)


def reference_search_dirs(workspace: AssetWorkspace, user_id: str | None = None) -> list[Path]:
    dirs: list[Path] = []
    if user_id:
        dirs.append(workspace.user(user_id).user_reference_dir)
    dirs.append(workspace.global_reference_dir)
    return dirs


def cleanup_plan(root: str | Path = ".", names: tuple[str, ...] = ALLOWED_CLEANUP_DIRS) -> list[CleanupTarget]:
    base = Path(root).resolve()
    targets: list[CleanupTarget] = []
    for name in names:
        path = (base / name).resolve()
        allowed = path.parent == base and path.name in ALLOWED_CLEANUP_DIRS
        if not path.exists():
            targets.append(CleanupTarget(path=path, exists=False, allowed=allowed))
            continue
        files = [item for item in path.rglob("*") if item.is_file()]
        targets.append(
            CleanupTarget(
                path=path,
                exists=True,
                file_count=len(files),
                size_bytes=sum(item.stat().st_size for item in files),
                allowed=allowed,
            )
        )
    return targets


def cleanup_allowed_targets(root: str | Path = ".", dry_run: bool = True) -> list[CleanupTarget]:
    targets = cleanup_plan(root)
    if dry_run:
        return targets
    for target in targets:
        if target.exists and target.allowed:
            shutil.rmtree(target.path)
    return targets
