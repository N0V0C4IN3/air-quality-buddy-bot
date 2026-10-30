"""What the inline keyboard says, what each button does, and how rows divide.

Kept apart from `markup.py` so the whole plan can be tested without aiogram: a
row is split equally between its buttons and Telegram truncates any label that
overflows its share, so the row plan is the thing that has to be right.

The inline keyboard is the bot's only menu - there is no reply keyboard - so
every card carries every action:

    [ 📟 Status ][ 🗓 Patterns ]
    [ Today ][  12h  ][  7d   ]
    [ 🌙 Dark mode ][ 🔔 Subscribe ]
    [ ℹ️ Info ][ 📊 Dashboard ]       (Dashboard: private chats with a URL)

No row holds more than three buttons; a fourth in the time-window row is
what truncated "● Today" on a narrow phone.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from callbacks import info_action, subscribe_action, theme_action, view_action
from reports import STATUS, View, Window

# No emoji on the three time windows - each costs roughly two characters of
# width, which is what pushed "📅 Today" past its quarter of a four-way row.
VIEW_LABELS = {
    STATUS: "📟 Status",
    Window.TODAY: "Today",
    Window.LAST_12H: "12h",
    Window.LAST_7D: "7d",
    Window.PATTERNS: "🗓 Patterns",
}

# Marks the view currently on screen. A prefix costs less width than wrapping
# the label in separators on both sides.
ACTIVE_MARK = "● "

# Rough budget: on a narrow phone a full-width row fits ~30 characters, so a
# three-way split gives each button about ten and a two-way split fifteen.
ROW_BUDGET = 30

THEME_LABELS = {"light": "🌙 Dark mode", "dark": "☀️ Light mode"}
SUBSCRIBE_LABELS = {False: "🔔 Subscribe", True: "🔕 Unsubscribe"}
INFO_LABEL = "ℹ️ Info"
DASHBOARD_LABEL = "📊 Dashboard"


@dataclass(frozen=True)
class Button:
    label: str
    callback: str = ""
    web_app_url: str = ""


def button_label(view: View, active: Optional[View]) -> str:
    label = VIEW_LABELS[view]
    return f"{ACTIVE_MARK}{label}" if view is active else label


def theme_label(theme: str) -> str:
    """The button offers the *other* theme, because that is what tapping does."""
    return THEME_LABELS.get(theme, THEME_LABELS["light"])


def other_theme(theme: str) -> str:
    return "light" if theme == "dark" else "dark"


def subscribe_label(subscribed: bool) -> str:
    """Names the action, not the state - same rule as the theme button."""
    return SUBSCRIBE_LABELS[bool(subscribed)]


def card_rows(on: Optional[View], *, theme: str, subscribed: bool,
              dashboard_url: str = "") -> list[list[Button]]:
    """Every row under a card showing `on`; None means the card is an alert.

    On an alert no view is marked, and the view buttons open a new card rather
    than editing: the alert has to stay what it was when it arrived.
    """
    def view(v: View) -> Button:
        return Button(button_label(v, on), view_action(v, new_card=on is None))

    time_windows = [w for w in Window if w is not Window.PATTERNS]
    last = [Button(INFO_LABEL, info_action(on))]
    if dashboard_url:
        last.append(Button(DASHBOARD_LABEL, web_app_url=dashboard_url))
    return [
        [view(STATUS), view(Window.PATTERNS)],
        [view(w) for w in time_windows],
        [Button(theme_label(theme), theme_action(on, other_theme(theme))),
         Button(subscribe_label(subscribed), subscribe_action(on, not subscribed))],
        last,
    ]
