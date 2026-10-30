"""What the inline keyboard says and how its rows are divided.

Kept apart from `markup.py` so the layout can be tested without aiogram: a row
is split equally between its buttons and Telegram truncates any label that
overflows its share, so the row plan is the thing that has to be right.

The inline keyboard is the bot's only menu - there is no reply keyboard - so
every card carries every action:

    [ 📟 Status ][ 🗓 Patterns ]
    [ Today ][  12h  ][  7d   ]
    [ 🌙 Dark mode ][ 🔔 Subscribe ]
    [ ℹ️ Info ][ 📊 Dashboard ]       (Dashboard only with a URL)

No row holds more than three buttons; a fourth in the time-window row is
what truncated "● Today" on a narrow phone.
"""
from __future__ import annotations

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
MAX_LABEL_PER_THIRD = 10
MAX_LABEL_PER_HALF = 15

THEME_LABELS = {"light": "🌙 Dark mode", "dark": "☀️ Light mode"}
SUBSCRIBE_LABELS = {False: "🔔 Subscribe", True: "🔕 Unsubscribe"}
INFO_LABEL = "ℹ️ Info"
DASHBOARD_LABEL = "📊 Dashboard"


def button_label(view: View, active: View) -> str:
    label = VIEW_LABELS[view]
    return f"{ACTIVE_MARK}{label}" if view is active else label


def theme_label(theme: str) -> str:
    """The button offers the *other* theme, because that is what tapping does."""
    return THEME_LABELS.get(theme, THEME_LABELS["light"])


def subscribe_label(subscribed: bool) -> str:
    """Names the action, not the state - same rule as the theme button."""
    return SUBSCRIBE_LABELS[bool(subscribed)]


def view_rows(active: View) -> list[list[tuple[View, str]]]:
    """The view rows: Status and Patterns abreast, then the three time windows."""
    time_windows = [w for w in Window if w is not Window.PATTERNS]
    return [
        [(v, button_label(v, active)) for v in (STATUS, Window.PATTERNS)],
        [(w, button_label(w, active)) for w in time_windows],
    ]
