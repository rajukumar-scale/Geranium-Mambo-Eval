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


def conn():
    global _conn
    with _lock:
        if _conn is None:
            config.DATA_DIR.mkdir(parents=True, exist_ok=True)
            _conn = sqlite3.connect(config.DATA_DIR / "portal.db", check_same_thread=False, isolation_level=None)
            _conn.row_factory = sqlite3.Row
            _conn.execute("pragma journal_mode=wal")
            _conn.execute("pragma busy_timeout=5000")
            _conn.executescript(SCHEMA)
            migrate()
        return _conn


def q(sql, args=()):
    reader = getattr(_local, "active", None)
    if reader is not None:
        return [dict(r) for r in reader.execute(sql, args).fetchall()]
    with _lock:
        return [dict(r) for r in conn().execute(sql, args).fetchall()]


@contextlib.contextmanager
def reading():
    """Run q()/one() in this block on the thread's own read-only connection, without the global lock. For heavy
    read-only pages (the /admin lists): WAL lets them read while others write, so they no longer stall every
    other request. Don't write, or read-then-write, inside it."""
    conn()  # schema + migrations first
    path = config.DATA_DIR / "portal.db"
    if getattr(_local, "reader_path", None) != path:
        reader = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        reader.row_factory = sqlite3.Row
        reader.execute("pragma busy_timeout=5000")
        _local.reader, _local.reader_path = reader, path
    outer = getattr(_local, "active", None)
    _local.active = _local.reader
    try:
        yield
    finally:
        _local.active = outer


def one(sql, args=()):
    rows = q(sql, args)
    return rows[0] if rows else None


def ex(sql, args=()):
    with _lock:
        cur = conn().execute(sql, args)
        return cur.lastrowid


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
                      ("milestones", "text"), ("box_ready_at", "real"), ("review_started_at", "real")):
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
