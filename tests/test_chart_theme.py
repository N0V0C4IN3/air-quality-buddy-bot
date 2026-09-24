"""Per-chat chart theme — Telegram never tells a bot which theme the viewer uses,
so the preference is stored."""
import pytest

from common.db import ChatRepository
from subscriber_cache import InMemorySubscriberCache
from subscriptions import DARK, LIGHT, Subscriptions


@pytest.fixture
def subs(db):
    return Subscriptions(db, InMemorySubscriberCache())


def test_an_unknown_chat_gets_light_charts(subs):
    assert subs.theme(4242) == LIGHT


def test_theme_survives_a_round_trip(subs):
    subs.set_theme(4242, DARK)
    assert subs.theme(4242) == DARK


def test_toggle_flips_and_returns_the_new_theme(subs):
    assert subs.toggle_theme(1) == DARK
    assert subs.theme(1) == DARK
    assert subs.toggle_theme(1) == LIGHT
    assert subs.theme(1) == LIGHT


def test_an_unknown_theme_is_refused(subs):
    with pytest.raises(ValueError, match="sepia"):
        subs.set_theme(1, "sepia")


def test_setting_a_theme_does_not_subscribe_the_chat(subs):
    """Preferences and subscription are separate decisions."""
    subs.set_theme(7, DARK)
    assert subs.is_subscribed(7) is False


def test_theme_survives_subscribing_and_unsubscribing(subs):
    subs.set_theme(7, DARK)
    subs.subscribe(7)
    subs.unsubscribe(7)
    assert subs.theme(7) == DARK


def test_subscription_survives_a_theme_change(subs):
    subs.subscribe(7)
    subs.toggle_theme(7)
    assert subs.is_subscribed(7) is True


def test_themes_are_per_chat(subs):
    subs.set_theme(1, DARK)
    assert subs.theme(2) == LIGHT


def test_existing_rows_default_to_light(db):
    """The migration backfills with a server default, so old rows stay valid."""
    with db.session() as s:
        ChatRepository(s).upsert(99, True)
    with db.session() as s:
        assert ChatRepository(s).get_theme(99) == LIGHT
