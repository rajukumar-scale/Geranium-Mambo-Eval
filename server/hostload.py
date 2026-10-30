"""This machine's load, for placing reviews (server first, boat as overflow) and for the /status page.
Reads /proc on Linux; elsewhere (a laptop) the memory figure is None, which never places a review here."""

import os
import shutil

from . import config


def mem_available_gb():
    """MemAvailable from /proc/meminfo in GB, or None when it can't be read."""
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) / 1024 / 1024
    except OSError:
        pass
    return None


def mem_total_gb():
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) / 1024 / 1024
    except OSError:
        pass
    return None


def snapshot():
    """Public-safe numbers about this machine (no names, emails or tasks)."""
    try:
        load1 = os.getloadavg()[0]
    except OSError:
        load1 = None
    try:
        disk = shutil.disk_usage(config.DATA_DIR)
        disk_free_pct = round(100 * disk.free / disk.total, 1)
    except OSError:
        disk_free_pct = None
    free, total = mem_available_gb(), mem_total_gb()
    return dict(memory_free_gb=round(free, 1) if free is not None else None,
                memory_total_gb=round(total, 1) if total is not None else None,
                load1=round(load1, 2) if load1 is not None else None, cpus=os.cpu_count(),
                disk_free_pct=disk_free_pct)
