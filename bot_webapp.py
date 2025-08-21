# bot_webapp.py
# Пример интеграции Telegram WebApp для «тапалки» без лимитов кликов и с AFK.
# Требования: python-telegram-bot >= 20
# Установить: pip install python-telegram-bot==20.7
#
# 1) Залейте index.html на любой хостинг с HTTPS (например, GitHub Pages, Vercel, Netlify).
# 2) Укажите URL в WEBAPP_URL ниже.
# 3) Запустите бота. Команда /tap пришлёт кнопку «Открыть игру» (WebApp).
# 4) Веб-страница хранит прогресс локально. Кнопка «Сохранить» шлёт state боту (опционально).
# 5) Вы можете сохранять state на сервере/в файле и строить топы.

import json
import logging
import os
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

TOKEN = os.getenv("BOT_TOKEN", "PASTE_YOUR_TOKEN_HERE")
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://your-host.example.com/index.html")

# Файл для опционального сервера сохранений
SAVE_FILE = Path("tap_saves.json")

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

@CommandHandler("start")
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Это бот с WebApp-игрой «Тапалка». Используй /tap чтобы открыть игру в полноэкранном режиме внутри Telegram."
    )

@CommandHandler("tap")
async def tap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # 1) Вариант с инлайн-кнопкой
    kb_inline = InlineKeyboardMarkup([
        [InlineKeyboardButton(text="🚀 Открыть игру", web_app=WebAppInfo(url=WEBAPP_URL))]
    ])
    await update.message.reply_text("Открой игру:", reply_markup=kb_inline)

    # 2) Вариант с реплай-клавиатурой (на выбор)
    kb = ReplyKeyboardMarkup(
        [[KeyboardButton(text="🎮 Запустить игру", web_app=WebAppInfo(url=WEBAPP_URL))]],
        resize_keyboard=True
    )
    await update.message.reply_text("Или через меню:", reply_markup=kb)

# Обработка данных, которые WebApp отправляет через tg.sendData(JSON.stringify(...))
# В PTB v20 это обычное текстовое сообщение с полем web_app_data в API.
@MessageHandler(filters.ALL)
async def any_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if msg and msg.web_app_data and msg.web_app_data.data:
        # это данные от WebApp
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
            await msg.reply_text("Состояние сохранено на сервере бота ✅")
            return

        await msg.reply_text("WebApp: данные получены.")
        return

    # Прочее ваше поведение
    # Можно оставить пустым или добавить ответы по умолчанию
    # log.info("Non-webapp message: %s", msg.text if msg else None)

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(start)
    app.add_handler(tap)
    app.add_handler(any_message)
    log.info("Bot is running. /tap to open WebApp.")
    app.run_polling()

if __name__ == "__main__":
    main()
