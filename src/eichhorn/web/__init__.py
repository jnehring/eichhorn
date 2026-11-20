"""The web interface (spec 9). Needs the `web` extra: pip install "eichhorn[web]"."""

from __future__ import annotations

import os
import secrets
import socket
import webbrowser
from pathlib import Path

from eichhorn.errors import EichhornError


def parse_listen(listen: str) -> tuple[str, int]:
    host, _, port = listen.rpartition(":")
    if not host or not port.isdigit():
        raise EichhornError(f"--listen must look like 127.0.0.1:8765, got {listen!r}")
    return host.strip("[]"), int(port)


def serve(config_dir: Path | None, listen: str, open_browser: bool = True) -> None:
    try:
        import uvicorn

        from eichhorn.web.app import create_app
    except ImportError as exc:
        raise EichhornError(
            f'the web interface needs extra packages ({exc.name}): pip install "eichhorn[web]"'
        ) from None
    host, port = parse_listen(listen)
    local = host in ("127.0.0.1", "localhost", "::1")
    allowed = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"} if local else None
    token = secrets.token_urlsafe(24)
    app = create_app(config_dir, token, allowed)
    shown = "127.0.0.1" if local else (socket.gethostname() if host in ("0.0.0.0", "::") else host)
    url = f"http://{shown}:{port}/?token={token}"
    print("Eichhorn web interface")
    print(f"  open: {url}")
    if not local:
        print("  WARNING: listening on another address than localhost. The web interface has no")
        print("  user accounts or TLS; anyone who gets the link can read and restore all backups.")
    if os.environ.get("SSH_CONNECTION"):
        print("  You are logged in over SSH. From your own computer, open a tunnel first:")
        print(f"    ssh -L {port}:127.0.0.1:{port} {socket.gethostname()}")
        print("  then open the link above there.")
        open_browser = False
    print("  Stop with Ctrl-C.", flush=True)
    if open_browser:
        webbrowser.open(url)
    uvicorn.run(app, host=host, port=port, log_level="warning")
