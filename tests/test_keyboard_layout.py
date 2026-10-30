"""The card's keyboard: labels, actions and how rows divide.

Telegram splits a row equally between its buttons and truncates any label that
overflows its share — which is how "📅 Today" and "🗓 Patterns" got cut off when
all four shared one row.
"""
import pytest

from callbacks import NEW_CARD, SUBSCRIBE, THEME, WINDOW, decode
from keyboard_layout import (
    ACTIVE_MARK, ROW_BUDGET, VIEW_LABELS, button_label, card_rows, subscribe_label,
    theme_label,
)
from reports import STATUS, VIEWS, Window

DASH = "https://example.ts.net"
CARDS = [*VIEWS, None]   # None: an alert


def plans():
    for on in CARDS:
        for theme in ("light", "dark"):
            for subscribed in (True, False):
                yield on, card_rows(on, theme=theme, subscribed=subscribed,
                                    dashboard_url=DASH)


def width(label: str) -> int:
    """Characters, with the emoji presentation selector free, as on screen."""
    return len(label.replace("\ufe0f", ""))


def buttons(rows):
    return [b for row in rows for b in row]


def view_buttons(rows):
    return {decode(b.callback).view: b for b in buttons(rows)
            if b.callback and decode(b.callback).action in (WINDOW, NEW_CARD)}


def test_every_view_has_a_label():
    assert set(VIEW_LABELS) == set(VIEWS)


def test_the_row_plan():
    rows = card_rows(Window.TODAY, theme="light", subscribed=False, dashboard_url=DASH)
    assert [[b.label for b in row] for row in rows] == [
        ["📟 Status", "🗓 Patterns"],
        ["● Today", "12h", "7d"],
        ["🌙 Dark mode", "🔔 Subscribe"],
        ["ℹ️ Info", "📊 Dashboard"],
    ]


def test_no_row_holds_more_than_three_buttons():
    """A four-way split is what truncated the long labels."""
    for _, rows in plans():
        assert all(len(row) <= 3 for row in rows)


def test_every_label_fits_its_share_of_the_row():
    """Including the active marker, which is the widest each one ever gets."""
    for _, rows in plans():
        for row in rows:
            for b in row:
                assert width(b.label) <= ROW_BUDGET // len(row), b.label


def test_time_window_labels_carry_no_emoji():
    """Emoji cost roughly two characters of width in a shared row."""
    rows = card_rows(Window.TODAY, theme="light", subscribed=False)
    assert all(b.label.replace(ACTIVE_MARK, "").isascii() for b in rows[1])


def test_every_view_is_reachable_from_every_card():
    for _, rows in plans():
        assert set(view_buttons(rows)) == set(VIEWS)


def test_exactly_the_active_view_is_marked():
    for on, rows in plans():
        marked = [v for v, b in view_buttons(rows).items() if b.label.startswith(ACTIVE_MARK)]
        assert marked == ([] if on is None else [on])


def test_view_buttons_edit_a_card_but_open_a_new_one_from_an_alert():
    """An alert must stay what it was when it arrived."""
    for on, rows in plans():
        actions = {decode(b.callback).action for b in view_buttons(rows).values()}
        assert actions == ({NEW_CARD} if on is None else {WINDOW})


@pytest.mark.parametrize("on", CARDS)
def test_settings_buttons_name_the_card_they_redraw(on):
    rows = card_rows(on, theme="light", subscribed=False)
    settings = [decode(b.callback) for b in rows[2]]
    assert [(c.action, c.view) for c in settings] == [(THEME, on), (SUBSCRIBE, on)]


@pytest.mark.parametrize("theme, label, target", [
    ("light", "🌙 Dark mode", "dark"), ("dark", "☀️ Light mode", "light"),
])
def test_theme_button_offers_and_sets_the_other_theme(theme, label, target):
    b = card_rows(STATUS, theme=theme, subscribed=False)[2][0]
    assert (b.label, decode(b.callback).arg) == (label, target)


@pytest.mark.parametrize("subscribed, label, target", [
    (False, "🔔 Subscribe", "on"), (True, "🔕 Unsubscribe", "off"),
])
def test_subscribe_button_names_and_sets_the_action(subscribed, label, target):
    b = card_rows(STATUS, theme="light", subscribed=subscribed)[2][1]
    assert (b.label, decode(b.callback).arg) == (label, target)


def test_unknown_theme_falls_back_to_offering_dark():
    assert theme_label("sepia") == theme_label("light")


def test_no_dashboard_url_means_no_dashboard_button():
    """An unset URL (or a group chat, where Telegram refuses web_app buttons)
    must hide the button, not render a dead one."""
    rows = card_rows(STATUS, theme="light", subscribed=False)
    assert not any(b.web_app_url for b in buttons(rows))
    assert [b.label for b in rows[-1]] == ["ℹ️ Info"]


def test_the_dashboard_button_opens_the_url():
    rows = card_rows(STATUS, theme="light", subscribed=False, dashboard_url=DASH)
    assert [b.web_app_url for b in buttons(rows) if b.web_app_url] == [DASH]


def test_button_label_is_stable_for_inactive_views():
    assert button_label(Window.LAST_7D, STATUS) == VIEW_LABELS[Window.LAST_7D]
    assert subscribe_label(True) == "🔕 Unsubscribe"
