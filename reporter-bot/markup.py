"""Keyboards.

Two kinds: the persistent reply keyboard at the bottom of the chat, and the
inline keyboard attached to a chart card so switching windows edits that card in
place instead of appending another pair of messages.
"""
from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup,
)

from callbacks import theme_action, window_action
from reports import Window

WINDOW_LABELS = {
    Window.TODAY: "📅 Today",
    Window.LAST_12H: "🕒 12h",
    Window.LAST_7D: "📈 7d",
    Window.PATTERNS: "🗓 Patterns",
}


def main_menu_markup(subscribed: bool) -> ReplyKeyboardMarkup:
    sub_text = "🔔 Unsubscribe" if subscribed else "🔔 Subscribe"
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(
        KeyboardButton("📟 Status"),
    )
    kb.row(
        KeyboardButton("📅 Today"),
        KeyboardButton("🕒 Last 12h"),
        KeyboardButton("📈 Last 7d"),
    )
    kb.add(KeyboardButton("🗓 Patterns"))
    kb.add(KeyboardButton(sub_text))
    kb.add(KeyboardButton("ℹ️ Info"))
    return kb


def window_markup(active: Window, *, theme: str = "light") -> InlineKeyboardMarkup:
    """Buttons under a chart card. The active window is marked, not hidden."""
    kb = InlineKeyboardMarkup(row_width=4)
    kb.row(*[
        InlineKeyboardButton(
            f"· {label} ·" if window is active else label,
            callback_data=window_action(window),
        )
        for window, label in WINDOW_LABELS.items()
    ])
    kb.add(
        InlineKeyboardButton(
            "🌙 Dark charts" if theme == "light" else "☀️ Light charts",
            callback_data=theme_action(active),
        )
    )
    return kb
