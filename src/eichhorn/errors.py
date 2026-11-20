"""Exceptions with the exit codes defined in the spec (section 6)."""

from __future__ import annotations

EXIT_OK = 0
EXIT_FAILED = 1  # a target, repository or check failed
EXIT_USAGE = 2  # usage or config error
EXIT_REPO = 3  # repository not connected or locked


class EichhornError(Exception):
    """Base class. The message is shown to the user as is."""

    exit_code = EXIT_FAILED


class ConfigError(EichhornError):
    exit_code = EXIT_USAGE

    def __init__(self, message: str, problems: list[str] | None = None) -> None:
        super().__init__(message)
        self.problems = problems or []

    def __str__(self) -> str:
        text = super().__str__()
        if self.problems:
            text += "\n" + "\n".join(f"  - {p}" for p in self.problems)
        return text


class RepositoryError(EichhornError):
    exit_code = EXIT_REPO


class LockError(RepositoryError):
    pass


class NotYetSupported(EichhornError):
    """A feature from the spec that a later milestone implements."""

    exit_code = EXIT_USAGE
