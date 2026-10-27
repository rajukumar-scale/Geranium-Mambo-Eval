"""Post one line to the portal's Slack channel from a shell script: python -m server.notify_cli "text"
(used by deploy/pull_deploy.sh to report auto-deploys)."""

import asyncio
import sys

from . import notify

if __name__ == "__main__":
    if notify.configured():
        asyncio.run(notify.slack(" ".join(sys.argv[1:])))
