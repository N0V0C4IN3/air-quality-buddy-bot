from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

def main_menu_markup(subscribed: bool) -> ReplyKeyboardMarkup:
    sub_text = "🔔 Unsubscribe" if subscribed else "🔔 Subscribe"
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(
        KeyboardButton("📟 Status"),
    )
    kb.row(
        KeyboardButton("📅 Today"),
        KeyboardButton("🕒 Last 24h"),
        KeyboardButton("📈 Last 7d"),
    )
    kb.add(KeyboardButton(sub_text))
    return kb