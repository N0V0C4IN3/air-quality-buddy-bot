import pytest

from common.db import Chat, ChatRepository
from subscriber_cache import InMemorySubscriberCache
from subscriptions import Subscriptions


@pytest.fixture
def cache():
    return InMemorySubscriberCache()


@pytest.fixture
def subs(db, cache):
    return Subscriptions(db, cache)


def stored_state(db, chat_id):
    with db.session() as s:
        chat = s.query(Chat).filter_by(chat_id=str(chat_id)).first()
        return None if chat is None else chat.is_subscribed


def test_unknown_chat_is_not_subscribed(subs):
    assert subs.is_subscribed(42) is False


def test_subscribe_writes_the_record_and_the_cache(subs, db, cache):
    subs.subscribe(42)

    assert stored_state(db, 42) is True
    assert cache.members() == {42}
    assert subs.is_subscribed(42) is True


def test_unsubscribe_keeps_the_row_and_clears_the_cache(subs, db, cache):
    subs.subscribe(42)
    subs.unsubscribe(42)

    assert stored_state(db, 42) is False   # row remains, flag flips
    assert cache.members() == set()
    assert subs.is_subscribed(42) is False


def test_toggle_returns_the_new_state(subs):
    assert subs.toggle(42) is True
    assert subs.toggle(42) is False
    assert subs.toggle(42) is True


def test_subscribing_twice_does_not_duplicate_the_row(subs, db):
    subs.subscribe(42)
    subs.subscribe(42)

    with db.session() as s:
        assert s.query(Chat).filter_by(chat_id="42").count() == 1


def test_all_returns_every_subscriber(subs):
    subs.subscribe(1)
    subs.subscribe(2)
    subs.subscribe(3)
    subs.unsubscribe(2)

    assert subs.all() == {1, 3}


def test_preload_warms_an_empty_cache_from_the_record(db, cache):
    Subscriptions(db, InMemorySubscriberCache()).subscribe(7)

    cold = Subscriptions(db, cache)
    assert cold.is_subscribed(7) is False   # cache is empty before preload

    assert cold.preload() == 1
    assert cold.is_subscribed(7) is True


def test_preload_ignores_unsubscribed_chats(db, cache):
    subs = Subscriptions(db, cache)
    subs.subscribe(1)
    subs.subscribe(2)
    subs.unsubscribe(1)

    fresh = Subscriptions(db, InMemorySubscriberCache())
    fresh.preload()
    assert fresh.all() == {2}


def test_preload_is_idempotent(subs):
    subs.subscribe(5)
    subs.preload()
    subs.preload()
    assert subs.all() == {5}


def test_record_survives_a_failing_cache_write(db):
    """The record is the truth; a cache failure must not lose the subscription."""

    class BrokenCache(InMemorySubscriberCache):
        def add(self, chat_id):
            raise RuntimeError("redis is down")

    subs = Subscriptions(db, BrokenCache())
    subs.subscribe(42)  # must not raise

    assert stored_state(db, 42) is True


def test_preload_repairs_a_cache_that_missed_a_write(db):
    class BrokenCache(InMemorySubscriberCache):
        def add(self, chat_id):
            raise RuntimeError("redis is down")

    broken = BrokenCache()
    subs = Subscriptions(db, broken)
    subs.subscribe(42)
    assert subs.is_subscribed(42) is False  # cache missed it

    repaired = Subscriptions(db, InMemorySubscriberCache())
    repaired.preload()
    assert repaired.is_subscribed(42) is True


def test_chat_ids_are_stored_as_strings_and_read_back_as_ints(subs, db):
    """Telegram gives ints; the chats table keys on strings."""
    subs.subscribe(-1001234567890)

    with db.session() as s:
        assert ChatRepository(s).get_subscribed_ids() == ["-1001234567890"]
    assert subs.all() == {-1001234567890}


def test_upsert_does_not_commit_on_its_own(db):
    """Regression: upsert used to commit inside a session block that also commits."""
    with db.session() as s:
        ChatRepository(s).upsert(99, True)
        assert s.in_transaction()

    assert stored_state(db, 99) is True


def test_themes_answers_for_many_chats_at_once(subs):
    subs.subscribe(1)
    subs.subscribe(2)
    subs.set_theme(2, "dark")

    # 3 has never been seen; it gets the default rather than going missing.
    assert subs.themes([1, 2, 3]) == {1: "light", 2: "dark", 3: "light"}


def test_themes_of_nothing_asks_the_database_nothing(subs):
    assert subs.themes([]) == {}


def test_themes_agrees_with_theme(subs):
    subs.subscribe(7)
    subs.set_theme(7, "dark")

    assert subs.themes([7])[7] == subs.theme(7)
