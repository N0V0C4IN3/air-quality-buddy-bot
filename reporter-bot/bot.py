import asyncio
import logging

from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import MenuButtonWebApp, WebAppInfo

from common.alerts import Alert
from common.db import Database

from config import settings
from consumer import AsyncConsumer
from html_helpers import info_text
import callbacks
from markup import main_menu_markup, window_markup
from reports import ReadingReports, Report, Window
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


def menu(chat_id: int):
    return main_menu_markup(subs.is_subscribed(chat_id))


def chart_buttons(window: Window, theme: str):
    return window_markup(window, theme=theme,
                         dashboard_url=settings.dashboard_url)


def _photo(report: Report) -> types.InputFile:
    report.chart.seek(0)
    return types.InputFile(report.chart, filename=report.chart.name)


async def send_window(message: types.Message, window: Window) -> None:
    """Send a chart card: one message carrying the chart, its stats and the buttons."""
    theme = subs.theme(message.chat.id)
    report = reports.for_window(window, theme=theme)

    if report.is_empty:
        await message.answer(report.text, reply_markup=menu(message.chat.id))
        return

    await message.answer_photo(
        photo=_photo(report),
        caption=report.text,
        reply_markup=chart_buttons(window, theme),
    )


# start shows menu
@dp.message_handler(commands=["start"])
async def start_cmd(message: types.Message):
    await message.answer("Welcome! Use the buttons below:", reply_markup=menu(message.chat.id))


# 📟 Status
@dp.message_handler(lambda m: m.text == "📟 Status")
async def status_handler(message: types.Message):
    report = reports.status_report(theme=subs.theme(message.chat.id))
    if report is None:
        await message.answer("No readings yet.", reply_markup=menu(message.chat.id))
        return
    if report.is_empty:                      # sensor quiet — nothing to draw
        await message.answer(
            report.text, reply_markup=menu(message.chat.id),
            disable_web_page_preview=True,
        )
        return
    await message.answer_photo(
        photo=_photo(report),
        caption=report.text or None,
        reply_markup=menu(message.chat.id),
    )


# 📅 Today
@dp.message_handler(lambda m: m.text == "📅 Today")
async def today_handler(message: types.Message):
    await send_window(message, Window.TODAY)


# 🕒 Last 12h
@dp.message_handler(lambda m: m.text == "🕒 Last 12h")
async def last12_handler(message: types.Message):
    await send_window(message, Window.LAST_12H)


# 📈 Last 7d
@dp.message_handler(lambda m: m.text == "📈 Last 7d")
async def last7_handler(message: types.Message):
    await send_window(message, Window.LAST_7D)


# 🗓 Patterns (hour-of-day heatmap)
@dp.message_handler(lambda m: m.text in ["🗓 Patterns", "Patterns"])
async def patterns_handler(message: types.Message):
    await send_window(message, Window.PATTERNS)


# ---- inline keyboard: re-render the same card in place ----

async def _swap_card(call: types.CallbackQuery, window: Window, theme: str) -> None:
    report = reports.for_window(window, theme=theme)
    if report.is_empty:
        await call.answer(report.text, show_alert=True)
        return

    await bot.edit_message_media(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        media=types.InputMediaPhoto(
            _photo(report), caption=report.text, parse_mode="HTML"
        ),
        reply_markup=chart_buttons(window, theme),
    )


@dp.callback_query_handler(lambda c: c.data and c.data.startswith(f"{callbacks.WINDOW}:"))
async def window_callback(call: types.CallbackQuery):
    _, window = callbacks.decode(call.data)
    await call.answer()
    await _swap_card(call, window, subs.theme(call.message.chat.id))


@dp.callback_query_handler(lambda c: c.data and c.data.startswith(f"{callbacks.THEME}:"))
async def theme_callback(call: types.CallbackQuery):
    _, window = callbacks.decode(call.data)
    theme = subs.toggle_theme(call.message.chat.id)
    await call.answer(f"{theme.capitalize()} charts")
    await _swap_card(call, window, theme)


# 🔔 Subscribe / Unsubscribe
@dp.message_handler(lambda m: m.text in ["🔔 Subscribe", "🔔 Unsubscribe"])
async def toggle_sub_handler(message: types.Message):
    subscribed = subs.toggle(message.chat.id)
    text = "🔔 Subscribed to alerts." if subscribed else "🔕 Unsubscribed from alerts."
    await message.answer(text, reply_markup=main_menu_markup(subscribed))


@dp.message_handler(lambda m: m.text in ["ℹ️ Info", "Info"])
async def info_handler(message: types.Message):
    await message.answer(
        info_text(settings.thresholds),
        reply_markup=menu(message.chat.id),
        disable_web_page_preview=True,
    )


# =======================
# AMQP consumer integration
# =======================

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
            await _deliver_alert_as_text(chat_id, alert)
            continue
        try:
            sent = await bot.send_photo(
                chat_id=chat_id,
                photo=uploaded.get(theme) or _photo(card),
                caption=card.text or None,
                reply_markup=main_menu_markup(True),
            )
            uploaded.setdefault(theme, sent.photo[-1].file_id)
            log.debug("Delivered alert to chat_id=%s", chat_id)
        except Exception as e:
            # ignore per-chat errors to avoid blocking the rest
            log.warning("Failed to deliver to chat_id=%s: %s", chat_id, e)
            await _deliver_alert_as_text(chat_id, alert)


async def _deliver_alert_as_text(chat_id: int, alert: Alert) -> None:
    """Last resort: an alert that cannot be drawn must still arrive."""
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=reports.alert_text(alert.pm25, alert.pm10, alert.observed_at),
            reply_markup=main_menu_markup(True),
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
