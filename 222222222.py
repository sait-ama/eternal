# bot_combined.py — единый бот: WebApp «тапалка» + привязка ReManga + постинг/пагинация из JSON, БЕНЯ/КРЯ и пр.
# Требования:
#   python-telegram-bot==21.*
#   Pillow

import asyncio
import base64
import json
import logging
import os
import random
import re
import sys
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

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

# ────────────────────────────── НАСТРОЙКИ ─────────────────────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN", "8380517379:AAF1pCJKN2uz2YL86yw_wKcFHGy_oFmvOjQ").strip()
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://sait-ama.github.io/eternal/").strip() or "https://example.com/index.html"

# БАЗОВЫЕ ПУТИ К ДАННЫМ (НОВОЕ)
BASE_DIR = Path(__file__).resolve().parent
# Можно задать ОДНУ папку для всех файлов:
REMANGA_DATA_DIR_ENV = os.getenv("REMANGA_DATA_DIR", "C:\Users\User\Desktop\Remanga\EW").strip()
DATA_DIR = Path(REMANGA_DATA_DIR_ENV) if REMANGA_DATA_DIR_ENV else BASE_DIR

# Или задать ТРИ/ОДИН отдельный путь(я) (каждый может быть абсолютным или относительным)
HISTORY_EW_FILE_ENV = os.getenv("HISTORY_EW_FILE", "C:\Users\User\Desktop\Remanga\EW").strip()
HISTORY_ED_FILE_ENV = os.getenv("HISTORY_ED_FILE", "C:\Users\User\Desktop\Remanga\EW").strip()
TOP10_FILE_ENV       = os.getenv("TOP10_FILE", "C:\Users\User\Desktop\Remanga\EW").strip()

def _resolve_path(p: str | Path) -> Path:
    """Абсолютные пути оставляем как есть; относительные — считаем относительно DATA_DIR."""
    p = Path(p)
    return p if p.is_absolute() else (DATA_DIR / p)

HISTORY_EW_FILE = _resolve_path(HISTORY_EW_FILE_ENV) if HISTORY_EW_FILE_ENV else (DATA_DIR / "history_ew.json")
HISTORY_ED_FILE = _resolve_path(HISTORY_ED_FILE_ENV) if HISTORY_ED_FILE_ENV else (DATA_DIR / "history_ed.json")
TOP10_FILE      = _resolve_path(TOP10_FILE_ENV)      if TOP10_FILE_ENV      else (DATA_DIR / "top10.json")

# Список JSON для поиска профиля ReManga.
# Можно задать REMANGA_DATA_FILES="C:\a\ew.json, D:\b\ed.json, history_e.json"
REMANGA_DATA_FILES_ENV = os.getenv("REMANGA_DATA_FILES", "").strip()
if REMANGA_DATA_FILES_ENV:
    REMANGA_DATA_FILES: List[Path] = []
    for chunk in REMANGA_DATA_FILES_ENV.split(","):
        q = chunk.strip()
        if q:
            REMANGA_DATA_FILES.append(_resolve_path(q))
else:
    # дефолтный набор
    REMANGA_DATA_FILES = [HISTORY_EW_FILE, HISTORY_ED_FILE, DATA_DIR / "history_e.json"]

LINKS_FILE = DATA_DIR / "user_links.json"   # можно тоже вынести в ту же папку
SAVE_FILE  = DATA_DIR / "tap_saves.json"

# Настройки блока «пагинации/фото»
LONG_DELETE_DELAY = 300
SHORT_DELETE_DELAY = 1
ITEMS_PER_PAGE = 4
PLACEHOLDER = str(DATA_DIR / "no_avatar.jpg")  # placeholder можем хранить в папке с данными
LOG_FILE = str(DATA_DIR / "bot.log")

# Папки с фотками для БЕНЯ и КРЯ (оставил рядом со скриптом; при желании можно вынести аналогично)
BENYA_DIR = BASE_DIR / "33"
KRYA_DIR  = BASE_DIR / "44"
BENYA_PHOTOS = sorted(BENYA_DIR.glob("*.jpg"))
KRYA_PHOTOS  = sorted(KRYA_DIR.glob("*.jpg"))

# ────────────────────────────── ЛОГИРОВАНИЕ ──────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler(LOG_FILE, encoding="utf-8")]
)
log = logging.getLogger("bot")

# ────────────────────────────── ПРОВЕРКИ БАЗОВЫЕ ──────────────────────────────────
if not re.fullmatch(r"\d{6,}:[A-Za-z0-9_-]{30,}", BOT_TOKEN or ""):
    print("❌ Некорректный BOT_TOKEN. Укажи реальный токен в переменной окружения BOT_TOKEN.")
    sys.exit(1)

if not WEBAPP_URL.startswith("https://"):
    log.warning("⚠ WEBAPP_URL должен начинаться с https:// Сейчас: %s", WEBAPP_URL)

# ────────────────────────────── УТИЛИТЫ JSON ─────────────────────────────────────
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

# ────────────────────────────── NORMALIZE ReManga ────────────────────────────────
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

# ────────────────────────────── ПРИВЯЗКИ user_id -> profile ─────────────────────
def load_links() -> Dict[str, str]:
    return read_json(LINKS_FILE, default={})

def save_links(links: Dict[str, str]) -> None:
    write_json(LINKS_FILE, links)

# ────────────────────────────── КНОПКИ WEBAPP ────────────────────────────────────
async def send_reply_keyboard(update: Update):
    kb_reply = ReplyKeyboardMarkup(
        [[KeyboardButton(text="Запустить игру (WebApp)", web_app=WebAppInfo(url=WEBAPP_URL))]],
        resize_keyboard=True
    )
    await update.message.reply_text("Открой игру кнопкой ниже ⤵️", reply_markup=kb_reply)

# ────────────────────────────── ХЭНДЛЕРЫ ТАПАЛКИ ─────────────────────────────────
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

    # Кнопка «Запустить игру (WebApp)» с привязкой
    play_url = f"{WEBAPP_URL}?profile={quote(norm)}"
    play_kb = InlineKeyboardMarkup([[InlineKeyboardButton(text="Запустить игру (WebApp)", web_app=WebAppInfo(url=play_url))]])

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

# ─────────────────────── ОГРАНИЧИТЕЛЬ ДЛЯ НЕПРИВАТНЫХ ЧАТОВ ─────────────────────
async def not_private(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_message:
        await update.effective_message.reply_text(
            "Эта мини-игра и привязка ReManga доступны только в ЛИЧНОМ чате. "
            "Открой диалог с ботом и отправь /start."
        )

# ───────────────────────── Нормализация изображений (Pillow) ─────────────────────
MAX_W, MAX_H = 2048, 2048
JPEG_QUALITY = 82

def ensure_placeholder():
    if os.path.exists(PLACEHOLDER):
        return
    png_b64 = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGMAAQAABQAB"
        "DQottAAAAABJRU5ErkJggg=="
    )
    try:
        Path(PLACEHOLDER).parent.mkdir(parents=True, exist_ok=True)
        with open(PLACEHOLDER, "wb") as f:
            f.write(base64.b64decode(png_b64))
        log.info("Placeholder created: %s", PLACEHOLDER)
    except Exception as e:
        log.exception("Failed to create placeholder: %s", e)

def sanitize_to_jpeg_bytes(img_path: str | Path) -> bytes:
    p = Path(img_path)
    with open(p, "rb") as f:
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

# ───────────────────────── Хранилище последних сообщений ─────────────────────────
last_messages: dict[Tuple[int, Optional[int]], List[int]] = {}

# ───────────────────────── Вспомогалки для пагинации ─────────────────────────────
def format_member(member: dict, index: int) -> str:
    profile = member.get("profile", "")
    display = member.get("display", "")
    diff = member.get("diff", 0)
    return (f"<b>{index}.</b> <a href='{profile}'>{display}</a>\n"
            f"<b>Прирост:</b> {diff:,} ⚡").replace(",", " ")

def get_avatar_path(member: dict) -> str:
    avatar = member.get("avatar", "")
    if avatar:
        p = Path(avatar)
        p = p if p.is_absolute() else (DATA_DIR / p)  # относительные аватары берём от DATA_DIR
        if p.is_file() and p.stat().st_size > 0 and p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
            return str(p)
    return PLACEHOLDER

async def delete_messages(bot, chat_id: int, message_ids: List[int]):
    if not message_ids:
        return
    log.info("Deleting messages in chat=%s : %s", chat_id, message_ids)
    for mid in message_ids:
        try:
            await bot.delete_message(chat_id, mid)
        except Exception as e:
            log.warning("Failed to delete message %s in chat %s: %s", mid, chat_id, e)

async def schedule_delete(bot, chat_id: int, message_ids: List[int], delay: float):
    await asyncio.sleep(delay)
    await delete_messages(bot, chat_id, message_ids)

# ───────────────────────── ASCII-арт ─────────────────────────────────────────────
ascii_art = {
    "КОТИК": """⣴⡿⠶⠀⠀⠀⣦⣀⣴⠀⠀⠀⠀
⣿⡄⠀⠀⣠⣾⠛⣿⠛⣷⠀⠿⣦ 
⠙⣷⣦⣾⣿⣿⣿⣿⣿⠟⠀⣴⣿
⠀⣸⣿⣿⣿⣿⣿⣿⣿⣾⠿⠋⠁
⠀⣿⣿⣿⠿⡿⣿⣿⡿⠀⠀⠀⠀
⢸⣿⡋⠀⠀⠀⢹⣿⡇⠀⠀⠀⠀
⣿⡟⠀⠀⠀⠀⠀⢿⡇""",
    "УТКА": """Утка
┈┈┈╱╱
┈┈╱╱╱▔
┈╱╭┈▔▔╲
▕▏┊╱╲┈╱▏
▕▏▕╮▕▕╮▏
▕▏▕▋▕▕▋▏
╱▔▔╲╱▔▔╲╮┈┈╱▔▔╲
▏▔▏┈┈▔┈┈▔▔▔╱▔▔╱
╲┈╲┈┈┈┈┈┈┈╱▔▔▔
┈▔╲╲▂▂▂▂▂╱
┈┈▕━━▏
┈┈▕━━▏
╱▔▔┈┈▔▔╲""",
    "ПИНГВИН": """．　　＿.＿
．　/######\\
． (##### @ ######\\
． /‘　\\######’ーー乛
．/　　\\####(
- /##　　'乛’ ＼
-/####\\　　　　\\
’/######\\
|#######　　　;
|########　　丿
|### '####　　/
|###　'###　 ;
|### 　##/　;
|###　''　　/
####　　／ 
/###　　乀
‘#/_______,)),）""",
    "СОБАКА": """╱▔▔╲▂▂▂╱▔▔╲
╲╱╳╱▔╲╱▔╲╱▔
┈┈┃▏▕▍▏▕▍▏
┈┈┃╲▂╱╲▂▱╲┈╭━╮
┈┈┃┊┳┊┊┊┊┊▔╰┳╯
┈┈┃┊╰━━━┳━━━╯
┈┈┃┊┊┊┊╭╯"""
}

# ───────────────────────── Основная логика «страниц» ─────────────────────────────
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
        chat = origin.message.chat
        chat_id = chat.id
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
            payloads: List[Tuple[str, bytes]] = []

            for idx, m in enumerate(members, start=1):
                path = get_avatar_path(m)
                try:
                    jpg_bytes = sanitize_to_jpeg_bytes(path)
                except Exception as e:
                    log.warning("Image sanitize failed for %s (%s). Using placeholder.", path, e)
                    jpg_bytes = sanitize_to_jpeg_bytes(PLACEHOLDER)

                fname = f"ava_{start+idx}.jpg"
                payloads.append((fname, jpg_bytes))
                media.append(InputMediaPhoto(media=InputFile(jpg_bytes, filename=fname)))

            sent_ids: List[int] = []
            if len(media) >= 2:
                try:
                    msgs = await bot.send_media_group(chat_id=chat_id, media=media[:10], **thread_kwargs)
                    sent_ids = [m.message_id for m in msgs]
                except Exception as e:
                    log.warning("send_media_group failed: %s. Fallback to single photos.", e)
                    for fname, jpg_bytes in payloads:
                        try:
                            msg = await bot.send_photo(chat_id=chat_id, photo=InputFile(jpg_bytes, filename=fname), **thread_kwargs)
                            sent_ids.append(msg.message_id)
                        except Exception as ee:
                            log.error("send_photo failed for %s: %s", fname, ee)
            else:
                for fname, jpg_bytes in payloads:
                    try:
                        msg = await bot.send_photo(chat_id=chat_id, photo=InputFile(jpg_bytes, filename=fname), **thread_kwargs)
                        sent_ids.append(msg.message_id)
                    except Exception as ee:
                        log.error("send_photo failed for %s: %s", fname, ee)

            new_ids.extend(sent_ids)

            captions = [format_member(m, idx) for idx, m in enumerate(members, start=start + 1)]
            text_block = "\n\n".join(captions)

            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("⬅", callback_data=f"{guild_key}|{page-1}"),
                    InlineKeyboardButton("🔄", callback_data=f"{guild_key}|refresh|{page}"),
                    InlineKeyboardButton("➡", callback_data=f"{guild_key}|{page+1}")
                ]
            ])
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

    try:
        is_callback = hasattr(origin, "data")
        if not is_callback and hasattr(origin, "message") and origin.message:
            user_cmd_mid = origin.message.message_id
            asyncio.create_task(schedule_delete(bot, chat_id, [user_cmd_mid], LONG_DELETE_DELAY))
    except Exception:
        log.exception("Error scheduling deletion of command msg")

    last_messages[key] = new_ids.copy()

    try:
        asyncio.create_task(schedule_delete(bot, chat_id, new_ids.copy(), LONG_DELETE_DELAY))
    except Exception:
        log.exception("Failed to schedule long delete for new messages")

# ───────────────────────── Хэндлер текстовых команд (в т.ч. группы) ─────────────
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
            return
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

# ─────────────────────────── MAIN ────────────────────────────────────────────────
def main():
    ensure_placeholder()

    app = Application.builder().token(BOT_TOKEN).build()

    PRIVATE = filters.ChatType.PRIVATE
    GROUPS  = filters.ChatType.GROUPS

    # Команды «тапалки» — только ЛС
    app.add_handler(CommandHandler("start", start_cmd, filters=PRIVATE))
    app.add_handler(CommandHandler("tap", tap_cmd, filters=PRIVATE))
    app.add_handler(CommandHandler(["register", "link", "registraciya"], register_cmd, filters=PRIVATE))
    app.add_handler(MessageHandler(PRIVATE & filters.Regex(r"^/(?:регистрация|привязка)(?:@\w+)?(?:\s+.*)?$"), register_cmd))
    app.add_handler(CommandHandler("mylink", mylink_cmd, filters=PRIVATE))
    app.add_handler(CommandHandler("unlink", unlink_cmd, filters=PRIVATE))
    app.add_handler(CommandHandler("remanga", remanga_cmd, filters=PRIVATE))
    app.add_handler(CommandHandler("where", where_cmd, filters=PRIVATE))

    # В группах ловим ИМЕННО команды «тапалки» и просим перейти в ЛС
    only_tapper_cmds = r"^/(?:start|tap|register|link|registraciya|регистрация|привязка|mylink|unlink|remanga|where)(?:@\w+)?\b"
    app.add_handler(MessageHandler(GROUPS & filters.Regex(only_tapper_cmds), not_private))

    # Данные из WebApp — только в ЛС
    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA & PRIVATE, on_webapp_data))

    # Текстовые «общие» команды (работают везде)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_command))

    # Навигация (кнопки ⬅ 🔄 ➡)
    app.add_handler(CallbackQueryHandler(page_callback))

    log.info("Bot is running. ЛС: /start /tap /register /mylink /unlink /remanga /where | Группы: !ЕВ / !ЕД / !ТОП10, БЕНЯ, КРЯ, ASCII и пр.")
    app.run_polling()

if __name__ == "__main__":
    main()
