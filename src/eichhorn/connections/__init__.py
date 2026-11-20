from eichhorn.connections.base import (
    Command,
    Completed,
    Connection,
    ConnectionCapabilities,
    SourceEntry,
    run,
)
from eichhorn.connections.local import LocalConnection
from eichhorn.connections.ssh import SshConnection


def connect(host: str, sudo: bool = False, shell: str | None = None) -> Connection:
    if host == "local":
        return LocalConnection(sudo=sudo, shell=shell)
    return SshConnection(host, sudo=sudo)


__all__ = [
    "Command",
    "Completed",
    "Connection",
    "ConnectionCapabilities",
    "LocalConnection",
    "SourceEntry",
    "SshConnection",
    "connect",
    "run",
]
