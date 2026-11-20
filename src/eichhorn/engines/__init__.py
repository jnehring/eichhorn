from eichhorn.engines.builtin import BuiltinEngine, Previous, SourceStats
from eichhorn.errors import NotYetSupported
from eichhorn.stores.base import StoreCapabilities


def choose_engine(name: str, caps: StoreCapabilities) -> BuiltinEngine:
    """Pick the transfer engine (spec 5.2). v1 milestone 1 has only builtin."""
    if name == "rsync":
        raise NotYetSupported("the rsync engine arrives in milestone 2; use engine: builtin")
    return BuiltinEngine(caps)


__all__ = ["BuiltinEngine", "Previous", "SourceStats", "choose_engine"]
