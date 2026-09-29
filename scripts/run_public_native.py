#!/usr/bin/env python3
"""Run the real app on loopback for a fixed HTTPS reverse-proxy hostname.

This is the non-Docker companion to a named Cloudflare Tunnel. It deliberately
rejects localhost/dev configuration and never binds the shared cluster node.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import Settings
from app.main import create_app
from scripts.configure import ROOT, load_env


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    values = load_env(ROOT / ".env")
    os.environ.update(values)
    settings = Settings.from_env()
    if not settings.secure:
        raise SystemExit("公网入口必须使用 configure.py 配置固定 HTTPS 域名；不要使用 --dev")
    if not 1024 <= args.port <= 65535:
        raise SystemExit("端口必须在 1024～65535 之间")

    import uvicorn

    uvicorn.run(
        create_app,
        factory=True,
        host="127.0.0.1",
        port=args.port,
        access_log=False,
        proxy_headers=False,
        ws="websockets",
        ws_max_size=131072,
        ws_max_queue=4,
        ws_ping_interval=20,
        ws_ping_timeout=20,
        limit_concurrency=32,
        backlog=64,
        timeout_keep_alive=10,
        timeout_graceful_shutdown=25,
    )


if __name__ == "__main__":
    main()
