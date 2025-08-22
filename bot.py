# bot_combined.py — WebApp «тапалка» + привязка ReManga + пагинация + авто-пуш JSON в GitHub без job-queue
# Требования:
#   python-telegram-bot==21.*
#   Pillow
#   aiohttp

import asyncio
import base64
import json
import logging
import os
import random
import re
import sys
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
from PIL import Image
from telegram import (
    Update,
    WebAppInfo,
    KeyboardButton,
    ReplyKeyboardMarkup,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    InputMediaPhoto,
    InputFile,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ───────────────────────── НАСТРОЙКИ ─────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN", "8380517379:AAF1pCJKN2uz2YL86yw_wKcFHGy_oFmvOjQ").strip()
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://sait-ama.github.io/eternal/index.html").strip() or "https://example.com/index.html"

# где лежат локальные JSON (бот ЧИТАЕТ отсюда)
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("REMANGA_DATA_DIR", r"C:\Users\User\Desktop\Remanga\EW"))

HISTORY_EW_FILE = Path(os.getenv("HISTORY_EW_FILE", str(DATA_DIR / "history_ew.json")))
HISTORY_ED_FILE = Path(os.getenv("HISTORY_ED_FILE", str(DATA_DIR / "history_ed.json")))
TOP10_FILE      = Path(os.getenv("TOP10_FILE",      str(DATA_DIR / "top10.json")))

# список файлов для поиска профиля (бот читает ЛОКАЛЬНО)
REMANGA_DATA_FILES = [
    HISTORY_EW_FILE,
    HISTORY_ED_FILE,
    DATA_DIR / "history_e.json",  # если есть
]

LINKS_FILE = DATA_DIR / "user_links.json"
SAVE_FILE  = DATA_DIR / "tap_saves.json"

# лог/медиа
LONG_DELETE_DELAY = 300
SHORT_DELETE_DELAY = 1
ITEMS_PER_PAGE = 4
PLACEHOLDER = str(DATA_DIR / "no_avatar.jpg")
LOG_FILE = str(DATA_DIR / "bot.log")

BENYA_DIR = BASE_DIR / "33"
KRYA_DIR  = BASE_DIR / "44"
BENYA_PHOTOS = sorted(BENYA_DIR.glob("*.jpg"))
KRYA_PHOTOS  = sorted(KRYA_DIR.glob("*.jpg"))

# ── GitHub выгрузка ──────────────────────────────────────────
GITHUB_TOKEN        = os.getenv("GITHUB_TOKEN", "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").strip()         # REQUIRED
GITHUB_REPO         = os.getenv("GITHUB_REPO",  "sait-ama/eternal").strip()
GITHUB_BRANCH       = os.getenv("GITHUB_BRANCH", "main").strip()
GITHUB_PATH_PREFIX  = os.getenv("GITHUB_PATH_PREFIX", "").strip()   # например "data"

# ───────────────────────── ЛОГИРОВАНИЕ ───────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler(LOG_FILE, encoding="utf-8")]
)
log = logging.getLogger("bot")

# ───────────────────────── ПРОВЕРКИ ──────────────────────────
if not re.fullmatch(r"\d{6,}:[A-Za-z0-9_-]{30,}", BOT_TOKEN or ""):
    print("❌ Некорректный BOT_TOKEN. Укажи реальный токен в переменной окружения BOT_TOKEN.")
    sys.exit(1)

if not WEBAPP_URL.startswith("https://"):
    log.warning("⚠ WEBAPP_URL должен начинаться с https:// Сейчас: %s", WEBAPP_URL)

# ───────────────────────── УТИЛИТЫ JSON ──────────────────────
def read_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("JSON read failed for %s: %s", path, e)
    return default

def write_json(path: Path, data: Any) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        log.warning("JSON write failed for %s: %s", path, e)

def load_json_array(path: Path) -> List[dict]:
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        else:
            log.warning("JSON not found: %s", path)
            return []
    except Exception as e:
        log.warning("Can't load JSON %s: %s", path, e)
        return []

# ───────────────────────── ReManga поиск ─────────────────────
def normalize_profile_url(url: str) -> Optional[str]:
    if not isinstance(url, str):
        return None
    url = url.strip()
    m = re.match(r"^https?://(?:www\.)?remanga\.org/user/(\d+)/(?:about/?)?$", url, re.IGNORECASE)
    if not m:
        return None
    user_id = m.group(1)
    return f"https://remanga.org/user/{user_id}/about"

def load_all_remanga_records() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for p in REMANGA_DATA_FILES:
        data = read_json(Path(p), default=[])
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

# ───────────────────────── Кнопки WebApp ─────────────────────
async def send_reply_keyboard(update: Update):
    kb_reply = ReplyKeyboardMarkup(
        [[KeyboardButton(text="Запустить игру (WebApp)", web_app=WebAppInfo(url=WEBAPP_URL))]],
        resize_keyboard=True
    )
    await update.message.reply_text("Открой игру кнопкой ниже ⤵️", reply_markup=kb_reply)

# ───────────────────────── Хэндлеры «тапалки» ────────────────
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_reply_keyboard(update)

async def tap_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_reply_keyboard(update)

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
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "name": update.effective_user.full_name if update.effective_user else "",
            "username": update.effective_user.username if update.effective_user else "",
        }
        write_json(SAVE_FILE, saves)
        if update.message:
            await update.message.reply_text("Состояние сохранено на сервере ✅")
        return

    if update.message:
        await update.message.reply_text("WebApp: данные получены.")

# ───────────────────────── Привязка ReManga ──────────────────
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

    play_url = f"{WEBAPP_URL}?profile={norm}"
    play_kb = InlineKeyboardMarkup([[InlineKeyboardButton(text="Запустить игру (WebApp)", web_app=WebAppInfo(url=play_url))]])

    row = find_profile_in_all(norm)
    if row:
        await reply_remanga_card(msg, row, prefix="✅ Профиль привязан.\n")
        await msg.reply_text("Открой игру с привязанным профилем:", reply_markup=play_kb)
    else:
        missing = ", ".join(str(p) for p in REMANGA_DATA_FILES if not Path(p).exists())
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
        await update.effective_message.reply_text("В подключённых JSON не найден твой профиль. Проверь поле profile.")
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

def load_links() -> Dict[str, str]:
    return read_json(LINKS_FILE, default={})

def save_links(links: Dict[str, str]) -> None:
    write_json(LINKS_FILE, links)

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

# ───────────────────────── Не приватные чаты ─────────────────
async def not_private(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_message:
        await update.effective_message.reply_text(
            "Эта мини-игра и привязка ReManga доступны только в ЛИЧНОМ чате. "
            "Открой диалог с ботом и отправь /start."
        )

# ───────────────────────── Картинки / утилиты ────────────────
MAX_W, MAX_H = 2048, 2048
JPEG_QUALITY = 82

def ensure_placeholder():
    p = Path(PLACEHOLDER)
    if p.exists():
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    png_b64 = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGMAAQAABQAB"
        "DQottAAAAABJRU5ErkJggg=="
    )
    try:
        with open(p, "wb") as f:
            f.write(base64.b64decode(png_b64))
        log.info("Placeholder created: %s", PLACEHOLDER)
    except Exception as e:
        log.exception("Failed to create placeholder: %s", e)

def sanitize_to_jpeg_bytes(local_path: Path) -> bytes:
    with open(local_path, "rb") as f:
        raw = f.read()
    im = Image.open(BytesIO(raw))
    try:
        im.load()
    except Exception:
        pass
    if im.mode != "RGB":
        im = im.convert("RGB")
    w, h = im.size
    if w > MAX_W or h > MAX_H:
        im.thumbnail((MAX_W, MAX_H), Image.LANCZOS)
    out = BytesIO()
    im.save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True, progressive=False, subsampling="4:2:0")
    return out.getvalue()

last_messages: dict[Tuple[int, Optional[int]], List[int]] = {}

async def delete_messages(bot, chat_id: int, message_ids: List[int]):
    if not message_ids:
        return
    for mid in message_ids:
        try:
            await bot.delete_message(chat_id, mid)
        except Exception as e:
            log.warning("Failed to delete message %s in chat %s: %s", mid, chat_id, e)

async def schedule_delete(bot, chat_id: int, message_ids: List[int], delay: float):
    await asyncio.sleep(delay)
    await delete_messages(bot, chat_id, message_ids)

# ───────────────────────── ASCII ─────────────────────────────
ascii_art = {
    "КОТИК": "⣴⡿⠶⠀⠀⠀⣦⣀⣴⠀⠀⠀⠀\n⣿⡄⠀⠀⣠⣾⠛⣿⠛⣷⠀⠿⣦ \n⠙⣷⣦⣾⣿⣿⣿⣿⣿⠟⠀⣴⣿\n⠀⣸⣿⣿⣿⣿⣿⣿⣿⣾⠿⠋⠁\n⠀⣿⣿⣿⠿⡿⣿⣿⡿⠀⠀⠀⠀\n⢸⣿⡋⠀⠀⠀⢹⣿⡇⠀⠀⠀⠀\n⣿⡟⠀⠀⠀⠀⠀⢿⡇",
    "УТКА": "Утка\n┈┈┈╱╱\n┈┈╱╱╱▔\n┈╱╭┈▔▔╲\n▕▏┊╱╲┈╱▏\n▕▏▕╮▕▕╮▏\n▕▏▕▋▕▕▋▏\n╱▔▔╲╱▔▔╲╮┈┈╱▔▔╲\n▏▔▏┈┈▔┈┈▔▔▔╱▔▔╱\n╲┈╲┈┈┈┈┈┈┈╱▔▔╔\n┈▔╲╲▂▂▂▂▂╱\n┈┈▕━━▏\n┈┈▕━━▏\n╱▔▔┈┈▔▔╲",
    "ПИНГВИН": "．　　＿.＿\n．　/######\\\n． (##### @ ######\\\n． /‘　\\######’ーー乛\n．/　　\\####(\n- /##　　'乛’ ＼\n-/####\\　　　　\\\n’/######\\\n|#######　　　;\n|########　　丿\n|### '####　　/\n|###　'###　 ;\n|### 　##/　;\n|###　''　　/\n####　　／ \n/###　　乀\n‘#/_______,)),）",
    "СОБАКА": "╱▔▔╲▂▂▂╱▔▔╲\n╲╱╳╱▔╲╱▔╲╱▔\n┈┈┃▏▕▍▏▕▍▏\n┈┈┃╲▂╱╲▂▱╲┈╭━╮\n┈┈┃┊┳┊┊┊┊┊▔╰┳╯\n┈┈┃┊╰━━━┳━━━╯\n┈┈┃┊┊┊┊╭╯"
}

# ───────────────────────── Пагинация ────────────────────────
def format_member(member: dict, index: int) -> str:
    profile = member.get("profile", "")
    display = member.get("display", "")
    diff = member.get("diff", 0)
    return (f"<b>{index}.</b> <a href='{profile}'>{display}</a>\n"
            f"<b>Прирост:</b> {diff:,} ⚡").replace(",", " ")

def get_avatar_path(member: dict) -> Path:
    avatar = member.get("avatar", "")
    if avatar:
        p = Path(avatar)
        if not p.is_absolute():
            p = DATA_DIR / p
        if p.is_file() and p.stat().st_size > 0 and p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
            return p
    return Path(PLACEHOLDER)

async def send_page(origin, guild_key: str, page: int, context: ContextTypes.DEFAULT_TYPE, from_callback: bool):
    bot = context.bot

    if guild_key == "ЕВ":
        data = load_json_array(HISTORY_EW_FILE)
    elif guild_key == "ЕД":
        data = load_json_array(HISTORY_ED_FILE)
    elif guild_key == "ТОП10":
        data = load_json_array(TOP10_FILE)
    else:
        data = []

    if not data:
        try:
            if hasattr(origin, "message") and origin.message:
                await origin.message.reply_text("Данные не найдены.")
            else:
                await origin.edit_message_text("Данные не найдены.")
        except Exception:
            pass
        return

    if hasattr(origin, "message") and origin.message:
        chat_id = origin.message.chat.id
        thread_id = getattr(origin.message, "message_thread_id", None)
    else:
        chat_id = origin.message.chat.id
        thread_id = getattr(origin.message, "message_thread_id", None)

    key = (chat_id, thread_id)

    if from_callback:
        old = last_messages.get(key)
        if old:
            asyncio.create_task(schedule_delete(bot, chat_id, old.copy(), SHORT_DELETE_DELAY))
            last_messages[key] = []

    new_ids: List[int] = []
    thread_kwargs = {"message_thread_id": thread_id} if thread_id is not None else {}

    try:
        if guild_key != "ТОП10":
            total_pages = max((len(data) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE, 1)
            page = page % total_pages
            start = page * ITEMS_PER_PAGE
            end = start + ITEMS_PER_PAGE
            members = data[start:end]

            media: List[InputMediaPhoto] = []
            for idx, m in enumerate(members, start=1):
                path = get_avatar_path(m)
                try:
                    jpg_bytes = sanitize_to_jpeg_bytes(path)
                except Exception:
                    jpg_bytes = sanitize_to_jpeg_bytes(Path(PLACEHOLDER))
                fname = f"ava_{start+idx}.jpg"
                media.append(InputMediaPhoto(media=InputFile(jpg_bytes, filename=fname)))

            sent_ids: List[int] = []
            if len(media) >= 2:
                try:
                    msgs = await bot.send_media_group(chat_id=chat_id, media=media[:10], **thread_kwargs)
                    sent_ids = [m.message_id for m in msgs]
                except Exception:
                    for item in media:
                        try:
                            msg = await bot.send_photo(chat_id=chat_id, photo=item.media, **thread_kwargs)
                            sent_ids.append(msg.message_id)
                        except Exception:
                            pass
            else:
                for item in media:
                    try:
                        msg = await bot.send_photo(chat_id=chat_id, photo=item.media, **thread_kwargs)
                        sent_ids.append(msg.message_id)
                    except Exception:
                        pass

            new_ids.extend(sent_ids)

            captions = [format_member(m, idx) for idx, m in enumerate(members, start=start + 1)]
            text_block = "\n\n".join(captions)

            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("⬅", callback_data=f"{guild_key}|{page-1}"),
                InlineKeyboardButton("🔄", callback_data=f"{guild_key}|refresh|{page}"),
                InlineKeyboardButton("➡", callback_data=f"{guild_key}|{page+1}")
            ]])
            nav_msg = await bot.send_message(chat_id=chat_id, text=text_block, parse_mode="HTML",
                                             reply_markup=keyboard, **thread_kwargs)
            new_ids.append(nav_msg.message_id)

        else:
            text = "🏆 <b>Топ 10 по вкладу</b> 🏆\n\n"
            for i, member in enumerate(data, start=1):
                text += f"{i}. <a href='{member.get('profile','')}'>{member.get('display','')}</a> — {member.get('diff',0):,} ⚡\n"
            text = text.replace(",", " ")
            msg_top = await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML", **thread_kwargs)
            new_ids.append(msg_top.message_id)

    except Exception:
        log.exception("Failed to send page")

    # удаляем командное сообщение пользователя (если это не callback)
    try:
        is_callback = hasattr(origin, "data")
        if not is_callback and hasattr(origin, "message") and origin.message:
            user_cmd_mid = origin.message.message_id
            asyncio.create_task(
                schedule_delete(bot, chat_id, [user_cmd_mid], LONG_DELETE_DELAY)
            )
    except Exception as e:
        log.exception("Error scheduling deletion of command msg: %s", e)

    last_messages[key] = new_ids.copy()

    try:
        asyncio.create_task(
            schedule_delete(bot, chat_id, new_ids.copy(), LONG_DELETE_DELAY)
        )
    except Exception as e:
        log.exception("Failed to schedule long delete for new messages: %s", e)

# ───────────────────────── Обработчик текста ─────────────────
async def handle_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip().upper()

    if text in ("!ЕВ", "!ЕД", "!ТОП10"):
        guild_key = text.replace("!", "")
        await send_page(update, guild_key, 0, context, from_callback=False)
        return

    if text == "ИДИ НАХУЙ":
        msg = await update.message.reply_text("Сам иди нахуй")
        asyncio.create_task(schedule_delete(context.bot, update.message.chat.id,
                                            [update.message.message_id, msg.message_id], LONG_DELETE_DELAY))
        return

    if text in ("БАН", "КИК"):
        msg = await update.message.reply_text("-1")
        asyncio.create_task(schedule_delete(context.bot, update.message.chat.id,
                                            [update.message.message_id, msg.message_id], LONG_DELETE_DELAY))
        return

    if text in ("БУБА", "BUBA"):
        msg = await update.message.reply_text("Не призывай сатану!")
        asyncio.create_task(schedule_delete(context.bot, update.message.chat.id,
                                            [update.message.message_id, msg.message_id], LONG_DELETE_DELAY))
        return

    if text == "БЕНЯ":
        if not BENYA_PHOTOS:
            msg = await update.message.reply_text("Папка 33 пуста или картинки не найдены.")
            asyncio.create_task(schedule_delete(context.bot, update.message.chat.id,
                                                [update.message.message_id, msg.message_id], LONG_DELETE_DELAY))
            return
        photo_path = random.choice(BENYA_PHOTOS)
        with open(photo_path, "rb") as f:
            msg = await update.message.reply_photo(f)
        asyncio.create_task(schedule_delete(context.bot, update.message.chat.id,
                                            [update.message.message_id, msg.message_id], LONG_DELETE_DELAY))
        return

    if text == "КРЯ":
        if not KRYA_PHOTOS:
            msg = await update.message.reply_text("Папка 44 пуста или картинки не найдены.")
            asyncio.create_task(schedule_delete(context.bot, update.message.chat.id,
                                                [update.message.message_id, msg.message_id], LONG_DELETE_DELAY))
        else:
            photo_path = random.choice(KRYA_PHOTOS)
            with open(photo_path, "rb") as f:
                msg = await update.message.reply_photo(f)
            asyncio.create_task(schedule_delete(context.bot, update.message.chat.id,
                                                [update.message.message_id, msg.message_id], LONG_DELETE_DELAY))
        return

    if text in ascii_art:
        msg = await update.message.reply_text(ascii_art[text])
        asyncio.create_task(schedule_delete(context.bot, update.message.chat.id,
                                            [update.message.message_id, msg.message_id], LONG_DELETE_DELAY))
        return

# ───────────────────────── Callback навигации ─────────────────
async def page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    await query.answer()
    data = query.data or ""
    if "|" not in data:
        return
    parts = data.split("|")
    guild_key = parts[0]
    if len(parts) >= 2 and parts[1] == "refresh":
        page = int(parts[2]) if len(parts) > 2 else 0
        await send_page(query, guild_key, page, context, from_callback=True)
    else:
        page = int(parts[1]) if len(parts) > 1 else 0
        await send_page(query, guild_key, page, context, from_callback=True)

# ───────────────────────── GitHub helper'ы ───────────────────
def _dst_path(name: str) -> str:
    if GITHUB_PATH_PREFIX:
        prefix = GITHUB_PATH_PREFIX.strip().strip("/").replace("\\", "/")
        return f"{prefix}/{name}"
    return name

async def _gh_get_sha(session: aiohttp.ClientSession, repo: str, path: str, branch: str) -> Optional[str]:
    url = f"https://api.github.com/repos/{repo}/contents/{path}"
    params = {"ref": branch}
    async with session.get(url, params=params) as resp:
        if resp.status == 200:
            data = await resp.json()
            return data.get("sha")
        elif resp.status == 404:
            return None
        else:
            text = await resp.text()
            log.warning("GET sha %s -> %s %s", path, resp.status, text)
            return None

async def _gh_put_file(session: aiohttp.ClientSession, repo: str, path: str, branch: str, token: str, content_bytes: bytes, message: str, sha: Optional[str]) -> bool:
    url = f"https://api.github.com/repos/{repo}/contents/{path}"
    payload = {
        "message": message,
        "content": base64.b64encode(content_bytes).decode("ascii"),
        "branch": branch,
    }
    if sha:
        payload["sha"] = sha
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }
    async with session.put(url, headers=headers, json=payload) as resp:
        ok = resp.status in (200, 201)
        if not ok:
            text = await resp.text()
            log.warning("PUT %s -> %s %s", path, resp.status, text)
        return ok

def _read_bytes(path: Path) -> Optional[bytes]:
    try:
        return path.read_bytes()
    except Exception as e:
        log.warning("Can't read %s: %s", path, e)
        return None

async def push_to_github(reply_chat_id: Optional[int], bot) -> None:
    if not GITHUB_TOKEN:
        msg = "GITHUB_TOKEN не задан — выгрузка пропущена."
        log.warning(msg)
        if reply_chat_id:
            try:
                await bot.send_message(chat_id=reply_chat_id, text=msg)
            except Exception:
                pass
        return

    files_to_push = [
        ("history_ew.json", HISTORY_EW_FILE),
        ("history_ed.json", HISTORY_ED_FILE),
        ("top10.json",      TOP10_FILE),
    ]

    results: List[str] = []
    async with aiohttp.ClientSession() as session:
        for name, local_path in files_to_push:
            dst = _dst_path(name)
            data = _read_bytes(local_path)
            if data is None:
                results.append(f"❌ {name}: не прочитан {local_path}")
                continue
            sha = await _gh_get_sha(session, GITHUB_REPO, dst, GITHUB_BRANCH)
            msg = f"auto: update {name} at {datetime.now(timezone.utc).isoformat()}"
            ok = await _gh_put_file(session, GITHUB_REPO, dst, GITHUB_BRANCH, GITHUB_TOKEN, data, msg, sha)
            results.append(("✅" if ok else "❌") + f" {name} -> {GITHUB_REPO}:{GITHUB_BRANCH}/{dst}")

    text = "Синхронизация с GitHub:\n" + "\n".join(results)
    log.info(text)
    if reply_chat_id:
        try:
            await bot.send_message(chat_id=reply_chat_id, text=text)
        except Exception:
            pass

# фоновая задача без job-queue
async def periodic_github_sync(bot):
    await asyncio.sleep(10)  # первый запуск через 10 сек
    while True:
        try:
            await push_to_github(None, bot)
        except Exception as e:
            log.exception("periodic sync failed: %s", e)
        await asyncio.sleep(300)  # каждые 5 минут

# ручная команда для мгновенной синхронизации
async def sync_now_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await push_to_github(update.effective_chat.id, context.bot)

# ───────────────────────── post_init ─────────────────────────
async def post_init(app: Application):
    # стартуем фоновую автосинхронизацию без job-queue
    app.create_task(periodic_github_sync(app.bot))

# ───────────────────────── MAIN ──────────────────────────────
def main():
    ensure_placeholder()

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    PRIVATE = filters.ChatType.PRIVATE
    GROUPS  = filters.ChatType.GROUPS

    # ЛС команды
    app.add_handler(CommandHandler("start", start_cmd, filters=PRIVATE))
    app.add_handler(CommandHandler("tap", tap_cmd, filters=PRIVATE))
    app.add_handler(CommandHandler(["register", "link", "registraciya"], register_cmd, filters=PRIVATE))
    app.add_handler(MessageHandler(PRIVATE & filters.Regex(r"^/(?:регистрация|привязка)(?:@\w+)?(?:\s+.*)?$"), register_cmd))
    app.add_handler(CommandHandler("mylink", mylink_cmd, filters=PRIVATE))
    app.add_handler(CommandHandler("unlink", unlink_cmd, filters=PRIVATE))
    app.add_handler(CommandHandler("remanga", remanga_cmd, filters=PRIVATE))
    app.add_handler(CommandHandler("where", where_cmd, filters=PRIVATE))
    app.add_handler(CommandHandler("sync_now", sync_now_cmd, filters=PRIVATE))  # ручная выгрузка

    # Группы — вежливо отправляем в ЛС
    only_tapper_cmds = r"^/(?:start|tap|register|link|registraciya|регистрация|привязка|mylink|unlink|remanga|where|sync_now)(?:@\w+)?\b"
    app.add_handler(MessageHandler(GROUPS & filters.Regex(only_tapper_cmds), not_private))

    # WebApp данные
    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA & PRIVATE, on_webapp_data))

    # Текст/триггеры
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_command))

    # Навигация
    app.add_handler(CallbackQueryHandler(page_callback))

    log.info("Bot is running. JSON push to GitHub every 5 minutes enabled (asyncio task).")
    app.run_polling()

if __name__ == "__main__":
    main()
