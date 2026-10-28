"""Startbefehl der Statusseite: python -m sc_digger.web [--config ...] [--host ...] [--port ...]."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import uvicorn

from ..models import Config
from .app import FailureTracker, create_app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m sc_digger.web")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args(argv)

    password = os.environ.get("SC_DIGGER_WEB_PASSWORD")
    if not password:
        print(
            "Fehler: Umgebungsvariable SC_DIGGER_WEB_PASSWORD ist nicht gesetzt. "
            "Der Webserver startet ohne Passwort nicht.",
            file=sys.stderr,
        )
        return 2

    cfg = Config.load(args.config)
    # Login-Sperre standardmäßig an; "0" schaltet sie ab. Hinter einem Proxy (Caddy) ist der
    # Socket-Peer für alle Clients gleich, dann zählt mit TRUST_PROXY=1 die Adresse aus
    # X-Forwarded-For.
    limit_on = os.environ.get("SC_DIGGER_WEB_RATE_LIMIT", "1").strip() != "0"
    options: dict = {
        "login_limit": FailureTracker() if limit_on else None,
        "trust_proxy": os.environ.get("SC_DIGGER_WEB_TRUST_PROXY", "").strip() == "1",
    }
    # Editor nur, wenn eine lokale Override-Datei konfiguriert ist; sonst bleibt er wie bisher aus.
    local_env = os.environ.get("SC_DIGGER_CONFIG_LOCAL")
    if local_env:
        app = create_app(cfg, password=password, base_path=Path(args.config),
                         local_path=Path(local_env), **options)
    else:
        app = create_app(cfg, password=password, **options)
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
