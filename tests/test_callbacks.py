"""Callback data travels on buttons that may be months old — the grammar has to
stay stable and reject anything else."""
import pytest

from callbacks import (
    MAX_BYTES, THEME, WINDOW, CallbackError, decode, theme_action, window_action,
)
from reports import Window


def test_window_action_round_trips():
    for window in Window:
        assert decode(window_action(window)) == (WINDOW, window)


def test_theme_action_round_trips():
    for window in Window:
        assert decode(theme_action(window)) == (THEME, window)


def test_the_two_actions_are_distinguishable():
    assert window_action(Window.TODAY) != theme_action(Window.TODAY)


def test_encoded_data_fits_telegram_limit():
    for window in Window:
        assert len(window_action(window).encode()) <= MAX_BYTES
        assert len(theme_action(window).encode()) <= MAX_BYTES


def test_the_wire_format_is_the_documented_one():
    """Changing these strings orphans every button already on someone's screen."""
    assert window_action(Window.LAST_12H) == "w:last_12h"
    assert theme_action(Window.PATTERNS) == "theme:patterns"


@pytest.mark.parametrize("data", [
    "", "nonsense", "w", "w:", "w:yesterday", "x:today", "theme:", ":today", None,
])
def test_malformed_data_is_rejected(data):
    with pytest.raises(CallbackError):
        decode(data)


def test_rejection_names_the_offending_data():
    with pytest.raises(CallbackError, match="w:yesterday"):
        decode("w:yesterday")
