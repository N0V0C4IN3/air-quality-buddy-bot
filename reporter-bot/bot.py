import asyncio
import logging

from aiogram import Bot, Dispatcher, executor, types

from common.alerts import Alert
from common.db import Database

from config import settings
from consumer import AsyncConsumer
from html_helpers import info_text
from markup import main_menu_markup
from reports import ReadingReports, Window
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
reports = ReadingReports(db, settings.tz, settings.thresholds)


def menu(chat_id: int):
    return main_menu_markup(subs.is_subscribed(chat_id))


async def send_report(message: types.Message, window: Window) -> None:
    report = reports.for_window(window)
    await message.answer(
        report.text, reply_markup=menu(message.chat.id), disable_web_page_preview=True
    )
    if report.chart:
        await message.answer_photo(photo=report.chart)


# start shows menu
@dp.message_handler(commands=["start"])
async def start_cmd(message: types.Message):
    await message.answer("Welcome! Use the buttons below:", reply_markup=menu(message.chat.id))


# 📟 Status
@dp.message_handler(lambda m: m.text == "📟 Status")
async def status_handler(message: types.Message):
    block = reports.latest()
    if not block:
        await message.answer("No readings yet.", reply_markup=menu(message.chat.id))
        return
    await message.answer(
        block, reply_markup=menu(message.chat.id), disable_web_page_preview=True
    )


# 📅 Today
@dp.message_handler(lambda m: m.text == "📅 Today")
async def today_handler(message: types.Message):
    await send_report(message, Window.TODAY)


# 🕒 Last 12h
@dp.message_handler(lambda m: m.text == "🕒 Last 12h")
async def last12_handler(message: types.Message):
    await send_report(message, Window.LAST_12H)


# 📈 Last 7d
@dp.message_handler(lambda m: m.text == "📈 Last 7d")
async def last7_handler(message: types.Message):
    await send_report(message, Window.LAST_7D)


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

    text = reports.status_block(alert.pm25, alert.pm10, alert.observed_at)
    for chat_id in subscribers:
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=main_menu_markup(True),
                disable_web_page_preview=True,
            )
            log.debug("Delivered alert to chat_id=%s", chat_id)
        except Exception as e:
            # ignore per-chat errors to avoid blocking the rest
            log.warning("Failed to deliver to chat_id=%s: %s", chat_id, e)


def _done(t: asyncio.Task):
    try:
        t.result()
    except asyncio.CancelledError:
        pass
    except Exception:
        log.exception("AMQP consumer task crashed")


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
