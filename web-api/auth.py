"""Who is allowed to read the dashboard.

Three modes, because the same page is reached two ways. A Mini App opened from
inside Telegram arrives with `initData` - a signed payload the bot token can
verify - while a plain link pasted in a browser arrives with nothing at all.

Pure module: no FastAPI import, so the rules are testable without a server.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional
from urllib.parse import parse_qsl


class AuthMode(str, Enum):
    TELEGRAM = "telegram"   # signed Mini App initData only
    TOKEN = "token"         # a shared secret in the URL also gets in
    PUBLIC = "public"       # no check at all


class AuthError(Exception):
    """The caller may not read this dashboard."""


@dataclass(frozen=True)
class Viewer:
    """Who is looking. `chat_id` is None for a token or public visitor."""
    chat_id: Optional[str] = None
    username: Optional[str] = None
    first_name: Optional[str] = None

    @property
    def is_telegram(self) -> bool:
        return self.chat_id is not None


# Telegram signs initData with a key derived from the bot token; the constant
# is fixed by the protocol.
_WEBAPP_KEY = b"WebAppData"


def verify_init_data(
    init_data: str,
    *,
    bot_token: str,
    max_age_seconds: int = 86400,
    now: Optional[float] = None,
) -> Viewer:
    """Validate a Telegram Mini App `initData` string and say who sent it.

    Raises `AuthError` on a bad signature, a missing hash, or a payload old
    enough to have been copied out of someone's browser and replayed.
    """
    if not init_data:
        raise AuthError("missing initData")

    pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=False)
    fields = dict(pairs)
    received = fields.pop("hash", None)
    if not received:
        raise AuthError("initData carries no hash")

    check_string = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hmac.new(_WEBAPP_KEY, bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received):
        raise AuthError("initData signature does not match")

    auth_date = fields.get("auth_date")
    if not auth_date or not auth_date.isdigit():
        raise AuthError("initData carries no auth_date")
    age = (now if now is not None else time.time()) - int(auth_date)
    if age > max_age_seconds:
        raise AuthError("initData has expired")

    return Viewer(**_identity(fields))


def _identity(fields: dict) -> dict:
    """Pull the chat identity out of the verified payload.

    A Mini App launched from a private chat carries `user`; one launched from a
    group carries `chat`. Either way the bot already knows that id, which is
    what makes the stored per-chat theme reusable here.
    """
    try:
        user = json.loads(fields.get("user") or "{}")
    except json.JSONDecodeError:
        user = {}
    try:
        chat = json.loads(fields.get("chat") or "{}")
    except json.JSONDecodeError:
        chat = {}

    chat_id = user.get("id") or chat.get("id")
    return {
        "chat_id": str(chat_id) if chat_id is not None else None,
        "username": user.get("username") or chat.get("username"),
        "first_name": user.get("first_name") or chat.get("title"),
    }


class AccessGuard:
    """The one place that decides whether a request may see readings."""

    def __init__(
        self,
        mode: AuthMode,
        *,
        bot_token: str = "",
        access_token: str = "",
        max_age_seconds: int = 86400,
    ) -> None:
        if mode is AuthMode.TELEGRAM and not bot_token:
            raise ValueError("telegram auth needs the bot token to verify signatures")
        if mode is AuthMode.TOKEN and not access_token:
            raise ValueError("token auth needs WEB_ACCESS_TOKEN to be set")
        self.mode = mode
        self._bot_token = bot_token
        self._access_token = access_token
        self._max_age = max_age_seconds

    def check(self, *, init_data: Optional[str], token: Optional[str]) -> Viewer:
        """Return the viewer, or raise `AuthError`.

        `initData` is tried first in every mode that accepts it, so a Mini App
        visitor is identified even when the deployment would have let anyone in.
        """
        if self.mode is AuthMode.PUBLIC:
            return self._maybe_telegram(init_data) or Viewer()

        if init_data:
            return verify_init_data(
                init_data, bot_token=self._bot_token, max_age_seconds=self._max_age
            )

        if self.mode is AuthMode.TOKEN:
            if token and hmac.compare_digest(token, self._access_token):
                return Viewer()
            raise AuthError("missing or wrong access token")

        raise AuthError("this dashboard opens from inside Telegram")

    def _maybe_telegram(self, init_data: Optional[str]) -> Optional[Viewer]:
        if not (init_data and self._bot_token):
            return None
        try:
            return verify_init_data(
                init_data, bot_token=self._bot_token, max_age_seconds=self._max_age
            )
        except AuthError:
            return None
