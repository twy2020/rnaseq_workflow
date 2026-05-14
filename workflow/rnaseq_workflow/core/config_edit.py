from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def set_config_value(path: str | Path, key_path: str, value: str) -> None:
    config_path = Path(path)
    with config_path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError("config root must be a mapping")

    parts = key_path.split(".")
    target: Any = data
    for part in parts[:-1]:
        target = _descend(target, part)
    _assign(target, parts[-1], _parse_value(value))

    with config_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(data, handle, allow_unicode=True, sort_keys=False)


def _descend(target: Any, part: str) -> Any:
    if isinstance(target, list):
        return target[int(part)]
    if isinstance(target, dict):
        if part not in target:
            target[part] = {}
        return target[part]
    raise ValueError(f"cannot descend into {part}")


def _assign(target: Any, part: str, value: Any) -> None:
    if isinstance(target, list):
        target[int(part)] = value
        return
    if isinstance(target, dict):
        target[part] = value
        return
    raise ValueError(f"cannot assign {part}")


def _parse_value(value: str) -> Any:
    parsed = yaml.safe_load(value)
    return value if parsed is None and value.lower() != "null" else parsed
