import asyncio
import logging
import traceback
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatMemberStatus, ParseMode
from telegram.error import TelegramError

logger = logging.getLogger(__name__)


def normalize_url(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    if u.startswith("@"):
        u = u[1:]
    if u.startswith("t.me/"):
        u = "https://" + u
    if u.startswith("http://") or u.startswith("https://") or u.startswith("tg://"):
        return u
    return u


def build_chat_link(chat_id: int | None, username: str | None = None, join_url: str | None = None):
    if join_url:
        return join_url
    if username:
        return f"https://t.me/{username.lstrip('@')}"
    if chat_id and str(chat_id).startswith("-100"):
        return None
    return None


async def get_bot_username(context):
    try:
        if context.bot.username:
            return context.bot.username
    except Exception:
        pass
    me = await context.bot.get_me()
    return me.username


async def is_admin(context, group_id: int, user_id: int) -> bool:
    try:
        member = await context.bot.get_chat_member(group_id, user_id)
    except TelegramError:
        return False
    status = member.status
    if status in ("administrator", "creator", "owner"):
        return True
    if status == ChatMemberStatus.ADMINISTRATOR:
        return True
    owner = getattr(ChatMemberStatus, "OWNER", None)
    if owner and status == owner:
        return True
    return False


async def safe_answer(query, text: str = "", show_alert: bool = False, timeout_sec: float = 2.0):
    try:
        if text:
            coro = query.answer(text, show_alert=show_alert)
        else:
            coro = query.answer()
        await asyncio.wait_for(coro, timeout=timeout_sec)
        return True
    except asyncio.TimeoutError:
        logger.warning("answer_error: timed out after %.1fs", timeout_sec)
        return False
    except TelegramError as exc:
        logger.warning("answer_error: %s", exc)
        logger.debug("%s", traceback.format_exc())
        return False


async def safe_edit_message(query, text: str, reply_markup=None, parse_mode=None):
    try:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
        return True
    except TelegramError as exc:
        logger.warning("edit_message_error: %s", exc)
        logger.debug("%s", traceback.format_exc())
        try:
            if query.message:
                await query.message.reply_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
        except TelegramError as exc2:
            logger.warning("reply_text_error: %s", exc2)
            logger.debug("%s", traceback.format_exc())
        return False


def build_buttons(buttons, group_id: int, prefix: str):
    rows = {}
    for idx, btn in enumerate(buttons):
        row = int(btn.get("row", 0))
        rows.setdefault(row, [])
        if len(rows[row]) >= 2:
            continue
        btn_type = btn.get("type")
        if btn_type == "url":
            url = normalize_url(btn.get("value", ""))
            if not (url.startswith("http://") or url.startswith("https://") or url.startswith("tg://")):
                continue
            rows[row].append(InlineKeyboardButton(btn.get("text", "按钮"), url=url))
        else:
            rows[row].append(InlineKeyboardButton(btn.get("text", "按钮"), callback_data=f"{prefix}:{group_id}:{idx}"))
    keyboard = [rows[k] for k in sorted(rows.keys()) if rows[k]]
    return InlineKeyboardMarkup(keyboard) if keyboard else None

async def send_rich_message(
    bot,
    chat_id: int,
    text: str = "",
    photo: str = "",
    reply_markup=None,
    parse_mode=ParseMode.HTML,
    disable_web_page_preview: bool = True,
):
    if photo:
        return await bot.send_photo(
            chat_id=chat_id,
            photo=photo,
            caption=text or " ",
            parse_mode=parse_mode,
            reply_markup=reply_markup,
        )
    return await bot.send_message(
        chat_id=chat_id,
        text=text or " ",
        parse_mode=parse_mode,
        reply_markup=reply_markup,
        disable_web_page_preview=disable_web_page_preview,
    )

