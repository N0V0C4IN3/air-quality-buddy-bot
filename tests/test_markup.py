"""The keyboards.

`markup.py` is the one reporter-bot module the rest of the suite never reaches,
because it is the only one that imports aiogram — and aiogram is not in
requirements-dev.txt (2.25.1 predates the Python the suite is developed on).
So these skip unless aiogram is installed, and CI covers the same ground for
real by importing the module inside the built reporter-bot image.
"""
import pytest

pytest.importorskip("aiogram", reason="aiogram is a reporter-bot dependency, not a test one")

from markup import main_menu_markup, window_markup  # noqa: E402
from reports import Window  # noqa: E402

DASH = "https://air-quality-pi.tail03af11.ts.net"


def buttons(kb):
    return [b for row in kb.inline_keyboard for b in row]


def test_no_dashboard_url_means_no_dashboard_button():
    """An unset DASHBOARD_URL must hide the button, not render a dead one."""
    labels = [b.text for b in buttons(window_markup(Window.TODAY))]
    assert not any("dashboard" in t.lower() for t in labels)


def test_the_dashboard_button_appears_when_there_is_a_url():
    kb = window_markup(Window.TODAY, dashboard_url=DASH)
    dash = [b for b in buttons(kb) if "dashboard" in b.text.lower()]
    assert len(dash) == 1
    assert dash[0].web_app.url == DASH


def test_the_dashboard_button_gets_its_own_row():
    """The window row is width-budgeted to the pixel; a fourth button re-breaks it."""
    kb = window_markup(Window.TODAY, dashboard_url=DASH)
    last = kb.inline_keyboard[-1]
    assert len(last) == 1
    assert "dashboard" in last[0].text.lower()


def test_the_window_buttons_are_unchanged_by_the_dashboard_url():
    without = [b.text for b in buttons(window_markup(Window.LAST_7D))]
    with_url = [b.text for b in buttons(window_markup(Window.LAST_7D, dashboard_url=DASH))]
    assert with_url[:len(without)] == without


@pytest.mark.parametrize("window", list(Window))
def test_every_window_renders(window):
    for theme in ("light", "dark"):
        kb = window_markup(window, theme=theme, dashboard_url=DASH)
        assert buttons(kb)


@pytest.mark.parametrize("subscribed", [True, False])
def test_the_menu_names_the_action_not_the_state(subscribed):
    labels = [b.text for row in main_menu_markup(subscribed).keyboard for b in row]
    assert ("🔔 Unsubscribe" in labels) is subscribed
    assert ("🔔 Subscribe" in labels) is not subscribed
