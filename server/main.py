"""Start the portal and the metering proxy.

`python -m server.main`        both in one process (local development)
`python -m server.main meter`  only the metering proxy (the eval-portal-meter service)
EVP_METER=separate             only the portal (the eval-portal service; the proxy is its own service, so a portal
                               restart never cuts a running review's model calls)
"""

import asyncio
import logging
import os
import sys

import uvicorn

from . import config


def _portal():
    return uvicorn.Server(uvicorn.Config("server.app:app", host=config.HOST, port=config.PORT,
                                         proxy_headers=config.TRUST_PROXY, log_level="info",
                                         timeout_graceful_shutdown=3))  # open chat streams would hold a restart


def _meter(graceful):
    return uvicorn.Server(uvicorn.Config("server.proxy:app", host=config.PROXY_HOST, port=config.PROXY_PORT,
                                         log_level="warning", access_log=False, timeout_graceful_shutdown=graceful))


async def serve(mode):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # On its own, a meter restart waits for in-flight model calls (a review's call can stream for minutes).
    servers = {"meter": [_meter(600)], "portal": [_portal()]}.get(mode) or [_portal(), _meter(3)]
    await asyncio.gather(*(s.serve() for s in servers))


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else ("portal" if os.environ.get("EVP_METER") == "separate" else "all")
    asyncio.run(serve(mode))
