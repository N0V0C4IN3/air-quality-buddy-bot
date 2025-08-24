from aiogram import Bot, Dispatcher, executor, types
from config import settings
from common.db import Database, ReadingRepository
from charts import df_to_line_chart_png
import pandas as pd
from io import BytesIO
from datetime import datetime, timedelta, timezone
from markup import main_menu_markup
from html_helpers import *

UTC = timezone.utc
bot = Bot(token=settings.telegram_token, parse_mode="HTML")
dp = Dispatcher(bot)

SUBSCRIBERS: set[int] = set()

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

def get_range(start, end):
    db = Database(url=settings.database_url)
    with db.session() as s:
        return ReadingRepository(s).get_range(start=start, end=end)
    
async def get_data_and_create_chart(start, end, title, message: types.Message):
    rows = get_range(start, end)
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
    await message.answer(msg, reply_markup=main_menu_markup(message.chat.id in SUBSCRIBERS), disable_web_page_preview=True)

    bio = df_to_line_chart_png(df, title=title)
    bio.name = f"{title}.png"
    await message.answer_photo(photo=bio)

# start shows menu
@dp.message_handler(commands=["start"])
async def start_cmd(message: types.Message):
    chat_id = message.chat.id
    text = "Welcome! Use the buttons below:"
    await message.answer(text, reply_markup=main_menu_markup(chat_id in SUBSCRIBERS))

# 📟 Status
@dp.message_handler(lambda m: m.text == "📟 Status")
async def status_handler(message: types.Message):
    r = get_latest()
    if not r:
        await message.answer("No readings yet.")
        return
    await message.answer(
        format_status_block(r.pm25, r.pm10, r.timestamp),
        reply_markup=main_menu_markup(message.chat.id in SUBSCRIBERS),
        disable_web_page_preview=True,
    )
    
# 📅 Today
@dp.message_handler(lambda m: m.text == "📅 Today")
async def today_handler(message: types.Message):
    now = datetime.now(UTC)
    start = datetime(now.year, now.month, now.day, tzinfo=UTC)
    end = start + timedelta(days=1)
    rows = get_range(start, end)
    await get_data_and_create_chart(start, end, "Today", message)
    
# 🕒 Last 24h
@dp.message_handler(lambda m: m.text == "🕒 Last 24h")
async def last24_handler(message: types.Message):
    now = datetime.now(UTC)
    start = now - timedelta(hours=24)
    await get_data_and_create_chart(start, now, "Last 24h", message)

# 📈 Last 7d
@dp.message_handler(lambda m: m.text == "📈 Last 7d")
async def last7_handler(message: types.Message):
    now = datetime.now(UTC)
    start = now - timedelta(days=7)
    await get_data_and_create_chart(start, now, "Last 7d", message)

# 🔔 Subscribe / Unsubscribe
@dp.message_handler(lambda m: m.text in ["🔔 Subscribe","🔔 Unsubscribe"])
async def toggle_sub_handler(message: types.Message):
    chat_id = message.chat.id
    if chat_id in SUBSCRIBERS:
        SUBSCRIBERS.remove(chat_id)
        await message.answer("🔕 Unsubscribed from alerts.", reply_markup=main_menu_markup(False))
    else:
        SUBSCRIBERS.add(chat_id)
        await message.answer("🔔 Subscribed to alerts.", reply_markup=main_menu_markup(True))

@dp.message_handler(lambda m: m.text in ["ℹ️ Info", "Info"])
async def info_handler(message: types.Message):
    await message.answer(
        info_text(),
        reply_markup=main_menu_markup(message.chat.id in SUBSCRIBERS),
        disable_web_page_preview=True,
    )

if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)
