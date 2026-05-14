from __future__ import annotations


class WorkflowError(Exception):
    """Base exception for workflow errors."""


class ConfigError(WorkflowError):
    """Raised when a project configuration is invalid."""


class InputFileError(WorkflowError):
    """Raised when expected input files are missing or invalid."""


class ExternalToolError(WorkflowError):
    """Raised when an external command fails."""


class StepExecutionError(WorkflowError):
    """Raised when a pipeline step cannot complete."""
