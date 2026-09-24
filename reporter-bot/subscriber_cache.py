# subscriber_cache.py
"""Adapters for the subscriber cache seam.

Two of them: Redis in production, an in-memory set for tests. Both are internal
to `Subscriptions` — handlers never talk to either directly.
"""
from __future__ import annotations

import logging
from typing import Iterable, Protocol, Set

import redis

logger = logging.getLogger(__name__)

SUBSCRIBERS_SET_KEY = "subscribers"


class SubscriberCache(Protocol):
    def members(self) -> Set[int]: ...
    def add(self, chat_id: int) -> None: ...
    def remove(self, chat_id: int) -> None: ...
    def contains(self, chat_id: int) -> bool: ...
    def load(self, chat_ids: Iterable[int]) -> None: ...


class RedisSubscriberCache:
    def __init__(self, host: str, port: int = 6379, db: int = 0, password: str | None = None):
        self.client = redis.Redis(
            host=host,
            port=port,
            db=db,
            password=password,
            decode_responses=True,
        )
        logger.info("Connected to Redis at %s:%s, db=%s", host, port, db)

    def members(self) -> Set[int]:
        subscribers = {int(cid) for cid in self.client.smembers(SUBSCRIBERS_SET_KEY)}
        logger.debug("Current subscribers: %s", subscribers)
        return subscribers

    def add(self, chat_id: int) -> None:
        self.client.sadd(SUBSCRIBERS_SET_KEY, chat_id)
        logger.info("Cached subscriber %s", chat_id)

    def remove(self, chat_id: int) -> None:
        self.client.srem(SUBSCRIBERS_SET_KEY, chat_id)
        logger.info("Uncached subscriber %s", chat_id)

    def contains(self, chat_id: int) -> bool:
        return bool(self.client.sismember(SUBSCRIBERS_SET_KEY, chat_id))

    def load(self, chat_ids: Iterable[int]) -> None:
        ids = list(chat_ids)
        if ids:
            self.client.sadd(SUBSCRIBERS_SET_KEY, *ids)
        logger.info("Preloaded %d subscriber(s) into Redis", len(ids))


class InMemorySubscriberCache:
    """Used by tests, and as a fallback the bot can run on without Redis."""

    def __init__(self) -> None:
        self._ids: Set[int] = set()

    def members(self) -> Set[int]:
        return set(self._ids)

    def add(self, chat_id: int) -> None:
        self._ids.add(int(chat_id))

    def remove(self, chat_id: int) -> None:
        self._ids.discard(int(chat_id))

    def contains(self, chat_id: int) -> bool:
        return int(chat_id) in self._ids

    def load(self, chat_ids: Iterable[int]) -> None:
        self._ids.update(int(c) for c in chat_ids)
