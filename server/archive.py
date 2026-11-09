"""Old chats' sessions to S3, by hand (Evan, 2026-11-02). The disk grows ~4.5 GB a day and every chat must stay
saved, but the server has no S3 access; Evan uploads files through the corp "upload files to S3" page (public link,
~5 GB per file). So:

1. `pack`: chats idle for --days whose sessions are already packed (cold.tgz, housekeeping.py) are collected into tar
   parts of at most --part-gb in DATA/archive/<bundle>/. Nothing is removed yet.
2. Evan downloads each part and uploads it; `link <bundle> <part> <url>` downloads it back from the link and checks it
   byte for byte (sha256) against what was packed.
3. `remove <bundle>`: for the parts with a checked link, each chat's cold.tgz is replaced by archived.json (where its
   piece lives: url, offset, size, sha256) and the local part is deleted.
4. Restoring is automatic: workspace.thaw() calls fetch() when a chat with archived.json is used again (a CB writes in
   it, a handover, ...). It downloads only that chat's bytes (an HTTP Range request; the whole part streamed when the
   host ignores ranges), checks them, and puts cold.tgz back. `restore <attempt>` does the same by hand.

The files are not encrypted (Evan: the upload bucket is private enough). The sha256 checks only catch a broken or
partial upload/download. Run as the portal user:
  sudo -u evalportal .venv/bin/python -m server.archive pack|status|link|remove|restore ...
"""

import argparse
import hashlib
import json
import logging
import os
import sys
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path

from . import config

log = logging.getLogger("portal.archive")
MARKER = "archived.json"
COLD = "cold.tgz"  # workspace.COLD (not imported: workspace imports this module)
CHUNK = 1 << 20


def root():
    return config.DATA_DIR / "archive"


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def attempt_dir(attempt_id):
    return config.DATA_DIR / "attempts" / attempt_id


def is_archived(attempt_id):
    return (attempt_dir(attempt_id) / MARKER).is_file()


def candidates(days, busy):
    """Chats idle for `days`, sessions packed, not archived yet, nothing queued or running."""
    from . import db
    rows = db.q("select attempt_id from chats where coalesce(last_activity, created_at, 0) < ? order by last_activity",
                (time.time() - days * 86400,))
    pending = in_pending_bundles()
    out = []
    for r in rows:
        a = r["attempt_id"]
        d = attempt_dir(a)
        if (d / COLD).is_file() and not (d / MARKER).exists() and a not in pending and not busy(a):
            out.append(a)
    return out


def in_pending_bundles():
    """Chats already packed into a bundle that isn't removed yet (waiting for its link). Without this, packing one
    part at a time (2026-11-09: 12 GB free, 15 GB to pack) put the same chats into every new part."""
    out = set()
    base = root()
    for path in base.glob("*/manifest.json") if base.exists() else []:
        try:
            m = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        removed = {p["n"] for p in m.get("parts", []) if p.get("removed_at")}
        for attempt, where in m.get("chats", {}).items():
            # a removed part's chats are archived (or were restored and may be packed again later)
            if where.get("part") not in removed and not (attempt_dir(attempt) / MARKER).exists():
                out.add(attempt)
    return out


def _manifest_path(bundle):
    return root() / bundle / "manifest.json"


def load(bundle):
    return json.loads(_manifest_path(bundle).read_text())


def save(bundle, manifest):
    tmp = _manifest_path(bundle).with_suffix(".tmp")
    tmp.write_text(json.dumps(manifest, indent=1))
    tmp.replace(_manifest_path(bundle))


def pack(days=14, part_gb=4.5, max_parts=None, busy=None):
    """Encrypt the idle chats' cold.tgz into tar parts. Returns the bundle name (None when there is nothing to do)."""
    from . import db
    busy = busy or (lambda a: bool(db.active_run(a)))
    chats = candidates(days, busy)
    if not chats:
        return None
    bundle = stamp = time.strftime("chats-%Y%m%dT%H%M%SZ", time.gmtime())
    n = 1
    while (root() / bundle).exists():  # two packs in the same second
        n += 1
        bundle = f"{stamp}-{n}"
    out = root() / bundle
    out.mkdir(parents=True)
    os.chmod(out, 0o755)  # parts are encrypted; the ssh user downloads them
    limit = int(part_gb * 1e9)
    manifest = {"bundle": bundle, "created_at": time.time(), "days": days, "parts": [], "chats": {}}
    tar, part, part_path = None, None, None

    def close_part():
        if tar:
            tar.close()
            os.chmod(part_path, 0o644)
            part["size"] = part_path.stat().st_size
            part["sha256"] = sha256(part_path)

    for attempt in chats:
        cold = attempt_dir(attempt) / COLD
        try:
            size, plain = cold.stat().st_size, sha256(cold)
        except OSError as error:  # used again meanwhile
            log.warning("skipping %s: %s", attempt, error)
            continue
        if tar is None or (part["bytes"] + size > limit and part["bytes"] > 0):
            close_part()
            if max_parts and len(manifest["parts"]) >= max_parts:
                break
            n = len(manifest["parts"])
            part_path = out / f"{bundle}-part{n:02d}.tar"
            part = {"n": n, "file": part_path.name, "bytes": 0, "url": None, "checked_at": None}
            manifest["parts"].append(part)
            tar = tarfile.open(part_path, "w", format=tarfile.USTAR_FORMAT)
        info = tar.gettarinfo(str(cold), arcname=f"{attempt}.tgz")
        info.uid = info.gid = 0
        info.uname = info.gname = ""
        info.size = size
        # where the member's bytes start in the part (tarfile doesn't set offsets when writing): header after tar.offset
        data_at = tar.offset + len(info.tobuf(tar.format, tar.encoding, tar.errors))
        with open(cold, "rb") as f:
            tar.addfile(info, f)
        if sha256(cold) != plain:  # changed while copying: remove() will see the new hash and keep it here
            log.warning("%s changed while packing", attempt)
        manifest["chats"][attempt] = {"part": part["n"], "offset": data_at, "size": size, "sha256": plain}
        part["bytes"] += size
    close_part()
    save(bundle, manifest)
    return bundle


def _download(url, dst, byte_range=None, timeout=300):
    """Download url (or one byte range of it) into dst. Returns True if the host honoured the range."""
    req = urllib.request.Request(url, headers={"User-Agent": "eval-portal-archive"})
    if byte_range:
        req.add_header("Range", f"bytes={byte_range[0]}-{byte_range[1]}")
    with urllib.request.urlopen(req, timeout=timeout) as resp, open(dst, "wb") as f:
        ranged = byte_range is not None and resp.status == 206
        if byte_range and not ranged:  # whole file: keep only our piece
            start, end = byte_range
            seen = 0
            for block in iter(lambda: resp.read(CHUNK), b""):
                lo, hi = max(start - seen, 0), min(end + 1 - seen, len(block))
                if lo < hi:
                    f.write(block[lo:hi])
                seen += len(block)
                if seen > end:
                    break
            return False
        for block in iter(lambda: resp.read(CHUNK), b""):
            f.write(block)
    return ranged


def link(bundle, n, url):
    """Record the uploaded copy of part n after checking it against the packed part, byte for byte."""
    manifest = load(bundle)
    part = manifest["parts"][n]
    h = hashlib.sha256()
    req = urllib.request.Request(url, headers={"User-Agent": "eval-portal-archive"})
    with urllib.request.urlopen(req, timeout=600) as resp:
        for block in iter(lambda: resp.read(CHUNK), b""):
            h.update(block)
    if h.hexdigest() != part["sha256"]:
        raise RuntimeError(f"the file at that link is not part {n} of {bundle} (sha256 differs); upload it again")
    part["url"], part["checked_at"] = url, time.time()
    save(bundle, manifest)
    return part


def remove(bundle):
    """For each checked part: point its chats at the upload (archived.json) and free their cold.tgz. Returns bytes freed."""
    from . import workspace
    manifest = load(bundle)
    freed = 0
    for part in manifest["parts"]:
        if not part.get("url") or part.get("removed_at"):
            continue
        for attempt, piece in manifest["chats"].items():
            if piece["part"] != part["n"]:
                continue
            d = attempt_dir(attempt)
            with workspace._cold_lock(attempt):
                cold = d / COLD
                if not cold.is_file() or sha256(cold) != piece["sha256"]:  # used again since it was packed: keep it here
                    piece["skipped"] = "changed since packed"
                    continue
                marker = {"bundle": bundle, "url": part["url"], **piece, "archived_at": time.time()}
                tmp = d / (MARKER + ".tmp")
                tmp.write_text(json.dumps(marker))
                tmp.replace(d / MARKER)
                freed += cold.stat().st_size
                cold.unlink()
        part["removed_at"] = time.time()
        local = root() / bundle / part["file"]
        if local.exists():
            local.unlink()
        save(bundle, manifest)
    return freed


def fetch(attempt_id):
    """Bring an archived chat's cold.tgz back from its upload (called by workspace.thaw under the chat's lock)."""
    d = attempt_dir(attempt_id)
    marker = json.loads((d / MARKER).read_text())
    start, size = marker["offset"], marker["size"]
    with tempfile.TemporaryDirectory(dir=d) as tmp:
        piece = Path(tmp) / COLD
        t0 = time.time()
        _download(marker["url"], piece, (start, start + size - 1))
        if piece.stat().st_size != size or sha256(piece) != marker["sha256"]:
            raise RuntimeError(f"archived chat {attempt_id}: the download doesn't match what was packed")
        piece.replace(d / COLD)
    (d / MARKER).unlink()
    log.info("restored archived chat %s from %s in %.1f s", attempt_id, marker["bundle"], time.time() - t0)
    return True


def status():
    lines = []
    for m in sorted(root().glob("*/manifest.json")) if root().exists() else []:
        man = json.loads(m.read_text())
        for p in man["parts"]:
            state = "removed from server" if p.get("removed_at") else "link checked" if p.get("url") else "waiting for upload"
            lines.append(f"{man['bundle']} part {p['n']}: {p['bytes'] / 1e9:.2f} GB, "
                         f"{sum(1 for c in man['chats'].values() if c['part'] == p['n'])} chats, {state}  "
                         f"[{root() / man['bundle'] / p['file']}]")
    return lines


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(prog="python -m server.archive")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pack")
    p.add_argument("--days", type=float, default=14)
    p.add_argument("--part-gb", type=float, default=4.5)
    p.add_argument("--max-parts", type=int)
    sub.add_parser("status")
    p = sub.add_parser("link")
    p.add_argument("bundle")
    p.add_argument("part", type=int)
    p.add_argument("url")
    p = sub.add_parser("remove")
    p.add_argument("bundle")
    p = sub.add_parser("restore")
    p.add_argument("attempt")
    a = ap.parse_args(argv)
    if a.cmd == "pack":
        bundle = pack(a.days, a.part_gb, a.max_parts)
        print(f"packed {bundle}" if bundle else "nothing to pack")
        print("\n".join(status()))
    elif a.cmd == "status":
        print("\n".join(status()) or "no archives")
    elif a.cmd == "link":
        print(f"part {a.part} of {a.bundle} checked: {link(a.bundle, a.part, a.url)['url']}")
    elif a.cmd == "remove":
        print(f"freed {remove(a.bundle) / 1e9:.2f} GB")
    elif a.cmd == "restore":
        from . import workspace
        workspace.thaw(a.attempt)
        print("restored" if not is_archived(a.attempt) else "still archived")


if __name__ == "__main__":
    sys.exit(main())
