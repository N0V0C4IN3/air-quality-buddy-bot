"""What the inline keyboard says and how its rows are divided.

Kept apart from `markup.py` so the layout can be tested without aiogram: a row
is split equally between its buttons and Telegram truncates any label that
overflows its share, so the row plan is the thing that has to be right.
"""
from __future__ import annotations

from reports import Window

# No emoji on the three time windows — each costs roughly two characters of
# width, which is what pushed "📅 Today" past its quarter of a four-way row.
WINDOW_LABELS = {
    Window.TODAY: "Today",
    Window.LAST_12H: "12h",
    Window.LAST_7D: "7d",
    Window.PATTERNS: "🗓 Patterns",
}

# Marks the window currently on screen. A prefix costs less width than wrapping
# the label in separators on both sides.
ACTIVE_MARK = "● "

# Rough budget: on a narrow phone a full-width row fits ~30 characters, so a
# three-way split gives each button about ten.
MAX_LABEL_PER_THIRD = 10

THEME_LABELS = {"light": "🌙 Dark charts", "dark": "☀️ Light charts"}


def button_label(window: Window, active: Window) -> str:
    label = WINDOW_LABELS[window]
    return f"{ACTIVE_MARK}{label}" if window is active else label


def theme_label(theme: str) -> str:
    """The button offers the *other* theme, because that is what tapping does."""
    return THEME_LABELS.get(theme, THEME_LABELS["light"])


def window_rows(active: Window) -> list[list[tuple[Window, str]]]:
    """The row plan: three time windows abreast, then Patterns on its own row."""
    time_windows = [w for w in Window if w is not Window.PATTERNS]
    return [
        [(w, button_label(w, active)) for w in time_windows],
        [(Window.PATTERNS, button_label(Window.PATTERNS, active))],
    ]
