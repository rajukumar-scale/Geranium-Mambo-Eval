"""In-process pub/sub for server-sent events. Admin listeners receive every attempt's events."""

import asyncio
import json

_listeners = {}  # key (attempt id or "*admin*") -> set of asyncio.Queue
ADMIN = "*admin*"


def subscribe(key):
    q = asyncio.Queue(maxsize=500)
    _listeners.setdefault(key, set()).add(q)
    return q


def unsubscribe(key, q):
    _listeners.get(key, set()).discard(q)


def publish(attempt_id, event, data):
    payload = {"event": event, "attempt_id": attempt_id, "data": data}
    for key in (attempt_id, ADMIN):
        for q in list(_listeners.get(key, ())):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                pass  # a stalled browser tab; it reloads history on reconnect


def sse(payload):
    return f"event: {payload['event']}\ndata: {json.dumps(payload, default=str)}\n\n"
