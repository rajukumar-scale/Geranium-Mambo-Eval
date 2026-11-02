"""Safe ad-hoc SQL against the live portal.db (use this instead of one-off /tmp scripts).

    cd /opt/eval-portal && sudo -u evalportal .venv/bin/python -m server.dbq "select count(*) from runs"
    ... -m server.dbq --write "update runs set status='failed' where id='...' and status='running'"
    ... -m server.dbq --write --file fix.sql

Read-only by default (a read never blocks the portal in WAL mode). --write runs everything in ONE short
transaction with the same lock wait as the portal, so it can't hold the write lock for long or fail half-way.
The portal and the meter write constantly; a slow or half-done hand-written script is what made runs crash on
"database is locked" (2026-11-02). Keep writes small: no bulk deletes/updates of big tables while reviews run.
"""

import argparse
import sqlite3
import sys

from . import config
from .db import BUSY_TIMEOUT_MS


def connect(write=False, path=None):
    path = path or config.DATA_DIR / "portal.db"
    if write:
        conn = sqlite3.connect(path, isolation_level=None, timeout=BUSY_TIMEOUT_MS / 1000)
        conn.execute("pragma synchronous=normal")
    else:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=BUSY_TIMEOUT_MS / 1000)
    conn.execute(f"pragma busy_timeout={BUSY_TIMEOUT_MS}")
    return conn


def run(sql, write=False, path=None, out=sys.stdout):
    conn = connect(write, path)
    try:
        if not write:
            cur = conn.execute(sql)
            if cur.description:
                print("\t".join(d[0] for d in cur.description), file=out)
                for row in cur:
                    print("\t".join("" if v is None else str(v) for v in row), file=out)
            return
        before = conn.total_changes
        try:  # one script so executescript can't commit half; BEGIN IMMEDIATE takes the write lock up front
            conn.executescript(f"begin immediate;\n{sql.strip().rstrip(';')};\ncommit;")
        except BaseException:
            if conn.in_transaction:
                conn.execute("rollback")
            raise
        print(f"ok, {conn.total_changes - before} row(s) changed", file=out)
    finally:
        conn.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("sql", nargs="?")
    ap.add_argument("--file")
    ap.add_argument("--write", action="store_true", help="allow changes (one transaction)")
    args = ap.parse_args(argv)
    sql = open(args.file).read() if args.file else args.sql
    if not sql:
        ap.error("give SQL or --file")
    run(sql, write=args.write)


if __name__ == "__main__":
    main()
