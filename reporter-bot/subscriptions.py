# subscriptions.py
"""Owns who receives alerts.

Postgres `chats` is the record, the cache is the fast read path, and the order
of the two writes lives here — not in a handler. A cache write that fails leaves
the record correct and self-heals on the next preload.
"""
from __future__ import annotations

import logging
from typing import Set

from common.db import ChatRepository, Database

from subscriber_cache import SubscriberCache

log = logging.getLogger(__name__)


class Subscriptions:
    def __init__(self, db: Database, cache: SubscriberCache) -> None:
        self._db = db
        self._cache = cache

    def is_subscribed(self, chat_id: int) -> bool:
        return self._cache.contains(chat_id)

    def all(self) -> Set[int]:
        return self._cache.members()

    def subscribe(self, chat_id: int) -> None:
        self._set(chat_id, True)

    def unsubscribe(self, chat_id: int) -> None:
        self._set(chat_id, False)

    def toggle(self, chat_id: int) -> bool:
        """Flip the subscription. Returns the new state."""
        subscribed = not self.is_subscribed(chat_id)
        self._set(chat_id, subscribed)
        return subscribed

    def preload(self) -> int:
        """Warm the cache from the record. Safe to call at every startup."""
        with self._db.session() as s:
            ids = [int(cid) for cid in ChatRepository(s).get_subscribed_ids()]
        self._cache.load(ids)
        return len(ids)

    # ---------- internals ----------

    def _set(self, chat_id: int, subscribed: bool) -> None:
        # Record first: if the cache write fails, preload repairs it.
        with self._db.session() as s:
            ChatRepository(s).upsert(chat_id, subscribed)

        try:
            if subscribed:
                self._cache.add(chat_id)
            else:
                self._cache.remove(chat_id)
        except Exception:
            log.exception("Cache write failed for chat_id=%s; record is correct", chat_id)
