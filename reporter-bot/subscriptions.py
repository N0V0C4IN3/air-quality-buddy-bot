# subscriptions.py
"""Owns who receives alerts, and how their charts are drawn.

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

LIGHT = "light"
DARK = "dark"
THEMES = (LIGHT, DARK)


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

    # ---------- chart theme ----------

    def theme(self, chat_id: int) -> str:
        """Which palette this chat's charts are rendered in."""
        with self._db.session() as s:
            return ChatRepository(s).get_theme(chat_id)

    def themes(self, chat_ids) -> dict:
        """Themes for many chats at once, defaulting to light.

        The alert fan-out needs one per subscriber. Asking chat by chat opened
        a session, a transaction and a round trip per person - a hundred trips
        to Postgres to send one alert - and the answer is a single SELECT.
        """
        ids = list(chat_ids)
        if not ids:
            return {}
        with self._db.session() as s:
            stored = ChatRepository(s).get_themes(ids)
        return {cid: stored.get(str(cid), LIGHT) for cid in ids}

    def set_theme(self, chat_id: int, theme: str) -> str:
        if theme not in THEMES:
            raise ValueError(f"unknown chart theme: {theme!r}")
        with self._db.session() as s:
            ChatRepository(s).set_theme(chat_id, theme)
        return theme

    def toggle_theme(self, chat_id: int) -> str:
        """Flip light/dark. Returns the new theme."""
        return self.set_theme(chat_id, DARK if self.theme(chat_id) == LIGHT else LIGHT)

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
