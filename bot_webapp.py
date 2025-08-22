# bot_webapp.py — WebApp-игра «тапалка» (только для приватного чата)

import json
import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from telegram import Update, KeyboardButton, ReplyKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

# ── НАСТРОЙКИ ───────────────────────────────────────────────────────────────
TOKEN = "8380517379:AAF1pCJKN2uz2YL86yw_wKcFHGy_oFmvOjQ"              # оставь свой
WEBAPP_URL = "https://sait-ama.github.io/eternal/"  # оставь свой
SAVE_FILE = Path("tap_saves.json")

# Проверка токена по формату (защитит от InvalidToken)
if not re.fullmatch(r"\d{6,}:[A-Za-z0-9_-]{30,}", TOKEN):
    print("❌ Некорректный TOKEN. Укажи реальный токен от @BotFather.")
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

# ── ХЭНДЛЕРЫ ДЛЯ ПРИВАТНОГО ЧАТА ────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_webapp_keyboard(update, context, first=True)

async def tap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_webapp_keyboard(update, context, first=False)

async def send_webapp_keyboard(update: Update, context: ContextTypes.DEFAULT_TYPE, first: bool):
    if not WEBAPP_URL.startswith("https://"):
        await update.message.reply_text("⚠ WEBAPP_URL должен быть HTTPS. Сейчас: " + WEBAPP_URL)
        return

    kb = ReplyKeyboardMarkup(
        [[KeyboardButton(text="🎮 Запустить игру (WebApp)", web_app=WebAppInfo(url=WEBAPP_URL))]],
        resize_keyboard=True, one_time_keyboard=False
    )
    text = ("Привет! Нажми кнопку ниже, чтобы открыть игру с Telegram-контекстом.\n\n"
            "Важно: запускать именно через ЭТУ кнопку (или через Menu Button), иначе «Сохранить» не работает.")
    await update.message.reply_text(text if first else "Открой игру ниже ⤵️", reply_markup=kb)

async def on_webapp_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ловим tg.sendData(...) — приходит только из ЛС."""
    wad = update.message.web_app_data
    raw = wad.data if wad else ""
    log.info("WEB_APP_DATA from %s: %s", update.effective_user.id if update.effective_user else "?", raw)

    try:
        data = json.loads(raw)
    except Exception:
        await update.message.reply_text("Получены данные WebApp, но парсинг не удался.")
        return

    if isinstance(data, dict) and data.get("type") == "sync":
        state = data.get("state", {})
        saves = load_saves()
        uid = str(update.effective_user.id)
        saves[uid] = {
            "state": state,
            "updated_at": datetime.utcnow().isoformat() + "Z",
            "name": update.effective_user.full_name,
            "username": update.effective_user.username,
        }
        save_saves(saves)
        await update.message.reply_text("Состояние сохранено на сервере ✅")
        return

# ── ХЭНДЛЕР ДЛЯ НЕПРИВАТНЫХ ЧАТОВ (группы/каналы) ──────────────────────────
async def not_private(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "Эта мини-игра доступна только в ЛИЧНОМ чате с ботом.\n"
        "Откройте диалог с ботом и отправьте /tap, затем нажмите кнопку "
        "«🎮 Запустить игру (WebApp)»."
    )
    if update.effective_message:
        await update.effective_message.reply_text(msg)

    await update.message.reply_text("WebApp: данные получены.")


# ── MAIN ───────────────────────────────────────────────────────────────────
def main():
    app = Application.builder().token(TOKEN).build()

    PRIVATE = filters.ChatType.PRIVATE

    # Команды — только в личке
    app.add_handler(CommandHandler("start", start, filters=PRIVATE))
    app.add_handler(CommandHandler("tap", tap, filters=PRIVATE))

    # Данные из WebApp — только в личке
    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA & PRIVATE, on_webapp_data))

    # Любые другие сообщения в личке — подсказка + клавиатура
    app.add_handler(MessageHandler(PRIVATE & ~filters.StatusUpdate.WEB_APP_DATA, tap))

    # Всё остальное (группы/каналы) — аккуратное объяснение
    app.add_handler(MessageHandler(~PRIVATE, not_private))

    log.info("Bot is running. Use /tap in PRIVATE chat and press the reply-keyboard button.")
    app.run_polling()

if __name__ == "__main__":
    main()
