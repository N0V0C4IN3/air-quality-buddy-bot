"""Row plan and labels.

Telegram splits a row equally between its buttons and truncates any label that
overflows its share — which is how "📅 Today" and "🗓 Patterns" got cut off when
all four shared one row.
"""
import pytest

from keyboard_layout import (
    ACTIVE_MARK, MAX_LABEL_PER_THIRD, WINDOW_LABELS, button_label, theme_label,
    window_rows,
)
from reports import Window


def test_every_window_has_a_label():
    assert set(WINDOW_LABELS) == set(Window)


def test_patterns_gets_its_own_row():
    """A four-way split is what truncated the long labels."""
    rows = window_rows(Window.TODAY)

    assert len(rows) == 2
    assert len(rows[0]) == 3
    assert [w for w, _ in rows[1]] == [Window.PATTERNS]


def test_no_row_holds_more_than_three_buttons():
    for active in Window:
        for row in window_rows(active):
            assert len(row) <= 3


def test_shared_row_labels_fit_their_third():
    """Including the active marker, which is the widest each one ever gets."""
    for active in Window:
        for window, label in window_rows(active)[0]:
            assert len(label) <= MAX_LABEL_PER_THIRD, (window, label)


def test_time_window_labels_carry_no_emoji():
    """Emoji cost roughly two characters of width in a shared row."""
    for window, label in window_rows(Window.TODAY)[0]:
        assert label.replace(ACTIVE_MARK, "").isascii()


def test_every_window_is_reachable_from_every_card():
    for active in Window:
        offered = {w for row in window_rows(active) for w, _ in row}
        assert offered == set(Window)


def test_the_active_window_is_marked():
    rows = window_rows(Window.LAST_12H)
    labels = {w: label for row in rows for w, label in row}

    assert labels[Window.LAST_12H].startswith(ACTIVE_MARK)
    assert not labels[Window.TODAY].startswith(ACTIVE_MARK)


def test_exactly_one_button_is_marked():
    for active in Window:
        marked = [
            label for row in window_rows(active) for _, label in row
            if label.startswith(ACTIVE_MARK)
        ]
        assert len(marked) == 1


def test_patterns_keeps_its_full_label():
    """It has a whole row, so it is never the one that gets cut."""
    _, patterns_row = window_rows(Window.TODAY)
    assert patterns_row[0][1] == "🗓 Patterns"


@pytest.mark.parametrize("theme, expected", [
    ("light", "🌙 Dark charts"), ("dark", "☀️ Light charts"),
])
def test_theme_button_offers_the_other_theme(theme, expected):
    assert theme_label(theme) == expected


def test_unknown_theme_falls_back_to_offering_dark():
    assert theme_label("sepia") == theme_label("light")


def test_button_label_is_stable_for_inactive_windows():
    assert button_label(Window.LAST_7D, Window.TODAY) == WINDOW_LABELS[Window.LAST_7D]
