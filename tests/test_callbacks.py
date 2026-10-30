"""Callback data travels on buttons that may be months old — the grammar has to
stay stable and reject anything else."""
import pytest

from callbacks import (
    ALERT, INFO, MAX_BYTES, NEW_CARD, SUBSCRIBE, THEME, WINDOW, Callback,
    CallbackError, decode, info_action, subscribe_action, theme_action, view_action,
)
from reports import STATUS, VIEWS, Window

CARDS = [*VIEWS, None]   # None: the tapped card is an alert


def every_action():
    for view in VIEWS:
        yield view_action(view)
        yield view_action(view, new_card=True)
    for on in CARDS:
        for theme in ("light", "dark"):
            yield theme_action(on, theme)
        for subscribed in (True, False):
            yield subscribe_action(on, subscribed)
        yield info_action(on)


def test_view_actions_round_trip():
    for view in VIEWS:
        assert decode(view_action(view)) == Callback(WINDOW, view)
        assert decode(view_action(view, new_card=True)) == Callback(NEW_CARD, view)


@pytest.mark.parametrize("on", CARDS)
def test_settings_actions_round_trip_on_every_card(on):
    assert decode(theme_action(on, "dark")) == Callback(THEME, on, "dark")
    assert decode(subscribe_action(on, True)) == Callback(SUBSCRIBE, on, "on")
    assert decode(subscribe_action(on, False)) == Callback(SUBSCRIBE, on, "off")
    assert decode(info_action(on)) == Callback(INFO, on)


def test_every_action_is_distinct_and_fits_telegram_limit():
    actions = list(every_action())
    assert len(set(actions)) == len(actions)
    assert all(len(a.encode()) <= MAX_BYTES for a in actions)


def test_the_wire_format_is_the_documented_one():
    """Changing these strings orphans every button already on someone's screen."""
    assert view_action(Window.LAST_12H) == "w:last_12h"
    assert view_action(STATUS) == "w:status"
    assert view_action(Window.TODAY, new_card=True) == "n:today"
    assert theme_action(Window.PATTERNS, "dark") == "theme:patterns:dark"
    assert subscribe_action(None, False) == f"sub:{ALERT}:off"
    assert info_action(Window.TODAY) == "info:today"


@pytest.mark.parametrize("data, expected", [
    ("theme:patterns", Callback(THEME, Window.PATTERNS)),
    ("sub:status", Callback(SUBSCRIBE, STATUS)),
    ("info:status", Callback(INFO, STATUS)),
])
def test_buttons_already_on_screen_still_decode(data, expected):
    """Shipped before settings buttons carried their target state: a missing
    argument means toggle."""
    assert decode(data) == expected


def test_settings_buttons_carry_the_state_they_set():
    """A label on an old card is frozen; a toggle behind it would do the
    opposite of what it says once the state had moved on."""
    assert decode(subscribe_action(STATUS, False)).arg == "off"
    assert decode(theme_action(STATUS, "light")).arg == "light"


@pytest.mark.parametrize("data", [
    "", "nonsense", "w", "w:", "w:yesterday", "x:today", "theme:", ":today", None,
    "sub:", "info:card", "w:alert", "n:alert", "theme:today:sepia", "sub:today:maybe",
    "w:today:extra",
])
def test_malformed_data_is_rejected(data):
    with pytest.raises(CallbackError):
        decode(data)


def test_theme_action_refuses_an_unknown_theme():
    with pytest.raises(CallbackError):
        theme_action(STATUS, "sepia")


def test_rejection_names_the_offending_data():
    with pytest.raises(CallbackError, match="w:yesterday"):
        decode("w:yesterday")
