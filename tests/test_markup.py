"""The keyboard.

`markup.py` is the one reporter-bot module the rest of the suite never reaches,
because it is the only one that imports aiogram — and aiogram is not in
requirements-dev.txt (2.25.1 predates the Python the suite is developed on).
So these skip unless aiogram is installed, and CI covers the same ground for
real by importing the module inside the built reporter-bot image.
"""
import pytest

pytest.importorskip("aiogram", reason="aiogram is a reporter-bot dependency, not a test one")

from callbacks import decode, INFO, SUBSCRIBE, THEME  # noqa: E402
from markup import card_markup  # noqa: E402
from reports import STATUS, VIEWS, Window  # noqa: E402

DASH = "https://air-quality-pi.tail03af11.ts.net"


def buttons(kb):
    return [b for row in kb.inline_keyboard for b in row]


def test_no_dashboard_url_means_no_dashboard_button():
    """An unset DASHBOARD_URL must hide the button, not render a dead one."""
    labels = [b.text for b in buttons(card_markup(Window.TODAY))]
    assert not any("dashboard" in t.lower() for t in labels)


def test_the_dashboard_button_appears_when_there_is_a_url():
    kb = card_markup(Window.TODAY, dashboard_url=DASH)
    dash = [b for b in buttons(kb) if "dashboard" in b.text.lower()]
    assert len(dash) == 1
    assert dash[0].web_app.url == DASH


def test_the_dashboard_button_shares_the_last_row_with_info():
    last = card_markup(Window.TODAY, dashboard_url=DASH).inline_keyboard[-1]
    assert decode(last[0].callback_data)[0] == INFO
    assert "dashboard" in last[1].text.lower()


def test_no_row_holds_more_than_three_buttons():
    for view in VIEWS:
        for row in card_markup(view, dashboard_url=DASH).inline_keyboard:
            assert len(row) <= 3


def test_the_view_buttons_are_unchanged_by_the_dashboard_url():
    without = [b.text for b in buttons(card_markup(Window.LAST_7D))]
    with_url = [b.text for b in buttons(card_markup(Window.LAST_7D, dashboard_url=DASH))]
    assert with_url[:len(without)] == without


@pytest.mark.parametrize("view", VIEWS)
def test_every_view_renders(view):
    for theme in ("light", "dark"):
        kb = card_markup(view, theme=theme, dashboard_url=DASH)
        assert buttons(kb)


def test_settings_buttons_redraw_the_view_on_screen():
    """Theme and subscribe carry the active view so the same card comes back."""
    kb = card_markup(STATUS)
    actions = {decode(b.callback_data)[0]: decode(b.callback_data)[1]
               for b in buttons(kb) if b.callback_data}
    assert actions[THEME] is STATUS
    assert actions[SUBSCRIBE] is STATUS


@pytest.mark.parametrize("subscribed", [True, False])
def test_the_subscribe_button_names_the_action_not_the_state(subscribed):
    labels = [b.text for b in buttons(card_markup(STATUS, subscribed=subscribed))]
    assert ("🔕 Unsubscribe" in labels) is subscribed
    assert ("🔔 Subscribe" in labels) is not subscribed
