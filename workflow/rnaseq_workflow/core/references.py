from __future__ import annotations

import json
import re
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable

from rnaseq_workflow.core.app_db import AppDatabase
from rnaseq_workflow.core.command import CommandResult, run_context_command
from rnaseq_workflow.core.models import RunContext
from rnaseq_workflow.steps.alignment.hisat2 import hisat2_index_exists


REFERENCE_METADATA = "reference.json"


@dataclass(frozen=True, slots=True)
class ReferenceCheckIssue:
    level: str
    field: str
    message: str


@dataclass(frozen=True, slots=True)
class ReferenceCheckReport:
    reference_id: str
    ok: bool
    issues: list[ReferenceCheckIssue] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ReferenceAsset:
    reference_id: str
    root: Path
    fasta: Path
    annotation: Path | None
    hisat2_index: Path
    created_at: str
    updated_at: str
    provider: str = "custom"
    annotation_provider: str = "custom"
    species: str | None = None
    assembly: str | None = None
    release: str | None = None
    taxon_id: str | None = None
    source_urls: list[str] = field(default_factory=list)
    annotation_format: str | None = None
    created_by: str = "manual"
    build_status: str = "unknown"
    warnings: list[str] = field(default_factory=list)
    notes: str = ""


def normalize_reference_id(reference_id: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "_", reference_id.strip())
    if not normalized:
        raise ValueError("reference_id cannot be empty")
    return normalized


def reference_root(reference_dir: str | Path, reference_id: str) -> Path:
    return Path(reference_dir) / normalize_reference_id(reference_id)


def register_reference(
    reference_id: str,
    fasta: str | Path,
    annotation: str | Path | None = None,
    hisat2_index: str | Path | None = None,
    reference_dir: str | Path = "references",
    copy_files: bool = True,
    overwrite: bool = False,
    notes: str = "",
    *,
    provider: str = "custom",
    annotation_provider: str | None = None,
    species: str | None = None,
    assembly: str | None = None,
    release: str | None = None,
    taxon_id: str | None = None,
    source_urls: Iterable[str] | None = None,
    annotation_format: str | None = None,
    created_by: str = "manual",
    build_status: str = "registered",
    warnings: Iterable[str] | None = None,
    allow_mixed_source: bool = False,
) -> ReferenceAsset:
    ref_id = normalize_reference_id(reference_id)
    root = reference_root(reference_dir, ref_id)
    metadata_path = root / REFERENCE_METADATA
    if metadata_path.exists() and not overwrite:
        raise FileExistsError(f"reference already exists: {ref_id}")

    fasta_path = Path(fasta)
    if not fasta_path.exists():
        raise FileNotFoundError(f"FASTA file not found: {fasta_path}")
    annotation_path = Path(annotation) if annotation else None
    if annotation_path and not annotation_path.exists():
        raise FileNotFoundError(f"annotation file not found: {annotation_path}")
    hisat2_index_path = Path(hisat2_index) if hisat2_index else None
    if hisat2_index_path and not hisat2_index_exists(hisat2_index_path):
        raise FileNotFoundError(f"HISAT2 index files not found for prefix: {hisat2_index_path}")
    normalized_provider = normalize_source_provider(provider)
    normalized_annotation_provider = normalize_source_provider(annotation_provider or provider)
    merged_warning = ""
    if annotation_path and _source_family(normalized_provider) != _source_family(normalized_annotation_provider):
        if not allow_mixed_source:
            raise ValueError(
                "FASTA provider and annotation provider must share the same source family "
                f"unless allow_mixed_source=True: {provider} vs {annotation_provider or provider}"
            )
        merged_warning = (
            f"mixed source families allowed: FASTA={normalized_provider}, annotation={normalized_annotation_provider}"
        )

    root.mkdir(parents=True, exist_ok=True)
    managed_fasta = _copy_or_point(fasta_path, root / _managed_fasta_name(fasta_path), copy_files)
    managed_annotation = None
    if annotation_path:
        managed_annotation = _copy_or_point(annotation_path, root / _managed_annotation_name(annotation_path), copy_files)

    now = datetime.now().isoformat(timespec="seconds")
    previous = load_reference(ref_id, reference_dir) if metadata_path.exists() else None
    previous_warnings = previous.warnings if previous else []
    merged_warnings = list(previous_warnings)
    if merged_warning:
        merged_warnings.append(merged_warning)
    if warnings:
        merged_warnings.extend(str(item) for item in warnings if str(item).strip())
    asset = ReferenceAsset(
        reference_id=ref_id,
        root=root,
        fasta=managed_fasta,
        annotation=managed_annotation,
        hisat2_index=hisat2_index_path if hisat2_index_path else root / "hisat2" / "genome",
        created_at=previous.created_at if previous else now,
        updated_at=now,
        provider=normalized_provider,
        annotation_provider=normalize_source_provider(annotation_provider or provider),
        species=species,
        assembly=assembly,
        release=release,
        taxon_id=taxon_id,
        source_urls=[str(url) for url in (source_urls or [])],
        annotation_format=annotation_format,
        created_by=created_by,
        build_status=build_status,
        warnings=merged_warnings,
        notes=notes,
    )
    write_reference(asset)
    _sync_reference_record(asset)
    return asset


def load_reference(reference_id: str, reference_dir: str | Path = "references") -> ReferenceAsset:
    root = reference_root(reference_dir, reference_id)
    metadata_path = root / REFERENCE_METADATA
    if not metadata_path.exists():
        raise FileNotFoundError(f"reference metadata not found: {metadata_path}")
    data = json.loads(metadata_path.read_text(encoding="utf-8"))
    return ReferenceAsset(
        reference_id=data["reference_id"],
        root=Path(data.get("root", root)),
        fasta=Path(data["fasta"]),
        annotation=Path(data["annotation"]) if data.get("annotation") else None,
        hisat2_index=Path(data["hisat2_index"]),
        created_at=data["created_at"],
        updated_at=data["updated_at"],
        provider=normalize_source_provider(data.get("provider", "custom")),
        annotation_provider=normalize_source_provider(data.get("annotation_provider", data.get("provider", "custom"))),
        species=data.get("species"),
        assembly=data.get("assembly"),
        release=data.get("release"),
        taxon_id=data.get("taxon_id"),
        source_urls=_as_string_list(data.get("source_urls")),
        annotation_format=data.get("annotation_format"),
        created_by=data.get("created_by", "manual"),
        build_status=data.get("build_status", "unknown"),
        warnings=_as_string_list(data.get("warnings")),
        notes=data.get("notes", ""),
    )


def list_references(reference_dir: str | Path = "references") -> list[ReferenceAsset]:
    base = Path(reference_dir)
    if not base.exists():
        return []
    assets: list[ReferenceAsset] = []
    for metadata_path in sorted(base.glob(f"*/{REFERENCE_METADATA}")):
        try:
            assets.append(load_reference(metadata_path.parent.name, reference_dir))
        except (FileNotFoundError, KeyError, json.JSONDecodeError):
            continue
    return assets


def cleanup_stale_reference_records(reference_dir: str | Path = "references", database_path: str | Path | None = None) -> list[str]:
    base = Path(reference_dir)
    removed: list[str] = []
    db = AppDatabase(database_path) if database_path else None
    if not base.exists():
        return removed
    for metadata_path in sorted(base.glob(f"*/{REFERENCE_METADATA}")):
        reference_id = metadata_path.parent.name
        try:
            asset = load_reference(reference_id, reference_dir)
            report = check_reference_asset(asset)
        except (FileNotFoundError, KeyError, json.JSONDecodeError):
            report = None
        if report is None or any(issue.level == "error" for issue in report.issues):
            shutil.rmtree(metadata_path.parent, ignore_errors=True)
            if db:
                db.delete_reference(reference_id)
            removed.append(reference_id)
    return removed


def write_reference(asset: ReferenceAsset) -> None:
    asset.root.mkdir(parents=True, exist_ok=True)
    data = asdict(asset)
    for key in ("root", "fasta", "annotation", "hisat2_index"):
        value = data[key]
        data[key] = None if value is None else str(value)
    (asset.root / REFERENCE_METADATA).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def build_hisat2_index_command(fasta: str | Path, index_prefix: str | Path, threads: int = 4) -> list[str]:
    command = ["hisat2-build", "-p", str(threads), str(fasta), str(index_prefix)]
    return command


def build_hisat2_index_for_reference(
    reference_id: str,
    reference_dir: str | Path,
    context: RunContext,
    threads: int = 4,
    force: bool = False,
) -> tuple[ReferenceAsset, CommandResult]:
    asset = load_reference(reference_id, reference_dir)
    if hisat2_index_exists(asset.hisat2_index) and not force:
        raise FileExistsError(f"HISAT2 index already exists: {asset.hisat2_index}")
    asset.hisat2_index.parent.mkdir(parents=True, exist_ok=True)
    command = build_hisat2_index_command(asset.fasta, asset.hisat2_index, threads=threads)
    result = run_context_command(command, context)
    status = "dry_run" if result.dry_run else ("completed" if result.ok else "failed")
    updated = ReferenceAsset(
        reference_id=asset.reference_id,
        root=asset.root,
        fasta=asset.fasta,
        annotation=asset.annotation,
        hisat2_index=asset.hisat2_index,
        created_at=asset.created_at,
        updated_at=datetime.now().isoformat(timespec="seconds"),
        provider=asset.provider,
        annotation_provider=asset.annotation_provider,
        species=asset.species,
        assembly=asset.assembly,
        release=asset.release,
        taxon_id=asset.taxon_id,
        source_urls=asset.source_urls,
        annotation_format=asset.annotation_format,
        created_by=asset.created_by,
        build_status=status,
        warnings=asset.warnings + ([] if result.ok else [f"hisat2-build failed with return code {result.return_code}"]),
        notes=asset.notes,
    )
    write_reference(updated)
    _sync_reference_record(updated)
    asset = updated
    return asset, result


def reference_config_values(asset: ReferenceAsset) -> dict[str, str]:
    values = {
        "reference_id": asset.reference_id,
        "reference_dir": str(asset.root.parent),
        "hisat2_index": str(asset.hisat2_index),
    }
    if asset.annotation:
        values["featurecounts_annotation"] = str(asset.annotation)
    return values


def check_reference_asset(asset: ReferenceAsset) -> ReferenceCheckReport:
    issues: list[ReferenceCheckIssue] = []
    if not asset.fasta.exists():
        issues.append(ReferenceCheckIssue("error", "fasta", f"FASTA file not found: {asset.fasta}"))
    elif asset.fasta.stat().st_size <= 0:
        issues.append(ReferenceCheckIssue("error", "fasta", f"FASTA file is empty: {asset.fasta}"))

    if asset.annotation is None:
        issues.append(ReferenceCheckIssue("warning", "annotation", "annotation is not registered"))
    else:
        if not asset.annotation.exists():
            issues.append(ReferenceCheckIssue("error", "annotation", f"annotation file not found: {asset.annotation}"))
        elif asset.annotation.stat().st_size <= 0:
            issues.append(ReferenceCheckIssue("error", "annotation", f"annotation file is empty: {asset.annotation}"))

    index_files = _hisat2_index_files(asset.hisat2_index)
    if not index_files:
        issues.append(ReferenceCheckIssue("error", "hisat2_index", f"HISAT2 index not found: {asset.hisat2_index}"))
    else:
        empty_files = [path for path in index_files if path.stat().st_size <= 0]
        if empty_files:
            issues.append(
                ReferenceCheckIssue(
                    "error",
                    "hisat2_index",
                    "HISAT2 index contains empty files: " + ", ".join(str(path) for path in empty_files),
                )
            )

    if _source_family(asset.provider) != _source_family(asset.annotation_provider):
        issues.append(
            ReferenceCheckIssue(
                "error",
                "source",
                f"provider mismatch: FASTA={asset.provider}, annotation={asset.annotation_provider}",
            )
        )

    if asset.build_status.lower() == "failed":
        issues.append(ReferenceCheckIssue("warning", "build_status", "last HISAT2 build failed"))

    return ReferenceCheckReport(reference_id=asset.reference_id, ok=not any(issue.level == "error" for issue in issues), issues=issues)


def normalize_source_provider(provider: str | None) -> str:
    if provider is None:
        return "custom"
    value = provider.strip().lower().replace(" ", "_")
    if not value:
        return "custom"
    aliases = {
        "ensembl_plants": "ensembl",
        "ensemblgenomes": "ensembl",
        "ensembl_genomes": "ensembl",
        "ncbi": "refseq",
        "refseq_ncbi": "refseq",
        "custom_url": "custom",
    }
    return aliases.get(value, value)


def _source_family(provider: str) -> str:
    normalized = normalize_source_provider(provider)
    if normalized in {"ensembl"}:
        return "ensembl"
    if normalized in {"refseq"}:
        return "refseq"
    return normalized


def _as_string_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, tuple):
        return [str(item) for item in value]
    if isinstance(value, str):
        return [value] if value else []
    return [str(value)]


def _hisat2_index_files(index_prefix: Path) -> list[Path]:
    suffixes = [".1.ht2", ".2.ht2", ".3.ht2", ".4.ht2", ".5.ht2", ".6.ht2", ".7.ht2", ".8.ht2"]
    large_suffixes = [suffix + "l" for suffix in suffixes]
    parent = index_prefix.parent if str(index_prefix.parent) else Path(".")
    name = index_prefix.name
    primary = [parent / f"{name}{suffix}" for suffix in suffixes]
    if all(path.exists() for path in primary):
        return primary
    large = [parent / f"{name}{suffix}" for suffix in large_suffixes]
    if all(path.exists() for path in large):
        return large
    return []


def _copy_or_point(source: Path, destination: Path, copy_files: bool) -> Path:
    if not copy_files:
        return source
    if source.resolve() == destination.resolve():
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return destination


def _managed_fasta_name(path: Path) -> str:
    lower = path.name.lower()
    if lower.endswith((".fa.gz", ".fna.gz", ".fasta.gz")):
        return "genome" + "".join(path.suffixes[-2:])
    return "genome" + path.suffix


def _managed_annotation_name(path: Path) -> str:
    lower = path.name.lower()
    if lower.endswith((".gtf.gz", ".gff.gz", ".gff3.gz")):
        return "annotation" + "".join(path.suffixes[-2:])
    return "annotation" + path.suffix


def _sync_reference_record(asset: ReferenceAsset) -> None:
    db_path = _infer_database_path(asset.root)
    if db_path is None:
        return
    try:
        db = AppDatabase(db_path)
        owner_user_id = None
        scope = "shared"
        parts = asset.root.parts
        if "users" in parts:
            idx = parts.index("users")
            if idx + 1 < len(parts):
                owner_user_id = parts[idx + 1]
                scope = "private"
        db.upsert_reference(
            reference_id=asset.reference_id,
            reference_dir=asset.root.parent,
            provider=asset.provider,
            annotation_provider=asset.annotation_provider,
            species=asset.species,
            assembly=asset.assembly,
            release=asset.release,
            taxon_id=asset.taxon_id,
            owner_user_id=owner_user_id,
            scope=scope,
            created_by=asset.created_by,
            build_status=asset.build_status,
            description=asset.notes,
        )
    except Exception:
        return


def _infer_database_path(root: Path) -> Path | None:
    parts = root.parts
    if "workspace" in parts:
        idx = parts.index("workspace")
        return Path(*parts[: idx + 1]) / "app.db"
    return None
