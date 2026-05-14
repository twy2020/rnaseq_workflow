from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class DbUser:
    user_id: str
    username: str
    display_name: str
    created_at: str
    last_login_at: str | None = None


@dataclass(frozen=True, slots=True)
class DbTask:
    task_id: str
    user_id: str
    task_name: str
    description: str
    task_dir: str
    status: str
    created_at: str
    updated_at: str
    reference_id: str | None = None


@dataclass(frozen=True, slots=True)
class DbReference:
    reference_id: str
    owner_user_id: str | None
    scope: str
    reference_dir: str
    provider: str
    annotation_provider: str
    species: str | None
    assembly: str | None
    release: str | None
    taxon_id: str | None
    created_by: str
    build_status: str
    description: str
    created_at: str
    updated_at: str


class AppDatabase:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def init(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL DEFAULT '',
                    password_hash TEXT NOT NULL,
                    password_salt TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_login_at TEXT
                );

                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    task_name TEXT NOT NULL DEFAULT '',
                    description TEXT NOT NULL DEFAULT '',
                    task_dir TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'created',
                    reference_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(user_id)
                );

                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    FOREIGN KEY(user_id) REFERENCES users(user_id)
                );

                CREATE TABLE IF NOT EXISTS references_assets (
                    reference_id TEXT PRIMARY KEY,
                    owner_user_id TEXT,
                    scope TEXT NOT NULL DEFAULT 'shared',
                    reference_dir TEXT NOT NULL,
                    provider TEXT NOT NULL DEFAULT 'custom',
                    annotation_provider TEXT NOT NULL DEFAULT 'custom',
                    species TEXT,
                    assembly TEXT,
                    release TEXT,
                    taxon_id TEXT,
                    created_by TEXT NOT NULL DEFAULT 'manual',
                    build_status TEXT NOT NULL DEFAULT 'unknown',
                    description TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(owner_user_id) REFERENCES users(user_id)
                );
                """
            )
            conn.execute(
                "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )

    def create_user(self, username: str, password: str, display_name: str = "", user_id: str | None = None) -> DbUser:
        username = _normalize_username(username)
        if not password:
            raise ValueError("password cannot be empty")
        now = _now()
        salt, password_hash = _hash_password(password)
        user = DbUser(
            user_id=user_id or str(uuid.uuid4()),
            username=username,
            display_name=display_name.strip(),
            created_at=now,
        )
        self.init()
        try:
            with self.connect() as conn:
                conn.execute(
                    """
                    INSERT INTO users(user_id, username, display_name, password_hash, password_salt, created_at)
                    VALUES(?, ?, ?, ?, ?, ?)
                    """,
                    (user.user_id, user.username, user.display_name, password_hash, salt, user.created_at),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"user already exists: {username}") from exc
        return user

    def ensure_user_record(self, user_id: str, username: str | None = None, display_name: str = "") -> DbUser:
        existing = self.get_user(user_id)
        if existing:
            return existing
        base_username = username or f"user-{user_id}"
        normalized = _safe_generated_username(base_username)
        candidate = normalized
        suffix = 1
        self.init()
        while True:
            try:
                return self.create_user(
                    username=candidate,
                    password=secrets.token_urlsafe(24),
                    display_name=display_name,
                    user_id=user_id,
                )
            except ValueError as exc:
                if "already exists" not in str(exc):
                    raise
                suffix += 1
                candidate = f"{normalized}-{suffix}"

    def authenticate(self, username: str, password: str) -> DbUser | None:
        username = _normalize_username(username)
        self.init()
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
            if row is None:
                return None
            if not _verify_password(password, row["password_salt"], row["password_hash"]):
                return None
            now = _now()
            conn.execute("UPDATE users SET last_login_at = ? WHERE user_id = ?", (now, row["user_id"]))
            return DbUser(
                user_id=row["user_id"],
                username=row["username"],
                display_name=row["display_name"],
                created_at=row["created_at"],
                last_login_at=now,
            )

    def create_session(self, user_id: str) -> str:
        self.init()
        session_id = str(uuid.uuid4())
        now = _now()
        with self.connect() as conn:
            conn.execute("UPDATE sessions SET active = 0 WHERE user_id = ?", (user_id,))
            conn.execute(
                "INSERT INTO sessions(session_id, user_id, created_at, last_seen_at, active) VALUES(?, ?, ?, ?, 1)",
                (session_id, user_id, now, now),
            )
        return session_id

    def get_session_user(self, session_id: str | None) -> DbUser | None:
        if not session_id:
            return None
        self.init()
        now = _now()
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT users.* FROM sessions
                JOIN users ON users.user_id = sessions.user_id
                WHERE sessions.session_id = ? AND sessions.active = 1
                """,
                (session_id,),
            ).fetchone()
            if row is None:
                return None
            conn.execute("UPDATE sessions SET last_seen_at = ? WHERE session_id = ?", (now, session_id))
            return DbUser(
                user_id=row["user_id"],
                username=row["username"],
                display_name=row["display_name"],
                created_at=row["created_at"],
                last_login_at=row["last_login_at"],
            )

    def logout(self, session_id: str | None) -> None:
        if not session_id:
            return
        self.init()
        with self.connect() as conn:
            conn.execute("UPDATE sessions SET active = 0 WHERE session_id = ?", (session_id,))

    def get_user(self, user_id: str) -> DbUser | None:
        self.init()
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        if row is None:
            return None
        return DbUser(
            user_id=row["user_id"],
            username=row["username"],
            display_name=row["display_name"],
            created_at=row["created_at"],
            last_login_at=row["last_login_at"],
        )

    def list_users(self) -> list[DbUser]:
        self.init()
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM users ORDER BY created_at DESC, username").fetchall()
        return [
            DbUser(
                user_id=row["user_id"],
                username=row["username"],
                display_name=row["display_name"],
                created_at=row["created_at"],
                last_login_at=row["last_login_at"],
            )
            for row in rows
        ]

    def upsert_task(
        self,
        *,
        task_id: str,
        user_id: str,
        task_dir: str | Path,
        task_name: str = "",
        description: str = "",
        status: str = "created",
        reference_id: str | None = None,
    ) -> DbTask:
        self.init()
        now = _now()
        with self.connect() as conn:
            existing = conn.execute("SELECT created_at FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
            created_at = existing["created_at"] if existing else now
            conn.execute(
                """
                INSERT INTO tasks(task_id, user_id, task_name, description, task_dir, status, reference_id, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    user_id = excluded.user_id,
                    task_name = excluded.task_name,
                    description = excluded.description,
                    task_dir = excluded.task_dir,
                    status = excluded.status,
                    reference_id = excluded.reference_id,
                    updated_at = excluded.updated_at
                """,
                (
                    task_id,
                    user_id,
                    task_name,
                    description,
                    str(task_dir),
                    status,
                    reference_id,
                    created_at,
                    now,
                ),
            )
        return DbTask(
            task_id=task_id,
            user_id=user_id,
            task_name=task_name,
            description=description,
            task_dir=str(task_dir),
            status=status,
            reference_id=reference_id,
            created_at=created_at,
            updated_at=now,
        )

    def list_tasks(self, user_id: str | None = None) -> list[DbTask]:
        self.init()
        query = "SELECT * FROM tasks"
        args: tuple[str, ...] = ()
        if user_id:
            query += " WHERE user_id = ?"
            args = (user_id,)
        query += " ORDER BY updated_at DESC, created_at DESC"
        with self.connect() as conn:
            rows = conn.execute(query, args).fetchall()
        return [_task_from_row(row) for row in rows]

    def delete_task(self, task_id: str, user_id: str | None = None) -> None:
        self.init()
        query = "DELETE FROM tasks WHERE task_id = ?"
        args: tuple[str, ...] = (task_id,)
        if user_id:
            query += " AND user_id = ?"
            args = (task_id, user_id)
        with self.connect() as conn:
            conn.execute(query, args)

    def upsert_reference(
        self,
        *,
        reference_id: str,
        reference_dir: str | Path,
        provider: str = "custom",
        annotation_provider: str = "custom",
        species: str | None = None,
        assembly: str | None = None,
        release: str | None = None,
        taxon_id: str | None = None,
        owner_user_id: str | None = None,
        scope: str = "shared",
        created_by: str = "manual",
        build_status: str = "unknown",
        description: str = "",
    ) -> DbReference:
        self.init()
        now = _now()
        with self.connect() as conn:
            existing = conn.execute("SELECT created_at FROM references_assets WHERE reference_id = ?", (reference_id,)).fetchone()
            created_at = existing["created_at"] if existing else now
            conn.execute(
                """
                INSERT INTO references_assets(
                    reference_id, owner_user_id, scope, reference_dir, provider, annotation_provider,
                    species, assembly, release, taxon_id, created_by, build_status, description,
                    created_at, updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(reference_id) DO UPDATE SET
                    owner_user_id = excluded.owner_user_id,
                    scope = excluded.scope,
                    reference_dir = excluded.reference_dir,
                    provider = excluded.provider,
                    annotation_provider = excluded.annotation_provider,
                    species = excluded.species,
                    assembly = excluded.assembly,
                    release = excluded.release,
                    taxon_id = excluded.taxon_id,
                    created_by = excluded.created_by,
                    build_status = excluded.build_status,
                    description = excluded.description,
                    updated_at = excluded.updated_at
                """,
                (
                    reference_id,
                    owner_user_id,
                    scope,
                    str(reference_dir),
                    provider,
                    annotation_provider,
                    species,
                    assembly,
                    release,
                    taxon_id,
                    created_by,
                    build_status,
                    description,
                    created_at,
                    now,
                ),
            )
        return DbReference(
            reference_id=reference_id,
            owner_user_id=owner_user_id,
            scope=scope,
            reference_dir=str(reference_dir),
            provider=provider,
            annotation_provider=annotation_provider,
            species=species,
            assembly=assembly,
            release=release,
            taxon_id=taxon_id,
            created_by=created_by,
            build_status=build_status,
            description=description,
            created_at=created_at,
            updated_at=now,
        )

    def list_references(self, owner_user_id: str | None = None) -> list[DbReference]:
        self.init()
        query = "SELECT * FROM references_assets"
        args: tuple[str, ...] = ()
        if owner_user_id is not None:
            query += " WHERE owner_user_id = ? OR scope = 'shared'"
            args = (owner_user_id,)
        query += " ORDER BY updated_at DESC, reference_id"
        with self.connect() as conn:
            rows = conn.execute(query, args).fetchall()
        return [_reference_from_row(row) for row in rows]

    def get_reference(self, reference_id: str) -> DbReference | None:
        self.init()
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM references_assets WHERE reference_id = ?", (reference_id,)).fetchone()
        if row is None:
            return None
        return _reference_from_row(row)

    def delete_reference(self, reference_id: str) -> None:
        self.init()
        with self.connect() as conn:
            conn.execute("DELETE FROM references_assets WHERE reference_id = ?", (reference_id,))


def _task_from_row(row: sqlite3.Row) -> DbTask:
    return DbTask(
        task_id=row["task_id"],
        user_id=row["user_id"],
        task_name=row["task_name"],
        description=row["description"],
        task_dir=row["task_dir"],
        status=row["status"],
        reference_id=row["reference_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _reference_from_row(row: sqlite3.Row) -> DbReference:
    return DbReference(
        reference_id=row["reference_id"],
        owner_user_id=row["owner_user_id"],
        scope=row["scope"],
        reference_dir=row["reference_dir"],
        provider=row["provider"],
        annotation_provider=row["annotation_provider"],
        species=row["species"],
        assembly=row["assembly"],
        release=row["release"],
        taxon_id=row["taxon_id"],
        created_by=row["created_by"],
        build_status=row["build_status"],
        description=row["description"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _normalize_username(username: str) -> str:
    value = username.strip().lower()
    if not value:
        raise ValueError("username cannot be empty")
    if any(char.isspace() for char in value):
        raise ValueError("username cannot contain whitespace")
    return value


def _safe_generated_username(value: str) -> str:
    normalized = "".join(char.lower() if char.isalnum() else "-" for char in value.strip())
    normalized = "-".join(part for part in normalized.split("-") if part)
    return normalized or f"user-{uuid.uuid4()}"


def _hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), 200_000)
    return salt, digest.hex()


def _verify_password(password: str, salt: str, expected_hash: str) -> bool:
    _salt, actual_hash = _hash_password(password, salt)
    return hmac.compare_digest(actual_hash, expected_hash)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")
