import asyncio
import functools
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from typing import Optional

from aiogram import Bot, Dispatcher, executor, types
from aiogram.dispatcher.middlewares import BaseMiddleware
from aiogram.types import MenuButtonWebApp, WebAppInfo
from aiogram.utils.exceptions import MessageNotModified

from common.alerts import Alert
from common.db import Database

from config import settings
from consumer import AsyncConsumer
from html_helpers import info_text
import callbacks
from keyboard_layout import other_theme
from markup import card_markup
from reports import STATUS, ReadingReports, Report, View, Window
from subscriber_cache import RedisSubscriberCache
from subscriptions import Subscriptions

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("reporter_bot")

# Without a timeout aiohttp waits 300 s, and that includes the long poll that
# fetches updates: a connection dropped silently somewhere on the way (the Pi
# is on Wi-Fi) left the bot deaf for five minutes. aiogram adds the 20 s poll
# to this for getUpdates, so a dead poll is abandoned after 35 s.
TELEGRAM_TIMEOUT_SECONDS = 15
# Above these an update is logged as a warning rather than at info.
SLOW_DELIVERY_SECONDS = 5
SLOW_HANDLING_SECONDS = 2

bot = Bot(token=settings.telegram_token, parse_mode="HTML",
          timeout=TELEGRAM_TIMEOUT_SECONDS)
dp = Dispatcher(bot)


class UpdateTiming(BaseMiddleware):
    """One log line per update: how late Telegram delivered it and how long
    the bot took, so a reported delay can be placed on one side or the other.

    Delivery lag is only known for messages: a callback query carries no
    timestamp of its own, only the date of the card it was tapped on.
    """

    async def on_pre_process_update(self, update: types.Update, data: dict):
        data["_started"] = time.monotonic()

    async def on_post_process_update(self, update: types.Update, result, data: dict):
        handled = time.monotonic() - data.get("_started", time.monotonic())
        if update.message:
            kind = f"message {update.message.text or update.message.content_type!r}"
            lag = time.time() - update.message.date.timestamp()
        elif update.callback_query:
            kind, lag = f"button {update.callback_query.data!r}", None
        else:
            kind, lag = "update", None
        slow = handled > SLOW_HANDLING_SECONDS or (lag or 0) > SLOW_DELIVERY_SECONDS
        delivered = f"delivered after {lag:.1f}s, " if lag is not None else ""
        log.log(logging.WARNING if slow else logging.INFO,
                "%s%s: %shandled in %.2fs", "SLOW " if slow else "", kind,
                delivered, handled)


dp.middleware.setup(UpdateTiming())

# One engine for the process; handlers borrow sessions from it.
db = Database(url=settings.database_url)
subs = Subscriptions(db, RedisSubscriberCache(settings.redis_host, settings.redis_port))
reports = ReadingReports(
    db, settings.tz, settings.thresholds,
    reading_interval_seconds=settings.reading_interval_seconds,
)

MENU_MOVED = "The buttons now live under each card."
STALE_BUTTON = "This button is out of date. Send /status for a fresh card."
FAILED = "Something went wrong drawing that. Try again in a moment."

# Charts render off the event loop, so a slow matplotlib pass on the Pi does not
# stall every other chat. One worker: pyplot keeps global state.
_renderer = ThreadPoolExecutor(max_workers=1, thread_name_prefix="render")


async def render(view: View, theme: str) -> Report:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        _renderer, functools.partial(reports.for_view, view, theme=theme))


def is_private(chat_id: int) -> bool:
    """Private chats have the user's (positive) id; groups and channels are negative."""
    return chat_id > 0


def card_buttons(chat_id: int, on: Optional[View], *, theme: str, subscribed: bool):
    # Telegram accepts a web_app button only in a private chat; in a group it
    # rejects the whole message, so there the Dashboard button is left off.
    url = settings.dashboard_url if is_private(chat_id) else ""
    return card_markup(on, theme=theme, subscribed=subscribed, dashboard_url=url)


def _photo(report: Report) -> types.InputFile:
    report.chart.seek(0)
    return types.InputFile(report.chart, filename=report.chart.name)


async def _send(chat_id: int, report: Report, buttons) -> None:
    """A card with nothing to draw goes out as text, still with the buttons,
    so the chat is never left without a menu."""
    if report.is_empty:
        await bot.send_message(chat_id, report.text, reply_markup=buttons,
                               disable_web_page_preview=True)
    else:
        await bot.send_photo(chat_id, photo=_photo(report),
                             caption=report.text or None, reply_markup=buttons)


async def send_card(chat_id: int, view: View) -> None:
    """Send a new card: one message carrying the chart, its caption and the buttons."""
    theme = subs.theme(chat_id)
    report = await render(view, theme)
    await _send(chat_id, report, card_buttons(
        chat_id, view, theme=theme, subscribed=subs.is_subscribed(chat_id)))


@dp.message_handler(commands=["start"])
async def start_cmd(message: types.Message):
    # Also clears the reply keyboard older versions left in the chat.
    await message.answer("Welcome! Everything is on the buttons under the card.",
                         reply_markup=types.ReplyKeyboardRemove())
    await send_card(message.chat.id, STATUS)


@dp.message_handler(commands=["status"])
async def status_cmd(message: types.Message):
    await send_card(message.chat.id, STATUS)


@dp.message_handler(commands=["info"])
async def info_cmd(message: types.Message):
    await message.answer(info_text(settings.thresholds), disable_web_page_preview=True)


# ---- the reply keyboard older versions left behind ----
# Chats that never send /start again still have it, so its buttons keep
# working, and the first tap removes it. Delete once no chat can have it.

LEGACY_LABELS = {
    "📟 Status": STATUS,
    "📅 Today": Window.TODAY,
    "🕒 Last 12h": Window.LAST_12H,
    "📈 Last 7d": Window.LAST_7D,
    "🗓 Patterns": Window.PATTERNS,
    "Patterns": Window.PATTERNS,
    "🔔 Subscribe": True,
    "🔔 Unsubscribe": False,
    "ℹ️ Info": None,
    "Info": None,
}


@dp.message_handler(lambda m: m.text in LEGACY_LABELS)
async def legacy_keyboard_handler(message: types.Message):
    chat_id = message.chat.id
    action = LEGACY_LABELS[message.text]
    remove = types.ReplyKeyboardRemove()

    if action is None:
        await message.answer(info_text(settings.thresholds), reply_markup=remove,
                             disable_web_page_preview=True)
    elif isinstance(action, bool):
        # Set what the label says: it may be older than the current state.
        (subs.subscribe if action else subs.unsubscribe)(chat_id)
        text = "🔔 Subscribed to alerts." if action else "🔕 Unsubscribed from alerts."
        await message.answer(f"{text}\n{MENU_MOVED}", reply_markup=remove)
    else:
        await message.answer(MENU_MOVED, reply_markup=remove)
        await send_card(chat_id, action)


# ---- inline keyboard ----

async def _redraw_buttons(call: types.CallbackQuery, on: Optional[View], *,
                          theme: str, subscribed: bool) -> None:
    # An old label can already match the new state, and Telegram rejects an
    # edit that changes nothing.
    with suppress(MessageNotModified):
        await bot.edit_message_reply_markup(
            chat_id=call.message.chat.id, message_id=call.message.message_id,
            reply_markup=card_buttons(call.message.chat.id, on,
                                      theme=theme, subscribed=subscribed),
        )


async def _show(call: types.CallbackQuery, view: View, theme: str,
                toast: str = "") -> Optional[Report]:
    """Edit the tapped card into `view`, answering with `toast`.

    When there is nothing to draw, returns that empty report with the callback
    still unanswered: the caller decides what to say.
    """
    chat_id = call.message.chat.id
    report = await render(view, theme)
    if report.is_empty:
        return report
    await call.answer(toast)

    buttons = card_buttons(chat_id, view, theme=theme,
                           subscribed=subs.is_subscribed(chat_id))
    if not call.message.photo:
        # A text card cannot be edited into a photo, so the chart is sent fresh.
        await _send(chat_id, report, buttons)
        return None
    # Status tapped twice within one reading draws the identical card, and
    # Telegram refuses an edit that changes nothing: the card is current.
    with suppress(MessageNotModified):
        await bot.edit_message_media(
            chat_id=chat_id, message_id=call.message.message_id,
            media=types.InputMediaPhoto(_photo(report), caption=report.text or None,
                                        parse_mode="HTML"),
            reply_markup=buttons,
        )
    return None


async def _on_view(call: types.CallbackQuery, cb: callbacks.Callback) -> None:
    empty = await _show(call, cb.view, subs.theme(call.message.chat.id))
    if empty:
        # Nothing to draw. The card stays as it is and a pop-up says why.
        await call.answer(empty.notice, show_alert=True)


async def _on_new_card(call: types.CallbackQuery, cb: callbacks.Callback) -> None:
    await call.answer()
    await send_card(call.message.chat.id, cb.view)


async def _on_theme(call: types.CallbackQuery, cb: callbacks.Callback) -> None:
    chat_id = call.message.chat.id
    theme = subs.set_theme(chat_id, cb.arg or other_theme(subs.theme(chat_id)))
    toast = f"{theme.capitalize()} charts"
    # An alert is never re-rendered, and a text card has nothing themed to
    # redraw: either way only the buttons change.
    if cb.view is not None and not await _show(call, cb.view, theme, toast):
        return
    await call.answer(toast)
    await _redraw_buttons(call, cb.view, theme=theme,
                          subscribed=subs.is_subscribed(chat_id))


async def _on_subscribe(call: types.CallbackQuery, cb: callbacks.Callback) -> None:
    """Only the buttons change: the card itself does not depend on alerts."""
    chat_id = call.message.chat.id
    subscribed = (callbacks.SUBSCRIBE_ARGS[cb.arg] if cb.arg
                  else not subs.is_subscribed(chat_id))
    (subs.subscribe if subscribed else subs.unsubscribe)(chat_id)
    await call.answer("🔔 Subscribed to alerts" if subscribed
                      else "🔕 Unsubscribed from alerts")
    await _redraw_buttons(call, cb.view, theme=subs.theme(chat_id),
                          subscribed=subscribed)


async def _on_info(call: types.CallbackQuery, cb: callbacks.Callback) -> None:
    """Info is text, not a chart, so it arrives as its own message."""
    await call.answer()
    await call.message.answer(info_text(settings.thresholds),
                              disable_web_page_preview=True)


BUTTON_HANDLERS = {
    callbacks.WINDOW: _on_view,
    callbacks.NEW_CARD: _on_new_card,
    callbacks.THEME: _on_theme,
    callbacks.SUBSCRIBE: _on_subscribe,
    callbacks.INFO: _on_info,
}


@dp.callback_query_handler()
async def button_handler(call: types.CallbackQuery):
    """Every inline tap. Each path answers the callback exactly once, so the
    user's spinner always stops, even for a button this version cannot read."""
    try:
        cb = callbacks.decode(call.data)
    except callbacks.CallbackError:
        log.info("Out-of-date button: %r", call.data)
        await call.answer(STALE_BUTTON, show_alert=True)
        return
    try:
        await BUTTON_HANDLERS[cb.action](call, cb)
    except Exception:
        log.exception("Button %r failed", call.data)
        # Fails harmlessly if the handler had already answered.
        with suppress(Exception):
            await call.answer(FAILED, show_alert=True)


# =======================
# AMQP consumer integration
# =======================

def alert_buttons(chat_id: int, theme: str):
    """The card's buttons, in their alert form: no view is marked, and a view
    tap opens a new card, so the alert stays what it was when it arrived.

    Every recipient is a subscriber, so the state is known without asking the
    cache once per chat.
    """
    return card_buttons(chat_id, None, theme=theme, subscribed=True)


async def _on_alert(alert: Alert) -> None:
    """Fan one decoded alert out to every subscriber."""
    if not settings.enable_alerts:
        log.debug("ENABLE_ALERTS is off; dropping %s alert", alert.level.value)
        return

    subscribers = subs.all()
    log.info("Alert level=%s pm25=%s pm10=%s -> fanout to %d subscriber(s)",
             alert.level.value, alert.pm25, alert.pm10, len(subscribers))

    if not subscribers:
        log.warning("No subscribers; message will not be delivered")
        return

    # One render and one upload per theme, however many subscribers there are:
    # Telegram hands back a file_id for an uploaded photo, and re-sending that
    # id costs no bytes. Two themes means at most two of each.
    uploaded: dict[str, str] = {}

    # One query for every subscriber's theme, not one per subscriber.
    theme_by_chat = subs.themes(subscribers)

    # One render per distinct theme, from one look at the recent readings.
    try:
        cards: dict[str, Report] = reports.alert_reports(
            alert.pm25, alert.pm10, alert.observed_at,
            themes=set(theme_by_chat.values()),
        )
    except Exception:
        # An alert that cannot be drawn must still arrive, as text.
        log.exception("Could not render the alert card")
        cards = {}

    for chat_id in subscribers:
        theme = theme_by_chat.get(chat_id, "light")
        card = cards.get(theme)
        if card is None:
            await _deliver_alert_as_text(chat_id, alert, theme)
            continue
        try:
            sent = await bot.send_photo(
                chat_id=chat_id,
                photo=uploaded.get(theme) or _photo(card),
                caption=card.text or None,
                reply_markup=alert_buttons(chat_id, theme),
            )
            uploaded.setdefault(theme, sent.photo[-1].file_id)
            log.debug("Delivered alert to chat_id=%s", chat_id)
        except Exception as e:
            # ignore per-chat errors to avoid blocking the rest
            log.warning("Failed to deliver to chat_id=%s: %s", chat_id, e)
            await _deliver_alert_as_text(chat_id, alert, theme)


async def _deliver_alert_as_text(chat_id: int, alert: Alert, theme: str) -> None:
    """Last resort: an alert that cannot be drawn must still arrive."""
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=reports.alert_text(alert.pm25, alert.pm10, alert.observed_at),
            reply_markup=alert_buttons(chat_id, theme),
            disable_web_page_preview=True,
        )
        log.info("Delivered alert to chat_id=%s as text", chat_id)
    except Exception as e:
        log.warning("Text fallback failed for chat_id=%s: %s", chat_id, e)


def _done(t: asyncio.Task):
    try:
        t.result()
    except asyncio.CancelledError:
        pass
    except Exception:
        log.exception("AMQP consumer task crashed")


async def _install_menu_button() -> None:
    """Point the chat menu button at the dashboard.

    Set once at startup and it applies to every chat, so the Mini App is one tap
    away without a message in the way. Without a URL configured the default
    commands button stays.
    """
    if not settings.dashboard_url:
        return
    try:
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text="Dashboard", web_app=WebAppInfo(url=settings.dashboard_url)
            )
        )
        log.info("Menu button points at %s", settings.dashboard_url)
    except Exception:
        # A bad URL must not stop the bot answering messages.
        log.exception("Could not set the dashboard menu button")


async def _install_commands() -> None:
    """With no reply keyboard, /status is how a fresh card is summoned."""
    try:
        await bot.set_my_commands([
            types.BotCommand("status", "Latest reading, with all the buttons"),
            types.BotCommand("info", "What the numbers and charts mean"),
        ])
    except Exception:
        log.exception("Could not set the bot commands")


async def on_startup(_):
    """aiogram startup hook: warm the subscriber cache, start the AMQP consumer."""
    count = subs.preload()
    log.info("Preloaded %d subscriber(s) into the cache", count)

    consumer = AsyncConsumer.from_settings(settings)
    log.info("Starting AMQP consumer exchange=%s queue=%s keys=%s",
             settings.exchange, settings.queue, settings.routing_keys)

    task = asyncio.create_task(consumer.start(_on_alert), name="amqp-consumer")
    task.add_done_callback(_done)

    dp["amqp_consumer"] = consumer
    dp["amqp_task"] = task
    await _install_menu_button()
    await _install_commands()

    log.info("[startup] AMQP consumer task created")


async def on_shutdown(_):
    """aiogram shutdown hook: cancel the AMQP consumer task."""
    task = dp.get("amqp_task")
    if task:
        log.info("Cancelling AMQP consumer task...")
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            log.debug("AMQP consumer task cancelled cleanly")

    log.info("[shutdown] AMQP consumer stopped")


if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup, on_shutdown=on_shutdown)
