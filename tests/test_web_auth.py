"""Mini App signatures, and who each auth mode lets in."""
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest

from auth import AccessGuard, AuthError, AuthMode, verify_init_data

TOKEN = "123456:test-bot-token"


def sign(fields: dict, *, token: str = TOKEN) -> str:
    """Build an initData string the way Telegram does."""
    check = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode({**fields, "hash": digest})


def init_data(chat_id=4242, auth_date=None, **extra) -> str:
    return sign({
        "auth_date": str(int(auth_date if auth_date is not None else time.time())),
        "user": json.dumps({"id": chat_id, "username": "kirill",
                            "first_name": "Kirill"}, separators=(",", ":")),
        **extra,
    })


def test_a_valid_payload_identifies_the_chat():
    who = verify_init_data(init_data(), bot_token=TOKEN)
    assert who.chat_id == "4242"
    assert who.username == "kirill"
    assert who.is_telegram


def test_a_tampered_field_invalidates_the_signature():
    raw = init_data()
    tampered = raw.replace("auth_date=", "auth_date=1") if "auth_date=" in raw else raw
    with pytest.raises(AuthError):
        verify_init_data(tampered, bot_token=TOKEN)


def test_another_bots_token_does_not_verify():
    with pytest.raises(AuthError):
        verify_init_data(init_data(), bot_token="999:someone-else")


@pytest.mark.parametrize("raw", ["", "user=%7B%7D&auth_date=1"])
def test_missing_pieces_are_refused(raw):
    with pytest.raises(AuthError):
        verify_init_data(raw, bot_token=TOKEN)


def test_stale_payloads_expire():
    """A copied initData string must not be a permanent password."""
    old = init_data(auth_date=time.time() - 90_000)
    with pytest.raises(AuthError):
        verify_init_data(old, bot_token=TOKEN, max_age_seconds=86400)


def test_a_group_chat_payload_uses_the_chat_id():
    raw = sign({
        "auth_date": str(int(time.time())),
        "chat": json.dumps({"id": -100, "title": "Flat"}, separators=(",", ":")),
    })
    assert verify_init_data(raw, bot_token=TOKEN).chat_id == "-100"


# ---------- modes ----------

def test_telegram_mode_refuses_a_plain_browser():
    guard = AccessGuard(AuthMode.TELEGRAM, bot_token=TOKEN)
    with pytest.raises(AuthError):
        guard.check(init_data=None, token="anything")


def test_token_mode_accepts_the_link_secret_and_rejects_a_wrong_one():
    guard = AccessGuard(AuthMode.TOKEN, bot_token=TOKEN, access_token="s3cret")
    assert guard.check(init_data=None, token="s3cret").chat_id is None
    with pytest.raises(AuthError):
        guard.check(init_data=None, token="wrong")


def test_token_mode_still_identifies_a_telegram_visitor():
    """The same deployment is reached both ways; the chat id must survive."""
    guard = AccessGuard(AuthMode.TOKEN, bot_token=TOKEN, access_token="s3cret")
    assert guard.check(init_data=init_data(), token=None).chat_id == "4242"


def test_public_mode_lets_anyone_in_but_still_reads_a_signature():
    guard = AccessGuard(AuthMode.PUBLIC, bot_token=TOKEN)
    assert guard.check(init_data=None, token=None).chat_id is None
    assert guard.check(init_data=init_data(), token=None).chat_id == "4242"


def test_public_mode_ignores_a_bad_signature_rather_than_failing():
    guard = AccessGuard(AuthMode.PUBLIC, bot_token=TOKEN)
    assert guard.check(init_data="hash=nope&auth_date=1", token=None).chat_id is None


def test_a_mode_without_its_secret_is_a_configuration_error():
    with pytest.raises(ValueError):
        AccessGuard(AuthMode.TELEGRAM, bot_token="")
    with pytest.raises(ValueError):
        AccessGuard(AuthMode.TOKEN, access_token="")
