"""Row plan and labels.

Telegram splits a row equally between its buttons and truncates any label that
overflows its share — which is how "📅 Today" and "🗓 Patterns" got cut off when
all four shared one row.
"""
import pytest

from keyboard_layout import (
    ACTIVE_MARK, DASHBOARD_LABEL, INFO_LABEL, MAX_LABEL_PER_HALF,
    MAX_LABEL_PER_THIRD, SUBSCRIBE_LABELS, THEME_LABELS, VIEW_LABELS,
    button_label, subscribe_label, theme_label, view_rows,
)
from reports import STATUS, VIEWS, Window


def width(label: str) -> int:
    """Characters, with the emoji presentation selector free, as on screen."""
    return len(label.replace("️", ""))


def test_every_view_has_a_label():
    assert set(VIEW_LABELS) == set(VIEWS)


def test_status_and_patterns_share_the_first_row():
    rows = view_rows(Window.TODAY)

    assert len(rows) == 2
    assert [v for v, _ in rows[0]] == [STATUS, Window.PATTERNS]
    assert [v for v, _ in rows[1]] == [Window.TODAY, Window.LAST_12H, Window.LAST_7D]


def test_no_row_holds_more_than_three_buttons():
    """A four-way split is what truncated the long labels."""
    for active in VIEWS:
        for row in view_rows(active):
            assert len(row) <= 3


def test_time_window_labels_fit_their_third():
    """Including the active marker, which is the widest each one ever gets."""
    for active in VIEWS:
        for view, label in view_rows(active)[1]:
            assert width(label) <= MAX_LABEL_PER_THIRD, (view, label)


@pytest.mark.parametrize("label", [
    *(button_label(v, v) for v in (STATUS, Window.PATTERNS)),
    *THEME_LABELS.values(), *SUBSCRIBE_LABELS.values(), INFO_LABEL, DASHBOARD_LABEL,
])
def test_half_row_labels_fit_their_half(label):
    assert width(label) <= MAX_LABEL_PER_HALF, label


def test_time_window_labels_carry_no_emoji():
    """Emoji cost roughly two characters of width in a shared row."""
    for _, label in view_rows(Window.TODAY)[1]:
        assert label.replace(ACTIVE_MARK, "").isascii()


def test_every_view_is_reachable_from_every_card():
    for active in VIEWS:
        offered = {v for row in view_rows(active) for v, _ in row}
        assert offered == set(VIEWS)


def test_the_active_view_is_marked():
    labels = {v: label for row in view_rows(STATUS) for v, label in row}

    assert labels[STATUS].startswith(ACTIVE_MARK)
    assert not labels[Window.TODAY].startswith(ACTIVE_MARK)


def test_exactly_one_button_is_marked():
    for active in VIEWS:
        marked = [
            label for row in view_rows(active) for _, label in row
            if label.startswith(ACTIVE_MARK)
        ]
        assert len(marked) == 1


@pytest.mark.parametrize("theme, expected", [
    ("light", "🌙 Dark mode"), ("dark", "☀️ Light mode"),
])
def test_theme_button_offers_the_other_theme(theme, expected):
    assert theme_label(theme) == expected


def test_unknown_theme_falls_back_to_offering_dark():
    assert theme_label("sepia") == theme_label("light")


@pytest.mark.parametrize("subscribed, expected", [
    (False, "🔔 Subscribe"), (True, "🔕 Unsubscribe"),
])
def test_subscribe_button_names_the_action(subscribed, expected):
    assert subscribe_label(subscribed) == expected


def test_button_label_is_stable_for_inactive_views():
    assert button_label(Window.LAST_7D, STATUS) == VIEW_LABELS[Window.LAST_7D]
