import asyncio
import logging
from typing import Optional

from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import MenuButtonWebApp, WebAppInfo

from common.alerts import Alert
from common.db import Database

from config import settings
from consumer import AsyncConsumer
from html_helpers import info_text
import callbacks
from markup import card_markup
from reports import STATUS, ReadingReports, Report, View, Window
from subscriber_cache import RedisSubscriberCache
from subscriptions import Subscriptions

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("reporter_bot")

bot = Bot(token=settings.telegram_token, parse_mode="HTML")
dp = Dispatcher(bot)

# One engine for the process; handlers borrow sessions from it.
db = Database(url=settings.database_url)
subs = Subscriptions(db, RedisSubscriberCache(settings.redis_host, settings.redis_port))
reports = ReadingReports(
    db, settings.tz, settings.thresholds,
    reading_interval_seconds=settings.reading_interval_seconds,
)

NO_READINGS = "No readings yet."
MENU_MOVED = "The buttons now live under each card."


def card_buttons(chat_id: int, view: View, theme: str):
    return card_markup(view, theme=theme, subscribed=subs.is_subscribed(chat_id),
                       dashboard_url=settings.dashboard_url)


def _photo(report: Report) -> types.InputFile:
    report.chart.seek(0)
    return types.InputFile(report.chart, filename=report.chart.name)


def render(view: View, theme: str) -> Optional[Report]:
    """The card for one view; None only for Status before the first reading."""
    if view is STATUS:
        return reports.status_report(theme=theme)
    return reports.for_window(view, theme=theme)


async def send_card(chat_id: int, view: View) -> None:
    """Send a new card: one message carrying the chart, its caption and the buttons.

    A card with nothing to draw (no readings, a quiet sensor, an empty window)
    goes out as text, still with the buttons, so the chat is never left
    without a menu.
    """
    theme = subs.theme(chat_id)
    report = render(view, theme)
    buttons = card_buttons(chat_id, view, theme)

    if report is None or report.is_empty:
        await bot.send_message(
            chat_id, report.text if report else NO_READINGS,
            reply_markup=buttons, disable_web_page_preview=True,
        )
        return

    await bot.send_photo(
        chat_id, photo=_photo(report), caption=report.text or None,
        reply_markup=buttons,
    )


@dp.message_handler(commands=["start", "status"])
async def start_cmd(message: types.Message):
    if message.get_command() == "/start":
        # Also clears the reply keyboard older versions left in the chat.
        await message.answer("Welcome! Everything is on the buttons under the card.",
                             reply_markup=types.ReplyKeyboardRemove())
    await send_card(message.chat.id, STATUS)


@dp.message_handler(commands=["info"])
async def info_cmd(message: types.Message):
    await message.answer(info_text(settings.thresholds), disable_web_page_preview=True)


# ---- the reply keyboard older versions left behind ----
# Chats that never send /start again still have it, so its buttons keep
# working, and the first tap removes it.

LEGACY_VIEWS = {
    "📟 Status": STATUS,
    "📅 Today": Window.TODAY,
    "🕒 Last 12h": Window.LAST_12H,
    "📈 Last 7d": Window.LAST_7D,
    "🗓 Patterns": Window.PATTERNS,
    "Patterns": Window.PATTERNS,
}


@dp.message_handler(lambda m: m.text in LEGACY_VIEWS)
async def legacy_view_handler(message: types.Message):
    await message.answer(MENU_MOVED, reply_markup=types.ReplyKeyboardRemove())
    await send_card(message.chat.id, LEGACY_VIEWS[message.text])


@dp.message_handler(lambda m: m.text in ["🔔 Subscribe", "🔔 Unsubscribe"])
async def legacy_subscribe_handler(message: types.Message):
    subscribed = subs.toggle(message.chat.id)
    text = "🔔 Subscribed to alerts." if subscribed else "🔕 Unsubscribed from alerts."
    await message.answer(f"{text}\n{MENU_MOVED}", reply_markup=types.ReplyKeyboardRemove())


@dp.message_handler(lambda m: m.text in ["ℹ️ Info", "Info"])
async def legacy_info_handler(message: types.Message):
    await message.answer(info_text(settings.thresholds),
                         reply_markup=types.ReplyKeyboardRemove(),
                         disable_web_page_preview=True)


# ---- inline keyboard: re-render the same card in place ----

async def _swap_card(call: types.CallbackQuery, view: View, theme: str,
                     toast: str = "") -> None:
    """Edit the tapped card into `view`, answering the callback exactly once."""
    chat_id = call.message.chat.id
    report = render(view, theme)
    if report is None or report.is_empty:
        # Nothing to draw. The card stays as it is and a pop-up says why.
        await call.answer(report.notice if report else NO_READINGS, show_alert=True)
        return
    await call.answer(toast)

    buttons = card_buttons(chat_id, view, theme)
    if not call.message.photo:
        # A text card cannot be edited into a photo, so the chart is sent fresh.
        await bot.send_photo(chat_id, photo=_photo(report),
                             caption=report.text or None, reply_markup=buttons)
        return

    await bot.edit_message_media(
        chat_id=chat_id,
        message_id=call.message.message_id,
        media=types.InputMediaPhoto(
            _photo(report), caption=report.text or None, parse_mode="HTML"
        ),
        reply_markup=buttons,
    )


def _is(prefix: str):
    return lambda c: bool(c.data) and c.data.startswith(f"{prefix}:")


@dp.callback_query_handler(_is(callbacks.WINDOW))
async def window_callback(call: types.CallbackQuery):
    _, view = callbacks.decode(call.data)
    await _swap_card(call, view, subs.theme(call.message.chat.id))


@dp.callback_query_handler(_is(callbacks.THEME))
async def theme_callback(call: types.CallbackQuery):
    _, view = callbacks.decode(call.data)
    theme = subs.toggle_theme(call.message.chat.id)
    await _swap_card(call, view, theme, toast=f"{theme.capitalize()} charts")


@dp.callback_query_handler(_is(callbacks.SUBSCRIBE))
async def subscribe_callback(call: types.CallbackQuery):
    """Only the buttons change: the card itself does not depend on alerts."""
    _, view = callbacks.decode(call.data)
    chat_id = call.message.chat.id
    subscribed = subs.toggle(chat_id)
    await call.answer("🔔 Subscribed to alerts" if subscribed
                      else "🔕 Unsubscribed from alerts")
    await bot.edit_message_reply_markup(
        chat_id=chat_id, message_id=call.message.message_id,
        reply_markup=card_buttons(chat_id, view, subs.theme(chat_id)),
    )


@dp.callback_query_handler(_is(callbacks.INFO))
async def info_callback(call: types.CallbackQuery):
    """Info is text, not a chart, so it arrives as its own message."""
    await call.answer()
    await call.message.answer(info_text(settings.thresholds),
                              disable_web_page_preview=True)


# =======================
# AMQP consumer integration
# =======================

def alert_buttons(theme: str):
    """An alert is a status card, so it carries the status card's buttons.

    Every recipient is a subscriber, which is what lets the markup be built
    per theme rather than asked of the cache once per chat.
    """
    return card_markup(STATUS, theme=theme, subscribed=True,
                       dashboard_url=settings.dashboard_url)


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
                reply_markup=alert_buttons(theme),
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
            reply_markup=alert_buttons(theme),
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
