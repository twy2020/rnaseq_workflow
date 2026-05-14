from __future__ import annotations

from pathlib import Path
import shutil


def find_cached_sra(accession: str, output_dir: str | Path) -> Path | None:
    root = Path(output_dir)
    candidates = [
        root / f"{accession}.sra",
        root / accession / f"{accession}.sra",
    ]
    candidates.extend(root.glob(f"**/{accession}.sra"))
    for candidate in candidates:
        if candidate.is_file() and candidate.stat().st_size > 0 and not has_partial_markers(accession, candidate.parent):
            return candidate
    return None


def has_partial_markers(accession: str, accession_path: str | Path) -> bool:
    root = Path(accession_path)
    marker_names = [
        f"{accession}.sra.tmp",
        f"{accession}.sra.prf",
        f"{accession}.sra.lock",
        f"{accession}.sralite.tmp",
        f"{accession}.sralite.prf",
        f"{accession}.sralite.lock",
    ]
    if any((root / name).exists() for name in marker_names):
        return True
    return any(root.glob(f"{accession}*.tmp")) or any(root.glob(f"{accession}*.prf")) or any(root.glob(f"{accession}*.lock"))


def find_partial_sra_artifacts(accession: str, output_dir: str | Path) -> list[Path]:
    root = Path(output_dir)
    accession_root = root / accession
    candidates = []
    if accession_root.exists():
        candidates.extend(path for path in accession_root.glob(f"{accession}*") if path.is_file())
    candidates.extend(path for path in root.glob(f"**/{accession}*.tmp") if path.is_file())
    candidates.extend(path for path in root.glob(f"**/{accession}*.prf") if path.is_file())
    candidates.extend(path for path in root.glob(f"**/{accession}*.lock") if path.is_file())
    unique: dict[str, Path] = {}
    for path in candidates:
        unique[str(path.resolve())] = path
    return list(unique.values())


def cleanup_stale_sra_locks(accession: str, output_dir: str | Path) -> list[Path]:
    root = Path(output_dir) / accession
    if not root.exists():
        return []
    removed: list[Path] = []
    for path in root.glob(f"{accession}*.lock"):
        if not path.is_file():
            continue
        try:
            path.unlink()
            removed.append(path)
        except OSError:
            continue
    return removed


def directory_size(path: str | Path) -> int:
    root = Path(path)
    if not root.exists():
        return 0
    total = 0
    for item in root.rglob("*"):
        if item.is_file():
            try:
                total += item.stat().st_size
            except OSError:
                continue
    return total


def accession_size(accession: str, output_dir: str | Path) -> int:
    root = Path(output_dir)
    total = 0
    targets = [
        root / f"{accession}.sra",
        root / accession,
        root / ".tmp" / accession,
    ]
    targets.extend(root.glob(f"**/{accession}*.tmp"))
    targets.extend(root.glob(f"**/{accession}*.prf"))
    targets.extend(root.glob(f"**/{accession}.sra"))
    seen: set[str] = set()
    files: list[Path] = []
    for target in targets:
        if not target.exists():
            continue
        if target.is_file():
            files.append(target)
        elif target.is_dir():
            files.extend(item for item in target.rglob("*") if item.is_file())
    for path in files:
        try:
            resolved = str(path.resolve())
        except OSError:
            resolved = str(path)
        if resolved in seen:
            continue
        seen.add(resolved)
        try:
            total += path.stat().st_size
        except OSError:
            continue
    return total


def accession_dir(accession: str, output_dir: str | Path) -> Path:
    return Path(output_dir) / accession


def lock_path(accession: str, output_dir: str | Path) -> Path:
    lock_dir = Path(output_dir) / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    return lock_dir / f"{accession}.lock"


def cleanup_accession_artifacts(accession: str, output_dir: str | Path) -> None:
    root = Path(output_dir)
    targets = [
        root / f"{accession}.sra",
        root / accession,
        root / ".tmp" / accession,
    ]
    for target in targets:
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        elif target.exists():
            try:
                target.unlink()
            except OSError:
                pass
