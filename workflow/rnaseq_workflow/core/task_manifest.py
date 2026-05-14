from __future__ import annotations

import json
import re
import urllib.parse
from dataclasses import asdict, dataclass, field
from pathlib import PurePosixPath

from rnaseq_workflow.steps.download.smart import looks_like_sra_accession, split_sra_targets


SUPPORTED_URL_SUFFIXES = (".sra", ".fastq", ".fq", ".fastq.gz", ".fq.gz")
ALLOWED_URL_SCHEMES = {"http", "https", "ftp"}
SAFE_FILENAME = re.compile(r"^[A-Za-z0-9._+-]+$")


@dataclass(frozen=True, slots=True)
class CustomUrlGroup:
    base_url: str
    filenames: list[str]


@dataclass(frozen=True, slots=True)
class ParsedManifest:
    raw: str
    accessions: list[str] = field(default_factory=list)
    url_groups: list[CustomUrlGroup] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict:
        return asdict(self)


def parse_task_manifest(raw: str) -> ParsedManifest:
    text = raw.strip()
    if not text:
        return ParsedManifest(raw=raw, errors=["manifest is empty"])
    if _looks_like_json(text):
        return _parse_json_manifest(text, raw)
    accessions = split_sra_targets(text)
    if not accessions and looks_like_sra_accession(text):
        accessions = [text.upper()]
    if accessions:
        return ParsedManifest(raw=raw, accessions=accessions)
    return ParsedManifest(raw=raw, errors=["manifest must be SRA accessions or JSON custom_url manifest"])


def _parse_json_manifest(text: str, raw: str) -> ParsedManifest:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        return ParsedManifest(raw=raw, errors=[f"invalid JSON manifest: {exc}"])
    groups_raw = payload.get("url_groups") or payload.get("custom_urls") or []
    groups: list[CustomUrlGroup] = []
    urls: list[str] = []
    errors: list[str] = []
    for index, row in enumerate(groups_raw, start=1):
        base_url = str(row.get("base_url", "")).strip()
        filenames = [str(item).strip() for item in row.get("filenames", []) if str(item).strip()]
        base_error = validate_base_url(base_url)
        if base_error:
            errors.append(f"url_groups[{index}].base_url: {base_error}")
            continue
        valid_names: list[str] = []
        for filename in filenames:
            name_error = validate_url_filename(filename)
            if name_error:
                errors.append(f"url_groups[{index}].filenames[{filename}]: {name_error}")
                continue
            valid_names.append(filename)
            urls.append(join_base_url(base_url, filename))
        if valid_names:
            groups.append(CustomUrlGroup(base_url=base_url, filenames=valid_names))
    if not groups and not errors:
        errors.append("JSON manifest has no url_groups/custom_urls")
    return ParsedManifest(raw=raw, url_groups=groups, urls=urls, errors=errors)


def validate_base_url(base_url: str) -> str | None:
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme.lower() not in ALLOWED_URL_SCHEMES:
        return "scheme must be http, https, or ftp"
    if not parsed.netloc:
        return "host is required"
    if any(char in base_url for char in ["\n", "\r", "`", "$", "|", ";"]):
        return "dangerous shell characters are not allowed"
    return None


def validate_url_filename(filename: str) -> str | None:
    path = PurePosixPath(filename)
    if path.is_absolute() or ".." in path.parts:
        return "absolute paths and '..' are not allowed"
    if "/" in filename or "\\" in filename:
        return "nested paths are not allowed"
    if not SAFE_FILENAME.match(filename):
        return "filename contains unsupported characters"
    lower = filename.lower()
    if not any(lower.endswith(suffix) for suffix in SUPPORTED_URL_SUFFIXES):
        return "unsupported file suffix"
    return None


def join_base_url(base_url: str, filename: str) -> str:
    return base_url.rstrip("/") + "/" + urllib.parse.quote(filename)


def _looks_like_json(text: str) -> bool:
    return text.startswith("{") or text.startswith("[")
