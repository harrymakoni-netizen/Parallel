"""Progress events published to Redis for server-sent event streams.

The database remains the durable record; events are best-effort notifications
(clients that miss one simply poll the run/job record).
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

import redis

from .settings import get_settings


@lru_cache
def _client() -> redis.Redis:
    return redis.Redis.from_url(get_settings().redis_url, socket_timeout=2, socket_connect_timeout=2)


def channel(kind: str, object_id: str) -> str:
    return f"parallel:{kind}:{object_id}"


def publish(kind: str, object_id: str, payload: dict[str, Any]) -> None:
    try:
        _client().publish(channel(kind, object_id), json.dumps(payload, default=str))
    except redis.RedisError:
        pass  # notifications are best effort; state is in the database


def subscribe(kind: str, object_id: str):
    ps = _client().pubsub(ignore_subscribe_messages=True)
    ps.subscribe(channel(kind, object_id))
    return ps
