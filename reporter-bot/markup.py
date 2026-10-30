"""The inline keyboard attached to every card.

It is the bot's whole menu: switching views edits the card in place instead of
appending another message, and the settings live beside the views. There is no
reply keyboard; bot.py clears the one older versions left at the bottom of
existing chats.

What the buttons say and how the rows divide lives in `keyboard_layout`, which
carries no aiogram import so it can be tested.
"""
from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo,
)

from callbacks import info_action, subscribe_action, theme_action, window_action
from keyboard_layout import (
    DASHBOARD_LABEL, INFO_LABEL, subscribe_label, theme_label, view_rows,
)
from reports import View


def card_markup(active: View, *, theme: str = "light", subscribed: bool = False,
                dashboard_url: str = "") -> InlineKeyboardMarkup:
    """Buttons under a card. The active view is marked, not hidden.

    The dashboard button shares the last row with Info when there is a URL to
    point at; without one Info has the row to itself.
    """
    kb = InlineKeyboardMarkup()
    for row in view_rows(active):
        kb.row(*[
            InlineKeyboardButton(label, callback_data=window_action(view))
            for view, label in row
        ])
    kb.row(
        InlineKeyboardButton(theme_label(theme), callback_data=theme_action(active)),
        InlineKeyboardButton(subscribe_label(subscribed),
                             callback_data=subscribe_action(active)),
    )
    last = [InlineKeyboardButton(INFO_LABEL, callback_data=info_action(active))]
    if dashboard_url:
        last.append(InlineKeyboardButton(DASHBOARD_LABEL,
                                         web_app=WebAppInfo(url=dashboard_url)))
    kb.row(*last)
    return kb
