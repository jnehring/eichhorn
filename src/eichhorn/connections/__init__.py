from eichhorn.connections.base import Connection, ConnectionCapabilities, SourceEntry
from eichhorn.connections.local import LocalConnection
from eichhorn.errors import NotYetSupported


def connect(host: str) -> Connection:
    if host == "local":
        return LocalConnection()
    raise NotYetSupported(
        f"host {host!r}: SSH connections arrive in milestone 2; only host: local works yet"
    )


__all__ = ["Connection", "ConnectionCapabilities", "LocalConnection", "SourceEntry", "connect"]
