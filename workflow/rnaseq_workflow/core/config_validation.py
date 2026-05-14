from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from rnaseq_workflow.core.config import ProjectConfig
from rnaseq_workflow.core.errors import ConfigError
from rnaseq_workflow.core.step_registry import expand_step_ids


@dataclass(frozen=True, slots=True)
class ConfigIssue:
    level: str
    field: str
    message: str


@dataclass(frozen=True, slots=True)
class ConfigValidationResult:
    issues: list[ConfigIssue]

    @property
    def errors(self) -> list[ConfigIssue]:
        return [issue for issue in self.issues if issue.level == "error"]

    @property
    def warnings(self) -> list[ConfigIssue]:
        return [issue for issue in self.issues if issue.level == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def raise_for_errors(self) -> None:
        if self.errors:
            joined = "; ".join(f"{issue.field}: {issue.message}" for issue in self.errors)
            raise ConfigError(joined)


def validate_project_config(config: ProjectConfig, check_files: bool = True) -> ConfigValidationResult:
    issues: list[ConfigIssue] = []
    steps = expand_step_ids(config.steps)
    settings = config.settings

    _validate_steps(steps, settings, issues)
    _validate_execution(settings, issues)
    _validate_samples(config, check_files, issues)
    if check_files:
        _validate_reference_files(config, steps, settings, issues)

    return ConfigValidationResult(issues=issues)


def _validate_steps(steps: list[str], settings: dict, issues: list[ConfigIssue]) -> None:
    if "hisat2" in steps and not settings.get("hisat2_index"):
        issues.append(ConfigIssue("error", "hisat2_index", "required when alignment/hisat2 is enabled"))
    if "featurecounts" in steps and not settings.get("featurecounts_annotation"):
        issues.append(
            ConfigIssue("error", "featurecounts_annotation", "required when quantification/featurecounts is enabled")
        )


def _validate_execution(settings: dict, issues: list[ConfigIssue]) -> None:
    mode = str(settings.get("execution_mode", "local")).lower()
    if mode not in {"local", "docker", "container"}:
        issues.append(ConfigIssue("error", "execution_mode", "must be local or docker"))
    if mode in {"docker", "container"}:
        workspace = Path(settings.get("docker_workspace", ".")).resolve()
        if not workspace.exists():
            issues.append(ConfigIssue("error", "docker_workspace", f"path does not exist: {workspace}"))


def _validate_samples(config: ProjectConfig, check_files: bool, issues: list[ConfigIssue]) -> None:
    seen: set[str] = set()
    docker_mode = str(config.settings.get("execution_mode", "local")).lower() in {"docker", "container"}
    docker_workspace = Path(config.settings.get("docker_workspace", ".")).resolve()
    for index, sample in enumerate(config.samples, start=1):
        sample_id = str(sample.get("sample_id"))
        if sample_id in seen:
            issues.append(ConfigIssue("error", f"samples[{index}].sample_id", f"duplicate sample id: {sample_id}"))
        seen.add(sample_id)

        layout = str(sample.get("layout", "unknown"))
        if layout not in {"single", "paired", "unknown"}:
            issues.append(ConfigIssue("error", f"samples[{index}].layout", "must be single, paired, or unknown"))

        raw_paths = sample.get("source_paths") or [sample.get("source_path")]
        source_paths = [Path(path) for path in raw_paths if path]
        if layout == "paired" and len(source_paths) != 2:
            issues.append(ConfigIssue("error", f"samples[{index}].source_paths", "paired samples require two files"))

        for raw_path in source_paths:
            path = _resolve_config_path(raw_path, config.work_dir)
            if check_files and not path.exists():
                issues.append(ConfigIssue("error", f"samples[{index}].source_path", f"file not found: {path}"))
            if docker_mode and not _is_within(path, docker_workspace):
                issues.append(
                    ConfigIssue(
                        "error",
                        f"samples[{index}].source_path",
                        f"path must be inside docker_workspace {docker_workspace}: {path}",
                    )
                )


def _validate_reference_files(config: ProjectConfig, steps: list[str], settings: dict, issues: list[ConfigIssue]) -> None:
    docker_mode = str(settings.get("execution_mode", "local")).lower() in {"docker", "container"}
    docker_workspace = Path(settings.get("docker_workspace", ".")).resolve()
    if "hisat2" in steps and settings.get("hisat2_index"):
        index_prefix = _resolve_hisat2_index_prefix(Path(settings["hisat2_index"]), config.work_dir)
        if not _hisat2_index_exists(index_prefix):
            issues.append(ConfigIssue("error", "hisat2_index", f"HISAT2 index not found: {index_prefix}"))
        if docker_mode and not _is_within(index_prefix, docker_workspace):
            issues.append(ConfigIssue("error", "hisat2_index", f"path must be inside docker_workspace {docker_workspace}"))
    if "featurecounts" in steps and settings.get("featurecounts_annotation"):
        annotation = _resolve_config_path(Path(settings["featurecounts_annotation"]), config.work_dir)
        if not annotation.exists():
            issues.append(ConfigIssue("error", "featurecounts_annotation", f"file not found: {annotation}"))
        if docker_mode and not _is_within(annotation, docker_workspace):
            issues.append(
                ConfigIssue("error", "featurecounts_annotation", f"path must be inside docker_workspace {docker_workspace}")
            )


def _resolve_config_path(path: Path, work_dir: Path) -> Path:
    if path.is_absolute():
        return path.resolve()
    cwd_candidate = path.resolve()
    if cwd_candidate.exists():
        return cwd_candidate
    work_dir_candidate = (work_dir / path).resolve()
    if work_dir_candidate.exists():
        return work_dir_candidate
    return cwd_candidate


def _resolve_hisat2_index_prefix(path: Path, work_dir: Path) -> Path:
    if path.is_absolute():
        return path.resolve()
    cwd_candidate = path.resolve()
    if _hisat2_index_exists(cwd_candidate):
        return cwd_candidate
    work_dir_candidate = (work_dir / path).resolve()
    if _hisat2_index_exists(work_dir_candidate):
        return work_dir_candidate
    return cwd_candidate


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _hisat2_index_exists(index_prefix: Path) -> bool:
    suffixes = [".1.ht2", ".2.ht2", ".3.ht2", ".4.ht2", ".5.ht2", ".6.ht2", ".7.ht2", ".8.ht2"]
    large_suffixes = [suffix + "l" for suffix in suffixes]
    return all(Path(f"{index_prefix}{suffix}").exists() for suffix in suffixes) or all(
        Path(f"{index_prefix}{suffix}").exists() for suffix in large_suffixes
    )
