# bot_webapp.py
# Пример интеграции Telegram WebApp для игры «тапалка»

import json
import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from telegram import (
    Update,
    InlineKeyboardMarkup, InlineKeyboardButton,
    KeyboardButton, ReplyKeyboardMarkup, WebAppInfo,
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ContextTypes, filters,
)

# ───────── НАСТРОЙКИ ─────────
# Вариант 1: впиши токен прямо сюда (только твой реальный токен от @BotFather!)
TOKEN = "8380517379:AAF1pCJKN2uz2YL86yw_wKcFHGy_oFmvOjQ"

# Вариант 2: через переменную окружения (безопаснее):
# TOKEN = os.getenv("BOT_TOKEN", "").strip() or "ВАШ_ТОКЕН_ОТ_BotFather"

# URL страницы index.html (должен быть HTTPS!)
WEBAPP_URL = "https://voluble-rugelach-c8f17e.netlify.app"
# или через переменную окружения:
# WEBAPP_URL = os.getenv("WEBAPP_URL", "https://your-host.example.com/index.html").strip()

SAVE_FILE = Path("tap_saves.json")

# ───────── ПРОВЕРКА ТОКЕНА ─────────
if not re.fullmatch(r"\d{6,}:[A-Za-z0-9_-]{30,}", TOKEN):
    print("❌ Некорректный TOKEN. Укажи настоящий токен от @BotFather.")
    sys.exit(1)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("tapper")

def load_saves():
    if SAVE_FILE.exists():
        try:
            return json.loads(SAVE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

def save_saves(data):
    try:
        SAVE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        log.warning("Save failed: %s", e)

# --- команды ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Это бот с WebApp-игрой «Тапалка». Используй /tap чтобы открыть игру."
    )

async def tap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not WEBAPP_URL.startswith("https://"):
        await update.message.reply_text("⚠ WEBAPP_URL должен быть HTTPS. Сейчас: " + WEBAPP_URL)
    kb_inline = InlineKeyboardMarkup([
        [InlineKeyboardButton(text="🚀 Открыть игру", web_app=WebAppInfo(url=WEBAPP_URL))]
    ])
    await update.message.reply_text("Открой игру:", reply_markup=kb_inline)

    kb = ReplyKeyboardMarkup(
        [[KeyboardButton(text="🎮 Запустить игру", web_app=WebAppInfo(url=WEBAPP_URL))]],
        resize_keyboard=True
    )
    await update.message.reply_text("Или через меню:", reply_markup=kb)

# --- приём данных от WebApp ---
async def any_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if msg and getattr(msg, "web_app_data", None) and msg.web_app_data.data:
        raw = msg.web_app_data.data
        try:
            data = json.loads(raw)
        except Exception:
            await msg.reply_text("Получены данные WebApp, но парсинг не удался.")
            return

        if isinstance(data, dict) and data.get("type") == "sync":
            state = data.get("state", {})
            saves = load_saves()
            uid = str(msg.from_user.id)
            saves[uid] = {
                "state": state,
                "updated_at": datetime.utcnow().isoformat() + "Z",
                "name": msg.from_user.full_name,
                "username": msg.from_user.username,
            }
            save_saves(saves)
            await msg.reply_text("Состояние сохранено на сервере ✅")
            return

        await msg.reply_text("WebApp: данные получены.")
        return

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("tap", tap))
    app.add_handler(MessageHandler(filters.ALL, any_message))

    log.info("Bot is running. /tap to open WebApp.")
    app.run_polling()

if __name__ == "__main__":
    main()
