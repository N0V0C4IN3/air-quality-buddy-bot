"""The inline keyboard attached to every card, as aiogram objects.

The plan - labels, actions, rows - is `keyboard_layout.card_rows`, which
carries no aiogram import so it can be tested; this only translates it.
"""
from __future__ import annotations

from typing import Optional

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from keyboard_layout import Button, card_rows
from reports import View


def _button(b: Button) -> InlineKeyboardButton:
    if b.web_app_url:
        return InlineKeyboardButton(b.label, web_app=WebAppInfo(url=b.web_app_url))
    return InlineKeyboardButton(b.label, callback_data=b.callback)


def card_markup(on: Optional[View], *, theme: str = "light", subscribed: bool = False,
                dashboard_url: str = "") -> InlineKeyboardMarkup:
    """Buttons under a card showing `on`; None for an alert."""
    kb = InlineKeyboardMarkup()
    for row in card_rows(on, theme=theme, subscribed=subscribed,
                         dashboard_url=dashboard_url):
        kb.row(*[_button(b) for b in row])
    return kb
