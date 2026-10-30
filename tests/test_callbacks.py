"""Callback data travels on buttons that may be months old — the grammar has to
stay stable and reject anything else."""
import pytest

from callbacks import (
    INFO, MAX_BYTES, SUBSCRIBE, THEME, WINDOW, CallbackError, decode, info_action,
    subscribe_action, theme_action, window_action,
)
from reports import STATUS, VIEWS, Window

ACTIONS = [(WINDOW, window_action), (THEME, theme_action),
           (SUBSCRIBE, subscribe_action), (INFO, info_action)]


@pytest.mark.parametrize("prefix, action", ACTIONS)
def test_every_action_round_trips_for_every_view(prefix, action):
    for view in VIEWS:
        assert decode(action(view)) == (prefix, view)


def test_the_actions_are_distinguishable():
    assert len({action(Window.TODAY) for _, action in ACTIONS}) == len(ACTIONS)


@pytest.mark.parametrize("prefix, action", ACTIONS)
def test_encoded_data_fits_telegram_limit(prefix, action):
    for view in VIEWS:
        assert len(action(view).encode()) <= MAX_BYTES


def test_the_wire_format_is_the_documented_one():
    """Changing these strings orphans every button already on someone's screen."""
    assert window_action(Window.LAST_12H) == "w:last_12h"
    assert theme_action(Window.PATTERNS) == "theme:patterns"
    assert window_action(STATUS) == "w:status"
    assert subscribe_action(STATUS) == "sub:status"
    assert info_action(Window.TODAY) == "info:today"


@pytest.mark.parametrize("data", [
    "", "nonsense", "w", "w:", "w:yesterday", "x:today", "theme:", ":today", None,
    "sub:", "info:card",
])
def test_malformed_data_is_rejected(data):
    with pytest.raises(CallbackError):
        decode(data)


def test_rejection_names_the_offending_data():
    with pytest.raises(CallbackError, match="w:yesterday"):
        decode("w:yesterday")
