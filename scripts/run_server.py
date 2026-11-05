#!/usr/bin/env python3
"""
scripts/run_server.py
Production server entrypoint for Railway.
Listens on both Railway's injected $PORT (default 8080) and port 8000 simultaneously
so that both default Railway service domains (8080) and custom domains configured
for port 8000 respond with zero configuration mismatch.
"""

from __future__ import annotations
import asyncio
import os
import sys
from pathlib import Path

# Ensure repository root is on sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import uvicorn
from app.main import app


async def serve_port(port: int) -> None:
    config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=port,
        proxy_headers=True,
        forwarded_allow_ips="*",
        log_level="info",
    )
    server = uvicorn.Server(config)
    await server.serve()


async def main() -> None:
    env_port = int(os.environ.get("PORT", "8080"))
    ports = sorted({env_port, 8000, 8080})
    await asyncio.gather(*(serve_port(p) for p in ports))


if __name__ == "__main__":
    asyncio.run(main())
