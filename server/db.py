"""SQLite storage: chats (one per attempt), messages, runs, metered LLM calls, proofs."""

import contextlib
import json
import sqlite3
import threading
import time

from . import config

_lock = threading.RLock()
_conn = None
_local = threading.local()  # per-thread read-only connection, used inside reading()

SCHEMA = """
create table if not exists chats (
  attempt_id text primary key,
  task_id text, project_id text, user_id text, email text,
  session_id text, session_started integer default 0,
  admin_open integer default 0, pending_review_run text,
  created_at real, last_activity real
);
create table if not exists messages (
  id integer primary key autoincrement,
  attempt_id text not null, role text not null, content text not null,
  meta text default '{}', created_at real
);
create index if not exists messages_attempt on messages(attempt_id, id);
create index if not exists messages_attempt_role on messages(attempt_id, role, id);
create table if not exists runs (
  id text primary key, attempt_id text not null, kind text, command text,
  continuation integer default 0, session_id text, resumed integer default 0, runner_box text,
  status text, error text,
  created_at real, started_at real, finished_at real,
  proxy_token text,
  calls integer default 0, input_tokens integer default 0, output_tokens integer default 0,
  cache_read_tokens integer default 0, cache_creation_tokens integer default 0,
  litellm_cost_usd real default 0, cc_cost_usd real, cost_estimate real, models text default '{}',
  cc_result text, report_stage text, report_file text, report_sha text,
  verdict text, score integer, exec_status text, proof_code text
);
create index if not exists runs_attempt on runs(attempt_id, created_at);
create table if not exists llm_calls (
  id integer primary key autoincrement, run_id text, model text, path text, http_status integer,
  input_tokens integer, output_tokens integer, cache_read_tokens integer, cache_creation_tokens integer,
  cost_usd real, created_at real
);
create table if not exists proofs (
  code text primary key, run_id text, attempt_id text, payload text, created_at real
);
create index if not exists proofs_attempt on proofs(attempt_id);
create table if not exists logins (
  id integer primary key autoincrement, ip text, identifier text, attempt_id text,
  ok integer, reason text, created_at real
);
create table if not exists admins (
  email text primary key, role text default 'admin', password_hash text,
  invite_hash text, invite_expires real, created_by text, created_at real, last_login real
);
create table if not exists admin_requests (
  id integer primary key autoincrement, attempt_id text, message text, status text,
  created_at real, resolved_at real, resolved_by text
);
create table if not exists blocks (
  kind text not null, key text not null, label text, reason text, created_by text, created_at real,
  primary key (kind, key)
);
create table if not exists attempt_spend_flags (
  attempt_id text primary key, user_id text, email text,
  spend_usd real, limit_usd real, flagged_at real, notified integer default 0
);
create table if not exists attempt_budget (
  attempt_id text primary key, limit_usd real, approved_by text, approved_at real, note text
);
create table if not exists broadcasts (
  id integer primary key autoincrement, title text, body text, audience text,
  created_by text, created_at real, ended_at real, ended_by text
);
create table if not exists broadcast_targets (
  broadcast_id integer not null, person text not null, email text, acked_at real, acked_attempt text,
  primary key (broadcast_id, person)
);
create index if not exists broadcast_targets_person on broadcast_targets(person, acked_at);
create table if not exists status_samples (minute integer primary key);
create table if not exists status_incidents (
  id integer primary key autoincrement, title text, components text, impact text,
  created_at real, resolved_at real, created_by text
);
create table if not exists status_updates (
  id integer primary key autoincrement, incident_id integer, status text, body text, created_at real, created_by text
);
create table if not exists status_notified (key text primary key, created_at real);
create table if not exists cb_training (
  user_id text primary key, email text, trained_at real, trained_by text, note text
);
"""


CACHE_KB = 262144  # the writer connection's page cache (256 MB)
READER_CACHE_KB = 8192  # each thread's read connection (8 MB; up to ~45 threads; the file itself is pinned in RAM)
CHECKPOINT_EVERY = 30  # seconds between background WAL checkpoints
PIN_EVERY = 60  # seconds between re-pinning the database file in memory (it grows)
BUSY_TIMEOUT_MS = 15000  # the portal and the meter service both write; 5 s was too short under disk load


def conn():
    global _conn
    if _conn is not None:  # open already: no lock (a read must not wait while a writer holds it)
        return _conn
    with _lock:
        if _conn is None:
            config.DATA_DIR.mkdir(parents=True, exist_ok=True)
            _conn = sqlite3.connect(config.DATA_DIR / "portal.db", check_same_thread=False, isolation_level=None)
            _conn.row_factory = sqlite3.Row
            _conn.execute("pragma journal_mode=wal")
            # WAL + synchronous=NORMAL: a commit no longer waits for an fsync (only checkpoints do), so the write
            # lock isn't held for seconds while the disk is busy (boat snapshots read the whole disk). Commits stay
            # safe if the process crashes; only an OS crash/power loss can drop the last few. 2026-11-02: 5 s
            # busy waits ran out under that load, runs crashed on "database is locked" and stayed "running".
            _conn.execute("pragma synchronous=normal")
            _conn.execute(f"pragma busy_timeout={BUSY_TIMEOUT_MS}")
            # Busy disk (boat snapshots, 2026-11-02 stalls): keep reads in memory and fsyncs off the request path.
            # A 256 MB page cache in the process (the OS page cache gets evicted by the snapshot's reads), and no
            # automatic checkpoint: the commit that crossed 1,000 WAL pages used to fsync the whole database while
            # holding the portal's lock. checkpoint_loop() does it in its own thread and connection instead.
            _conn.execute(f"pragma cache_size=-{CACHE_KB}")
            _conn.execute("pragma wal_autocheckpoint=0")
            _conn.executescript(SCHEMA)
            migrate()
        return _conn


def _reader():
    """This thread's read-only connection (made on first use). WAL readers never wait for a writer, so reads no longer
    queue behind the portal's write lock: 2026-11-02 the event loop sat in q() waiting for that lock while another
    thread's write crawled through a disk boat's snapshot had saturated (or froze), and every page stalled."""
    conn()  # schema + migrations first
    path = config.DATA_DIR / "portal.db"
    if getattr(_local, "reader_path", None) != path:
        reader = sqlite3.connect(f"file:{path}?mode=ro", uri=True, isolation_level=None, check_same_thread=False)
        reader.row_factory = sqlite3.Row
        reader.execute(f"pragma busy_timeout={BUSY_TIMEOUT_MS}")
        reader.execute(f"pragma cache_size=-{READER_CACHE_KB}")
        _local.reader, _local.reader_path = reader, path
    return _local.reader


def q(sql, args=()):
    """Read. On this thread's own read-only connection: it sees every committed write at once (WAL, autocommit) and
    never waits for one. Statements that write (a pragma, an insert ... returning) go to the writer."""
    reader = getattr(_local, "active", None)
    if reader is None and sql.lstrip()[:7].lower().startswith(("select", "with ", "with\n")):
        try:
            reader = _reader()
        except sqlite3.Error:
            reader = None
    if reader is not None:
        try:
            return [dict(r) for r in reader.execute(sql, args).fetchall()]
        except sqlite3.OperationalError as error:
            if "readonly" not in str(error) and "read-only" not in str(error):
                raise
    with _lock:
        return [dict(r) for r in conn().execute(sql, args).fetchall()]


@contextlib.contextmanager
def reading():
    """Run q()/one() in this block on the thread's own read-only connection, without the global lock. For heavy
    read-only pages (the /admin lists): WAL lets them read while others write, so they no longer stall every
    other request. Don't write, or read-then-write, inside it."""
    reader = _reader()
    outer = getattr(_local, "active", None)
    _local.active = reader
    try:
        yield
    finally:
        _local.active = outer


def one(sql, args=()):
    rows = q(sql, args)
    return rows[0] if rows else None


def ex(sql, args=()):
    """Write. If the database is still locked after busy_timeout (the portal and the meter both write), try again a few
    times, without holding the portal's lock in between: a crashed run that stays "running" costs far more than a
    second's wait (2026-11-02: runs failed on "database is locked")."""
    for wait in (1, 2, 4, None):
        try:
            with _lock:
                cur = conn().execute(sql, args)
                return cur.lastrowid
        except sqlite3.OperationalError as error:
            if wait is None or "locked" not in str(error) and "busy" not in str(error):
                raise
        time.sleep(wait)


def update(table, key, key_value, **fields):
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    ex(f"update {table} set {cols} where {key}=?", (*fields.values(), key_value))


# ---- chats / messages -------------------------------------------------------

def get_chat(attempt_id):
    return one("select * from chats where attempt_id=?", (attempt_id,))


def upsert_chat(attempt_id, task_id, project_id, user_id, email, session_id):
    now = time.time()
    if get_chat(attempt_id):
        # Signing in doesn't count as activity: last_activity is the last message (see add_message).
        update("chats", "attempt_id", attempt_id, task_id=task_id, project_id=project_id, user_id=user_id, email=email)
    else:
        ex("insert into chats(attempt_id, task_id, project_id, user_id, email, session_id, created_at, last_activity)"
           " values (?,?,?,?,?,?,?,?)", (attempt_id, task_id, project_id, user_id, email, session_id, now, now))
    return get_chat(attempt_id)


def add_message(attempt_id, role, content, meta=None):
    now = time.time()
    mid = ex("insert into messages(attempt_id, role, content, meta, created_at) values (?,?,?,?,?)",
             (attempt_id, role, content, json.dumps(meta or {}), now))
    update("chats", "attempt_id", attempt_id, last_activity=now)
    return message(mid)


def message(mid):
    row = one("select * from messages where id=?", (mid,))
    if row:
        row["meta"] = json.loads(row["meta"] or "{}")
    return row


def messages(attempt_id, after=0):
    rows = q("select * from messages where attempt_id=? and id>? order by id", (attempt_id, after))
    for r in rows:
        r["meta"] = json.loads(r["meta"] or "{}")
    return rows


# ---- runs ------------------------------------------------------------------

def get_run(run_id):
    row = one("select * from runs where id=?", (run_id,))
    if row:
        row["models"] = json.loads(row["models"] or "{}")
    return row


def active_run(attempt_id):
    return one("select * from runs where attempt_id=? and status in ('queued','running') order by created_at desc limit 1",
               (attempt_id,))


def runs_today(attempt_id, kind):
    """New reviews count as reviews; follow-ups and answers to a review's question count as follow-ups."""
    since = time.time() - 24 * 3600
    which = "kind='review' and continuation=0" if kind == "review" else "(kind='followup' or continuation=1)"
    row = one(f"select count(*) n from runs where attempt_id=? and {which} and created_at>? "
              "and status in ('queued','running','done')", (attempt_id, since))
    return row["n"]


def migrate():
    """Add columns introduced after the first deploy."""
    have = {r["name"] for r in q("pragma table_info(runs)")}
    for col, decl in (("continuation", "integer default 0"), ("session_id", "text"), ("resumed", "integer default 0"),
                      ("runner_box", "text"), ("boat_phase", "text"), ("loadtest", "integer default 0"),
                      ("milestones", "text"), ("box_ready_at", "real"), ("review_started_at", "real"),
                      ("inputs_after", "text"), ("rerun_forced", "integer default 0"), ("container", "text")):
        if col not in have:
            ex(f"alter table runs add column {col} {decl}")
    chat_cols = {r["name"] for r in q("pragma table_info(chats)")}
    if "pending_review_run" not in chat_cols:
        ex("alter table chats add column pending_review_run text")
    if "session_guard" not in chat_cols:  # which version of the assistant's rules the follow-up session started on
        ex("alter table chats add column session_guard text")
    if "archived_from" not in chat_cols:  # a previous holder's chat, moved aside when the attempt ID was re-served
        ex("alter table chats add column archived_from text")
        ex("alter table chats add column archived_at real")
    # Who started each run and for which task, frozen at run time, so spend stays attributed correctly
    # (an admin opening someone's attempt is billed to the admin, not the contributor).
    if "actor_email" not in have:
        for col, decl in (("task_id", "text"), ("actor_user_id", "text"), ("actor_email", "text"),
                          ("actor_admin", "integer default 0")):
            ex(f"alter table runs add column {col} {decl}")
        ex("update runs set task_id=(select task_id from chats c where c.attempt_id=runs.attempt_id),"
           " actor_user_id=(select user_id from chats c where c.attempt_id=runs.attempt_id),"
           " actor_email=(select email from chats c where c.attempt_id=runs.attempt_id)")
    proof_cols = {r["name"] for r in q("pragma table_info(proofs)")}
    if "stale_notified" not in proof_cols:  # which changed inputs the chat was last told made this pass outdated
        ex("alter table proofs add column stale_notified text")
    ex("create index if not exists runs_created on runs(created_at)")
    ex("create index if not exists runs_proxy_token on runs(proxy_token)")  # the metering proxy checks tokens here
    ex("create index if not exists llm_calls_created on llm_calls(created_at)")  # status page hourly error counts
    # last_activity = the chat's latest message. Opening a chat or signing in used to bump it, which made the
    # chat lists say "just now" for a chat nobody had touched; put those back to the real last interaction.
    ex("update chats set last_activity = coalesce((select max(created_at) from messages m "
       "where m.attempt_id = chats.attempt_id), last_activity)")


def record_call(run_id, model, path, http_status, usage, cost):
    ex("insert into llm_calls(run_id, model, path, http_status, input_tokens, output_tokens, cache_read_tokens,"
       " cache_creation_tokens, cost_usd, created_at) values (?,?,?,?,?,?,?,?,?,?)",
       (run_id, model, path, http_status, usage.get("input_tokens", 0), usage.get("output_tokens", 0),
        usage.get("cache_read_input_tokens", 0), usage.get("cache_creation_input_tokens", 0), cost, time.time()))
    with _lock:
        run = get_run(run_id)
        if not run:
            return
        models = run["models"]
        m = models.setdefault(model or "unknown", {"calls": 0, "input": 0, "output": 0, "cache_read": 0, "cache_creation": 0})
        m["calls"] += 1
        m["input"] += usage.get("input_tokens", 0)
        m["output"] += usage.get("output_tokens", 0)
        m["cache_read"] += usage.get("cache_read_input_tokens", 0)
        m["cache_creation"] += usage.get("cache_creation_input_tokens", 0)
        ex("update runs set calls=calls+1, input_tokens=input_tokens+?, output_tokens=output_tokens+?,"
           " cache_read_tokens=cache_read_tokens+?, cache_creation_tokens=cache_creation_tokens+?,"
           " litellm_cost_usd=litellm_cost_usd+?, models=? where id=?",
           (usage.get("input_tokens", 0), usage.get("output_tokens", 0), usage.get("cache_read_input_tokens", 0),
            usage.get("cache_creation_input_tokens", 0), cost or 0, json.dumps(models), run_id))


# ---- keeping the database off the disk's critical path ------------------------------------------------------------
_background = {"started": False}


def checkpoint_once(mode="PASSIVE"):
    """Copy the WAL into the database file, on a connection of its own: nothing waits for it (PASSIVE never blocks
    readers or writers). Returns sqlite's (busy, wal pages, checkpointed pages)."""
    c = sqlite3.connect(config.DATA_DIR / "portal.db", timeout=1)
    try:
        return tuple(c.execute(f"pragma wal_checkpoint({mode})").fetchone())
    finally:
        c.close()


def _checkpoint_loop():
    while True:
        time.sleep(CHECKPOINT_EVERY)
        try:
            busy, wal, done = checkpoint_once()
            if wal > 200000:  # ~800 MB of WAL: something keeps a reader open; worth knowing
                _log().warning("WAL is %d pages (%d checkpointed)", wal, done)
        except Exception:  # noqa: BLE001
            _log().exception("background checkpoint failed")


def _log():
    import logging
    return logging.getLogger("portal.db")


class _Pin:
    """Hold portal.db (and its WAL) in RAM: map the files shared and read-only and mlock them (libc, through ctypes),
    so a read never waits on a disk the boat snapshot is saturating; the locked pages are the page cache sqlite reads.
    Without the right (RLIMIT_MEMLOCK, eval-portal.service LimitMEMLOCK) it asks the kernel to keep them read in
    (MADV_WILLNEED, again every minute)."""
    PROT_READ, MAP_SHARED, MADV_WILLNEED = 1, 1, 3

    def __init__(self):
        import ctypes
        import ctypes.util
        self.ct = ctypes
        self.libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        self.libc.mmap.restype = ctypes.c_void_p
        self.libc.mmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_long]
        for f in ("munmap", "mlock", "madvise"):
            getattr(self.libc, f).argtypes = [ctypes.c_void_p, ctypes.c_size_t] + ([ctypes.c_int] if f == "madvise" else [])
            getattr(self.libc, f).restype = ctypes.c_int
        self.maps = {}  # file name -> (size, address, locked)

    def _map(self, path, size):
        import os
        fd = os.open(path, os.O_RDONLY)
        try:
            addr = self.libc.mmap(None, size, self.PROT_READ, self.MAP_SHARED, fd, 0)
        finally:
            os.close(fd)
        if addr in (None, self.ct.c_void_p(-1).value):
            raise OSError(self.ct.get_errno(), "mmap failed")
        locked = self.libc.mlock(addr, size) == 0
        if not locked:
            self.libc.madvise(addr, size, self.MADV_WILLNEED)
        return addr, locked

    def refresh(self):
        for name in ("portal.db", "portal.db-wal"):
            path = config.DATA_DIR / name
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            old = self.maps.get(name)
            if old and old[0] == size:
                if not old[2]:
                    self.libc.madvise(old[1], size, self.MADV_WILLNEED)
                continue
            new = None
            if size:
                addr, locked = self._map(path, size)
                new = (size, addr, locked)
                if not old or old[2] != locked:
                    _log().info("%s %s in memory (%.0f MB)", name, "locked" if locked else "kept warm", size / 1e6)
            if old:
                self.libc.munmap(old[1], old[0])
            if new:
                self.maps[name] = new
            else:
                self.maps.pop(name, None)

    def locked(self):
        return {name: m[2] for name, m in self.maps.items()}


PIN_DELAY = 120  # seconds after start before the first pin: reading 1 GB in at startup slowed the start past the
# deploy's health check (2026-11-02 19:04, with a boat snapshot loading the disk), so it waits for the portal to be up


def _pin_loop():
    time.sleep(PIN_DELAY)
    pin = _Pin()
    while True:
        try:
            pin.refresh()
        except Exception:  # noqa: BLE001
            _log().exception("pinning the database in memory failed")
        time.sleep(PIN_EVERY)


def start_background(pin=True):
    """Start the checkpoint (and pin) threads once per process (the portal and the meter both call this)."""
    if _background["started"]:
        return
    _background["started"] = True
    conn()
    threading.Thread(target=_checkpoint_loop, name="db-checkpoint", daemon=True).start()
    if pin:
        threading.Thread(target=_pin_loop, name="db-pin", daemon=True).start()
