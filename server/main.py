"""Start the portal and the metering proxy in one process (they share the active run tokens)."""

import asyncio
import logging

import uvicorn

from . import config


async def serve():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    portal = uvicorn.Server(uvicorn.Config("server.app:app", host=config.HOST, port=config.PORT,
                                           proxy_headers=config.TRUST_PROXY, log_level="info",
                                           timeout_graceful_shutdown=3))  # open chat streams would hold a restart
    meter = uvicorn.Server(uvicorn.Config("server.proxy:app", host=config.PROXY_HOST, port=config.PROXY_PORT,
                                          log_level="warning", access_log=False, timeout_graceful_shutdown=3))
    await asyncio.gather(portal.serve(), meter.serve())


if __name__ == "__main__":
    asyncio.run(serve())
