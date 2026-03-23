import time
import logging

from telegram.constants import ParseMode
from telegram.error import TelegramError

from ..storage.config_store import get_group_config
from ..storage.session_store import get_welcome_queue, save_welcome_queue
from ..utils.template import render_template
from ..utils.telegram import build_buttons, send_rich_message

logger = logging.getLogger(__name__)


def build_welcome_payload(cfg: dict, user, chat):
    text = render_template(cfg.get("welcome_text", ""), user, chat)
    photo = cfg.get("welcome_photo_file_id", "")
    buttons = cfg.get("welcome_buttons", []) or []
    return text, photo, buttons


async def send_welcome(context, chat, user):
    cfg = get_group_config(chat.id)
    if not cfg.get("welcome_enabled", True):
        return None

    text, photo, buttons = build_welcome_payload(cfg, user, chat)
    markup = build_buttons(buttons, chat.id, "wb") if buttons else None

    # delete previous welcome if configured
    queue = get_welcome_queue(chat.id)
    if cfg.get("welcome_delete_prev"):
        if queue:
            last = queue[-1]
            try:
                await context.bot.delete_message(chat_id=chat.id, message_id=last.get("message_id"))
            except TelegramError:
                pass
        queue = []

    try:
        if photo:
            msg = await context.bot.send_photo(
                chat_id=chat.id,
                photo=photo,
                caption=text or " ",
                parse_mode=ParseMode.HTML,
                reply_markup=markup,
            )
        else:
            msg = await context.bot.send_message(
                chat_id=chat.id,
                text=text or " ",
                parse_mode=ParseMode.HTML,
                reply_markup=markup,
                disable_web_page_preview=True,
            )
    except TelegramError as exc:
        logger.warning("send_welcome failed: %s", exc)
        return None

    ttl = int(cfg.get("welcome_ttl_sec", 0) or 0)
    if ttl > 0:
        queue.append({"message_id": msg.message_id, "delete_at": int(time.time()) + ttl})
    else:
        queue.append({"message_id": msg.message_id, "delete_at": 0})
    save_welcome_queue(chat.id, queue)
    return msg


async def process_welcome_queue(context, chat_id: int):
    queue = get_welcome_queue(chat_id)
    if not queue:
        return
    now_ts = int(time.time())
    remaining = []
    for item in queue:
        delete_at = int(item.get("delete_at", 0) or 0)
        if delete_at and delete_at <= now_ts:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=item.get("message_id"))
            except TelegramError:
                pass
        else:
            remaining.append(item)
    if len(remaining) != len(queue):
        save_welcome_queue(chat_id, remaining)

