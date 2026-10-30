"""The keyboard as aiogram objects.

`markup.py` is the one reporter-bot module the rest of the suite never reaches,
because it is the only one that imports aiogram — and aiogram is not in
requirements-dev.txt (2.25.1 predates the Python the suite is developed on).
So these skip unless aiogram is installed, and CI covers the same ground for
real by importing the module inside the built reporter-bot image. The plan
itself is tested without aiogram in test_keyboard_layout.
"""
import pytest

pytest.importorskip("aiogram", reason="aiogram is a reporter-bot dependency, not a test one")

from keyboard_layout import card_rows  # noqa: E402
from markup import card_markup  # noqa: E402
from reports import VIEWS  # noqa: E402

DASH = "https://air-quality-pi.tail03af11.ts.net"


@pytest.mark.parametrize("on", [*VIEWS, None])
def test_the_markup_is_the_plan(on):
    for theme in ("light", "dark"):
        kb = card_markup(on, theme=theme, subscribed=True, dashboard_url=DASH)
        plan = card_rows(on, theme=theme, subscribed=True, dashboard_url=DASH)
        assert [[b.text for b in row] for row in kb.inline_keyboard] == \
            [[b.label for b in row] for row in plan]


def test_callback_buttons_carry_their_data_and_the_dashboard_a_web_app():
    kb = card_markup(None, dashboard_url=DASH)
    for row in kb.inline_keyboard:
        for b in row:
            if "Dashboard" in b.text:
                assert b.web_app.url == DASH and not b.callback_data
            else:
                assert b.callback_data and not b.web_app
