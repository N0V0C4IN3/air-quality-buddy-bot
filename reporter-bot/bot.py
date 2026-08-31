from aiogram import Bot, Dispatcher, executor, types
from config import settings
from common.db import Database, ReadingRepository, ChatRepository
from charts import df_to_line_chart_png
import pandas as pd
from io import BytesIO
from datetime import datetime, timedelta, timezone
from markup import main_menu_markup
from html_helpers import *
from config import settings
from consumer import AsyncConsumer, require 
import os
import asyncio
import logging
from redis_helper import RedisHelper

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("reporter_bot")

try:
    from zoneinfo import ZoneInfo
    TIMEZONE = ZoneInfo(settings.timezone)
except Exception:
    TIMEZONE = timezone.utc

bot = Bot(token=settings.telegram_token, parse_mode="HTML")
dp = Dispatcher(bot)
redis = RedisHelper(host=settings.redis_host, port=settings.redis_port)

def classify(pm25, pm10):
    if pm25 >= settings.pm25_err or pm10 >= settings.pm10_err:
        return "err"
    if pm25 >= settings.pm25_warn or pm10 >= settings.pm10_warn:
        return "warn"
    return "ok"

def get_latest():
    db = Database(url=settings.database_url)
    with db.session() as s:
        return ReadingRepository(s).get_latest()

def get_range(start: datetime, end: datetime):
    db = Database(url=settings.database_url)
    with db.session() as s:
        return ReadingRepository(s).get_range(start=start, end=end)
    
async def get_data_and_create_chart(start: datetime, end: datetime, title, message: types.Message):
    rows = get_range(start.astimezone(ZoneInfo("UTC")), end.astimezone(ZoneInfo("UTC")))
    df = pd.DataFrame([{"timestamp": r.timestamp, "pm25": r.pm25, "pm10": r.pm10} for r in rows])
    if df.empty:
        await message.answer("No data for today.")
        return

    stats = df.agg({"pm25": ["min","mean","max"], "pm10": ["min","mean","max"]}).round(1)
    msg = make_stats_table(
        stats.loc["min","pm25"], stats.loc["mean","pm25"], stats.loc["max","pm25"],
        stats.loc["min","pm10"], stats.loc["mean","pm10"], stats.loc["max","pm10"],
        len(df),
        title=title
    )
    await message.answer(msg, reply_markup=main_menu_markup(redis.is_subscribed(message.chat.id )), disable_web_page_preview=True)

    bio = df_to_line_chart_png(df, title=title, tz=settings.timezone)
    bio.name = f"{title}.png"
    await message.answer_photo(photo=bio)

# start shows menu
@dp.message_handler(commands=["start"])
async def start_cmd(message: types.Message):
    chat_id = message.chat.id
    text = "Welcome! Use the buttons below:"
    await message.answer(text, reply_markup=main_menu_markup(redis.is_subscribed(chat_id)))

# 📟 Status
@dp.message_handler(lambda m: m.text == "📟 Status")
async def status_handler(message: types.Message):
    r = get_latest()
    if not r:
        await message.answer("No readings yet.")
        return
    await message.answer(
        format_status_block(r.pm25, r.pm10, r.timestamp),
        reply_markup=main_menu_markup(redis.is_subscribed(message.chat.id)),
        disable_web_page_preview=True,
    )
    
# 📅 Today
@dp.message_handler(lambda m: m.text == "📅 Today")
async def today_handler(message: types.Message):
    now = datetime.now(TIMEZONE)
    start = datetime(now.year, now.month, now.day, tzinfo=TIMEZONE)
    end = start + timedelta(days=1)
    await get_data_and_create_chart(start, end, "Today", message)
    
# 🕒 Last 12h
@dp.message_handler(lambda m: m.text == "🕒 Last 12h")
async def last12_handler(message: types.Message):
    now = datetime.now(TIMEZONE)
    start = now - timedelta(hours=12)
    await get_data_and_create_chart(start, now, "Last 12h", message)

# 📈 Last 7d
@dp.message_handler(lambda m: m.text == "📈 Last 7d")
async def last7_handler(message: types.Message):
    now = datetime.now(TIMEZONE)
    start = now - timedelta(days=7)
    await get_data_and_create_chart(start, now, "Last 7d", message)

# 🔔 Subscribe / Unsubscribe
@dp.message_handler(lambda m: m.text in ["🔔 Subscribe","🔔 Unsubscribe"])
async def toggle_sub_handler(message: types.Message):
    chat_id = message.chat.id
    subscribed = redis.is_subscribed(chat_id)

    with Database(url=settings.database_url).session() as s:
        repo = ChatRepository(s)
        if subscribed:
            redis.remove_subscriber(chat_id)
            repo.upsert(chat_id, False)
            await message.answer("🔕 Unsubscribed from alerts.", reply_markup=main_menu_markup(False))
        else:
            repo.upsert(chat_id, True)
            redis.add_subscriber(chat_id)
            await message.answer("🔔 Subscribed to alerts.", reply_markup=main_menu_markup(True))

@dp.message_handler(lambda m: m.text in ["ℹ️ Info", "Info"])
async def info_handler(message: types.Message):
    await message.answer(
        info_text(),
        reply_markup=main_menu_markup(redis.is_subscribed(message.chat.id)),
        disable_web_page_preview=True,
    )

# =======================
# AMQP consumer integration
# =======================

def _parse_routing_keys() -> list[str]:
    # Comma- or whitespace-separated in env
    rks = require("AQ_ROUTING_KEYS")
    log.debug("Parsing AQ_ROUTING_KEYS raw='%s'", rks)

    parts = []
    for chunk in rks.split(","):
        parts.extend(chunk.split())
    keys = [p.strip() for p in parts if p.strip()]

    if not keys:
        log.error("AQ_ROUTING_KEYS provided but parsed to empty list")
        raise RuntimeError("AQ_ROUTING_KEYS provided but empty after parsing")
    
    log.info("Routing keys parsed: %s", keys)
    return keys

async def _on_alert(msg: dict) -> None:
    """
    Called for each AMQP message (already JSON-decoded).
    Sends notifications to all subscribed chats.
    """
    # Free-form: support err/warn/ok and generic payloads
    # mtype = msg.get("type", "alert").upper()
    pm25 = msg.get("pm25_value")
    pm10 = msg.get("pm10_value")
    ts   = msg.get("ts")

    subscribers = redis.get_subscribers()
    sub_count = len(subscribers)
    log.info("Received alert msg keys=%s pm25=%s pm10=%s ts=%s -> fanout to %d subscriber(s)",
             list(msg.keys()), pm25, pm10, ts, sub_count)

    if sub_count == 0:
        log.warning("No subscribers; message will not be delivered")

    # Fan out to subscribers (simple in-memory set)
    for chat_id in subscribers:
        try:
            await bot.send_message(
                chat_id=chat_id, 
                text=format_status_block(pm25, pm10, datetime.fromtimestamp(ts, tz=timezone.utc)), 
                reply_markup=main_menu_markup(chat_id in subscribers),
                disable_web_page_preview=True)
            
            log.debug("Delivered alert to chat_id=%s", chat_id)
        except Exception as e:
            # ignore per-chat errors to avoid blocking the rest
            log.warning("Failed to deliver to chat_id=%s: %s", chat_id, e)

async def on_startup(_):
    """
    aiogram startup hook: start the AMQP consumer task.
    """
    url      = require("AMQP_URL")              # e.g. amqp://user:pass@rabbitmq:5672/ or /vhost
    exchange = require("AQ_EXCHANGE")           # e.g. aq.alerts
    queue    = require("AQ_QUEUE_REPORTER")     # e.g. reporter-bot-alerts
    keys     = _parse_routing_keys()            # e.g. alerts.pm.err,alerts.pm.warn OR alerts.pm.#


    log.info("Starting AMQP consumer exchange=%s queue=%s keys=%s",
             exchange, queue, keys)

    consumer = AsyncConsumer(
        url=url,
        exchange=exchange,
        queue=queue,
        routing_keys=keys,
    )

    # Optional QoS via env
    prefetch = os.environ.get("AMQP_PREFETCH")
    if prefetch:
        # set via env inside consumer.start() (already supported in AsyncConsumer)
        log.info("Using AMQP_PREFETCH=%s (consumer will set QoS)", prefetch)

    # Keep references on dispatcher for shutdown
    task = asyncio.create_task(consumer.start(_on_alert), name="amqp-consumer")

    task.add_done_callback(_done)

    dp["amqp_consumer"] = consumer
    dp["amqp_task"] = task

    # Preload cache from DB
    db = Database(url=settings.database_url)
    with db.session() as s:
        repo = ChatRepository(s)
        ids = repo.get_subscribed_ids()
        redis.preload_subscribers(ids)
        log.info("Preloaded %d subscriber(s) into Redis", len(ids))
    
    log.info("[startup] AMQP consumer task created")

def _done(t: asyncio.Task):
    try:
        t.result()
    except asyncio.CancelledError:
        pass
    except Exception:
        log.exception("AMQP consumer task crashed")

async def on_shutdown(_):
    """
    aiogram shutdown hook: cancel the AMQP consumer task.
    """
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
