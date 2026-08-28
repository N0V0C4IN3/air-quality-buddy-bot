"""The callback-data grammar for inline buttons.

Kept apart from `markup.py` because it is data, not UI: Telegram echoes these
strings back from buttons that may be months old, so the grammar has to stay
stable and be testable without aiogram.

Telegram caps callback_data at 64 bytes.
"""
from __future__ import annotations

from reports import Window

WINDOW = "w"
THEME = "theme"
MAX_BYTES = 64


class CallbackError(ValueError):
    """The callback data did not match the grammar."""


def encode(prefix: str, window: Window) -> str:
    data = f"{prefix}:{window.slug}"
    if len(data.encode("utf-8")) > MAX_BYTES:
        raise CallbackError(f"callback data too long: {data!r}")
    return data


def window_action(window: Window) -> str:
    return encode(WINDOW, window)


def theme_action(window: Window) -> str:
    """Toggling the theme re-renders whichever window is on screen."""
    return encode(THEME, window)


def decode(data: str) -> tuple[str, Window]:
    """`w:last_12h` -> ("w", Window.LAST_12H)."""
    prefix, separator, slug = (data or "").partition(":")
    if not separator or prefix not in (WINDOW, THEME):
        raise CallbackError(f"unknown callback data: {data!r}")
    try:
        return prefix, Window.from_slug(slug)
    except KeyError as e:
        raise CallbackError(f"unknown window in callback data: {data!r}") from e
