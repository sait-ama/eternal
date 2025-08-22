# bot_webapp.py — WebApp «тапалка» + привязка ReManga (поиск в 2+ JSON)
# Требования: python-telegram-bot==21.*

import json
import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote  # ⬅ для ?profile=

from telegram import (
    Update,
    WebAppInfo,
    KeyboardButton,
    ReplyKeyboardMarkup,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    MenuButtonWebApp,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ── НАСТРОЙКИ ───────────────────────────────────────────────────────────────
# ВАЖНО: здесь у вас используется прямой токен; оставляю, как вы прислали.
# Обычно лучше брать из переменной окружения: os.getenv("BOT_TOKEN")
TOKEN = os.getenv("8380517379:AAF1pCJKN2uz2YL86yw_wKcFHGy_oFmvOjQ", "8380517379:AAF1pCJKN2uz2YL86yw_wKcFHGy_oFmvOjQ").strip()
WEBAPP_URL = os.getenv("https://sait-ama.github.io/eternal/", "https://sait-ama.github.io/eternal/").strip() or "https://example.com/index.html"

REMANGA_DATA_FILES_ENV = os.getenv("REMANGA_DATA_FILES", "history_ew.json,history_ed.json,history_e.json")
REMANGA_DATA_FILES: List[Path] = [Path(x.strip()) for x in REMANGA_DATA_FILES_ENV.split(",") if x.strip()]

LINKS_FILE = Path("user_links.json")
SAVE_FILE = Path("tap_saves.json")

# ── ПРОВЕРКИ ────────────────────────────────────────────────────────────────
if not re.fullmatch(r"\d{6,}:[A-Za-z0-9_-]{30,}", TOKEN or ""):
    print("❌ BOT_TOKEN не задан или некорректен. Укажи реальный токен от @BotFather в переменной окружения BOT_TOKEN.")
    sys.exit(1)

if not WEBAPP_URL.startswith("https://"):
    print(f"⚠ WEBAPP_URL должен быть HTTPS. Сейчас: {WEBAPP_URL}")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("tapper")

# ── УТИЛИТЫ JSON ───────────────────────────────────────────────────────────
def read_json(path: Path, default: Any) -> Any:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning("JSON read failed for %s: %s", path, e)
    return default

def write_json(path: Path, data: Any) -> None:
    try:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        log.warning("JSON write failed for %s: %s", path, e)

# ── НОРМАЛИЗАЦИЯ ССЫЛКИ REMANGA ────────────────────────────────────────────
def normalize_profile_url(url: str) -> Optional[str]:
    """
    Приводим к канону: https://remanga.org/user/<digits>/about
    """
    if not isinstance(url, str):
        return None
    url = url.strip()
    m = re.match(r"^https?://(?:www\.)?remanga\.org/user/(\d+)/(?:about/?)?$", url, re.IGNORECASE)
    if not m:
        return None
    user_id = m.group(1)
    return f"https://remanga.org/user/{user_id}/about"

# ── ЗАГРУЗКА И ПОИСК В НЕСКОЛЬКИХ ФАЙЛАХ ──────────────────────────────────
def load_all_remanga_records() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for p in REMANGA_DATA_FILES:
        data = read_json(p, default=[])
        if isinstance(data, list):
            for row in data:
                if isinstance(row, dict):
                    r = dict(row)
                    r["_source_file"] = str(p)
                    out.append(r)
        else:
            log.warning("Ожидался список в %s, получили %s", p, type(data).__name__)
    return out

def find_profile_in_all(profile_url: str) -> Optional[Dict[str, Any]]:
    norm = normalize_profile_url(profile_url)
    if not norm:
        return None
    for row in load_all_remanga_records():
        prof = row.get("profile")
        if prof and normalize_profile_url(str(prof)) == norm:
            return row
    return None

# ── ПРИВЯЗКИ user_id -> profile ───────────────────────────────────────────
def load_links() -> Dict[str, str]:
    return read_json(LINKS_FILE, default={})

def save_links(links: Dict[str, str]) -> None:
    write_json(LINKS_FILE, links)

# ── ОТПРАВКА КНОПОК WEBAPP ────────────────────────────────────────────────
async def send_inline_play(update: Update):
    kb_inline = InlineKeyboardMarkup([
        [InlineKeyboardButton(text="🚀 Играть", web_app=WebAppInfo(url=WEBAPP_URL))]
    ])
    await update.message.reply_text(
        "Нажми «Играть», чтобы открыть мини-приложение:",
        reply_markup=kb_inline,
    )

# (опционально) альтернативная реплай-клавиатура — если пригодится:
async def send_reply_keyboard(update: Update):
    kb_reply = ReplyKeyboardMarkup(
        [[KeyboardButton(text="🎮 Запустить игру", web_app=WebAppInfo(url=WEBAPP_URL))]],
        resize_keyboard=True
    )
    await update.message.reply_text("Можно и через меню ниже ⤵️", reply_markup=kb_reply)

# ── ХЭНДЛЕРЫ WEBAPP /start /tap ───────────────────────────────────────────
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_inline_play(update)  # только по /start показываем кнопку
    # при желании можно показать и реплай-клавиатуру:
    # await send_reply_keyboard(update)

async def tap_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_inline_play(update)

# Приём tg.sendData из WebApp
async def on_webapp_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    wad = update.message.web_app_data if update.message else None
    raw = wad.data if wad else ""
    uid = update.effective_user.id if update.effective_user else "?"
    log.info("WEB_APP_DATA from %s: %s", uid, raw)

    try:
        data = json.loads(raw)
    except Exception:
        if update.message:
            await update.message.reply_text("Получены данные WebApp, но парсинг не удался.")
        return

    if isinstance(data, dict) and data.get("type") == "sync":
        state = data.get("state", {})
        saves = read_json(SAVE_FILE, default={})
        saves[str(uid)] = {
            "state": state,
            "updated_at": datetime.utcnow().isoformat() + "Z",
            "name": update.effective_user.full_name if update.effective_user else "",
            "username": update.effective_user.username if update.effective_user else "",
        }
        write_json(SAVE_FILE, saves)
        if update.message:
            await update.message.reply_text("Состояние сохранено на сервере ✅")
        return

    if update.message:
        await update.message.reply_text("WebApp: данные получены.")

# ── РЕГИСТРАЦИЯ / REMANGA ─────────────────────────────────────────────────
HELP_REGISTER = (
    "Регистрация профиля ReManga:\n"
    "/register https://remanga.org/user/123456/about\n\n"
    "После привязки используй /remanga — бот найдёт запись в подключённых JSON и покажет diff и другие поля."
)

async def register_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    uid = str(update.effective_user.id)

    url = None
    if context.args:
        url = " ".join(context.args).strip()
    elif msg and msg.text:
        parts = msg.text.split(maxsplit=1)
        if len(parts) == 2:
            url = parts[1].strip()

    if not url:
        await msg.reply_text(HELP_REGISTER)
        return

    norm = normalize_profile_url(url)
    if not norm:
        await msg.reply_text("Ссылка не похожа на профиль ReManga. Пример: https://remanga.org/user/2384260/about")
        return

    links = load_links()
    links[uid] = norm
    save_links(links)

    # 🔽 Добавляем кнопку «Играть с привязкой» (WebApp подхватит ?profile=)
    play_url = f"{WEBAPP_URL}?profile={quote(norm)}"
    play_kb = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            text="🚀 Играть с привязкой",
            web_app=WebAppInfo(url=play_url)
        )
    ]])

    row = find_profile_in_all(norm)
    if row:
        await reply_remanga_card(msg, row, prefix="✅ Профиль привязан.\n")
        await msg.reply_text("Открой игру с привязанным профилем:", reply_markup=play_kb)
    else:
        missing = ", ".join(str(p) for p in REMANGA_DATA_FILES if not p.exists())
        add = f"\n\n⚠ Отсутствуют файлы: {missing}" if missing else ""
        await msg.reply_text(
            f"✅ Профиль привязан: {norm}\n"
            f"Пока записи в JSON не найдено. Обнови данные и используй /remanga.{add}",
            reply_markup=play_kb
        )

async def mylink_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    url = load_links().get(uid)
    if url:
        await update.effective_message.reply_text(f"Твоя привязка: {url}")
    else:
        await update.effective_message.reply_text("Привязка не найдена. Используй /register <url>.")

async def unlink_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    links = load_links()
    if links.pop(uid, None) is not None:
        save_links(links)
        await update.effective_message.reply_text("Привязка удалена.")
    else:
        await update.effective_message.reply_text("У тебя не было привязки.")

async def remanga_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    url = load_links().get(uid)
    if not url:
        await update.effective_message.reply_text("Сначала привяжи профиль: /register <url>")
        return

    row = find_profile_in_all(url)
    if not row:
        await update.effective_message.reply_text(
            "В подключённых JSON не найден твой профиль. Проверь поле profile."
        )
        return

    await reply_remanga_card(update.effective_message, row)

async def where_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    url = load_links().get(uid)
    if not url:
        await update.effective_message.reply_text("Сначала /register <url>")
        return
    row = find_profile_in_all(url)
    if not row:
        await update.effective_message.reply_text("Запись не найдена ни в одном файле.")
        return
    src = row.get("_source_file", "?")
    await update.effective_message.reply_text(f"Источник данных: {src}")

async def reply_remanga_card(target_message, row: Dict[str, Any], prefix: str = ""):
    display = row.get("display") or row.get("norm") or "—"
    diff = row.get("diff", "—")
    initial = row.get("initial", "—")
    current = row.get("current", "—")
    guild = row.get("guild", "—")
    last_active = row.get("last_active_human", "—")
    profile = row.get("profile", "—")
    src = row.get("_source_file", "—")

    text = (
        f"{prefix}"
        f"👤 <b>{display}</b>\n"
        f"🔗 <a href=\"{profile}\">Профиль ReManga</a>\n"
        f"🏰 Гильдия: <b>{guild}</b>\n"
        f"🕒 Активность: {last_active}\n"
        f"💾 initial: <code>{initial}</code>\n"
        f"📈 current: <code>{current}</code>\n"
        f"⚖️ diff: <b>{diff}</b>\n"
        f"📁 Источник: <code>{src}</code>"
    )
    await target_message.reply_html(text, disable_web_page_preview=True)

# ── НЕПРИВАТНЫЕ ЧАТЫ ───────────────────────────────────────────────────────
async def not_private(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_message:
        await update.effective_message.reply_text(
            "Эта мини-игра и регистрация доступны только в ЛИЧНОМ чате с ботом. "
            "Открой диалог с ботом и отправь /start."
        )

# ── POST_INIT: Menu Button WebApp ─────────────────────────────────────────
async def post_init(app: Application):
    try:
        await app.bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(text="🎮 Играть", web_app=WebAppInfo(url=WEBAPP_URL))
        )
    except Exception as e:
        log.warning("Не удалось выставить Menu Button: %s", e)

# ── MAIN ───────────────────────────────────────────────────────────────────
def main():
    app = Application.builder().token(TOKEN).post_init(post_init).build()
    PRIVATE = filters.ChatType.PRIVATE

    # Команды в ЛС
    app.add_handler(CommandHandler("start", start_cmd, filters=PRIVATE))
    app.add_handler(CommandHandler("tap", tap_cmd, filters=PRIVATE))

    # Регистрация / Remanga
    app.add_handler(CommandHandler(["register", "link", "registraciya"], register_cmd, filters=PRIVATE))
    # Русские алиасы как текстовые команды
    app.add_handler(MessageHandler(PRIVATE & filters.Regex(r"^/(?:регистрация|привязка)(?:@\w+)?(?:\s+.*)?$"), register_cmd))

    app.add_handler(CommandHandler("mylink", mylink_cmd, filters=PRIVATE))
    app.add_handler(CommandHandler("unlink", unlink_cmd, filters=PRIVATE))
    app.add_handler(CommandHandler("remanga", remanga_cmd, filters=PRIVATE))
    app.add_handler(CommandHandler("where", where_cmd, filters=PRIVATE))

    # Данные из WebApp
    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA & PRIVATE, on_webapp_data))

    # В ЛС — никаких «ловушек всего»: бот молчит, если это не команда.
    # Во всех НЕ приватных чатах — подсказка перейти в ЛС:
    app.add_handler(MessageHandler(~PRIVATE, not_private))

    log.info("Bot is running. Commands: /start /tap /register /mylink /unlink /remanga /where")
    app.run_polling()

if __name__ == "__main__":
    main()
