"""The callback-data grammar for inline buttons.

Kept apart from `markup.py` because it is data, not UI: Telegram echoes these
strings back from buttons that may be months old, so the grammar has to stay
stable and be testable without aiogram.

    <action>:<slug>[:<arg>]

    w:<view>                   show that view by editing this card
    n:<view>                   show that view as a new card. Alerts carried
                               these until 2026-11, when a tap on an alert
                               started editing it; no longer sent, still decoded
    theme:<card>:light|dark    set the chart theme, redraw this card
    sub:<card>:on|off          set alerts, redraw this card's buttons
    info:<card>                send the info text; the card stays as it is
    x:back                     delete this message (the info text), which
                               leaves the card it was opened from in view
    x:<view>                   delete this message, then send that view as
                               a new card

`<card>` is what the tapped card shows - a view slug, or `alert` - so a
settings tap knows what to redraw. Settings buttons carry the state they set,
not "toggle": a label on an old card is frozen, and a toggle behind it would do
the opposite of what it says once the state had moved on. `theme:<card>` and
`sub:<card>` without an argument are still decoded, as toggles, because
buttons in that shape are already on people's screens.

Telegram caps callback_data at 64 bytes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from reports import View, view_from_slug

WINDOW = "w"
NEW_CARD = "n"
THEME = "theme"
SUBSCRIBE = "sub"
INFO = "info"
DISMISS = "x"
ACTIONS = (WINDOW, NEW_CARD, THEME, SUBSCRIBE, INFO, DISMISS)

# The <card> slug of an alert. Not a view: an alert cannot be re-rendered.
ALERT = "alert"
# The x: slug that only dismisses, opening nothing.
BACK = "back"
THEME_ARGS = ("light", "dark")
SUBSCRIBE_ARGS = {"on": True, "off": False}
MAX_BYTES = 64


class CallbackError(ValueError):
    """The callback data did not match the grammar."""


@dataclass(frozen=True)
class Callback:
    action: str
    # The target view for w/n/x; the view on the tapped card for the rest.
    # None when that card is an alert, or for x:back.
    view: Optional[View]
    # light/dark for theme, on/off for sub; None means a legacy toggle.
    arg: Optional[str] = None


def _encode(*parts: str) -> str:
    data = ":".join(parts)
    if len(data.encode("utf-8")) > MAX_BYTES:
        raise CallbackError(f"callback data too long: {data!r}")
    return data


def _card(on: Optional[View]) -> str:
    return on.slug if on is not None else ALERT


def view_action(view: View) -> str:
    return _encode(WINDOW, view.slug)


def theme_action(on: Optional[View], theme: str) -> str:
    """Set `theme`, then redraw the card showing `on` (None: an alert)."""
    if theme not in THEME_ARGS:
        raise CallbackError(f"unknown theme: {theme!r}")
    return _encode(THEME, _card(on), theme)


def subscribe_action(on: Optional[View], subscribed: bool) -> str:
    return _encode(SUBSCRIBE, _card(on), "on" if subscribed else "off")


def info_action(on: Optional[View]) -> str:
    return _encode(INFO, _card(on))


def dismiss_action(then: Optional[View] = None) -> str:
    """Delete the tapped message, then open `then` as a new card if given."""
    return _encode(DISMISS, then.slug if then is not None else BACK)


def decode(data: str) -> Callback:
    """`w:last_12h` -> Callback("w", Window.LAST_12H)."""
    action, _, rest = (data or "").partition(":")
    slug, _, arg = rest.partition(":")
    if action not in ACTIONS or not slug:
        raise CallbackError(f"unknown callback data: {data!r}")

    # The slugs that name no view: an alert card, and a dismiss that opens nothing.
    no_view = BACK if action == DISMISS else None if action in (WINDOW, NEW_CARD) else ALERT
    if slug == no_view:
        view = None
    else:
        try:
            view = view_from_slug(slug)
        except KeyError as e:
            raise CallbackError(f"unknown view in callback data: {data!r}") from e

    allowed = {THEME: THEME_ARGS, SUBSCRIBE: tuple(SUBSCRIBE_ARGS)}.get(action, ())
    if arg and arg not in allowed:
        raise CallbackError(f"unknown argument in callback data: {data!r}")
    return Callback(action, view, arg or None)
