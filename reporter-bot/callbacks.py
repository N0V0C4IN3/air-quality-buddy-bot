"""The callback-data grammar for inline buttons.

Kept apart from `markup.py` because it is data, not UI: Telegram echoes these
strings back from buttons that may be months old, so the grammar has to stay
stable and be testable without aiogram.

Every action is `<prefix>:<view slug>`. The view is the one on screen when the
button was drawn, so an action that changes something else (theme, alerts) can
redraw the same card afterwards.

Telegram caps callback_data at 64 bytes.
"""
from __future__ import annotations

from reports import View, view_from_slug

WINDOW = "w"            # show this view (the name predates Status; it is wire format)
THEME = "theme"         # toggle the chart theme, redraw this view
SUBSCRIBE = "sub"       # toggle alerts, redraw this view's buttons
INFO = "info"           # send the info text; the card stays as it is
PREFIXES = (WINDOW, THEME, SUBSCRIBE, INFO)
MAX_BYTES = 64


class CallbackError(ValueError):
    """The callback data did not match the grammar."""


def encode(prefix: str, view: View) -> str:
    data = f"{prefix}:{view.slug}"
    if len(data.encode("utf-8")) > MAX_BYTES:
        raise CallbackError(f"callback data too long: {data!r}")
    return data


def window_action(view: View) -> str:
    return encode(WINDOW, view)


def theme_action(view: View) -> str:
    """Toggling the theme re-renders whichever view is on screen."""
    return encode(THEME, view)


def subscribe_action(view: View) -> str:
    return encode(SUBSCRIBE, view)


def info_action(view: View) -> str:
    return encode(INFO, view)


def decode(data: str) -> tuple[str, View]:
    """`w:last_12h` -> ("w", Window.LAST_12H); `w:status` -> ("w", STATUS)."""
    prefix, separator, slug = (data or "").partition(":")
    if not separator or prefix not in PREFIXES:
        raise CallbackError(f"unknown callback data: {data!r}")
    try:
        return prefix, view_from_slug(slug)
    except KeyError as e:
        raise CallbackError(f"unknown view in callback data: {data!r}") from e
