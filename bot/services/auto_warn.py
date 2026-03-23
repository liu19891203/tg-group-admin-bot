import re
import time
import logging

from telegram.constants import ParseMode
from telegram.error import TelegramError

from ..storage.config_store import get_group_auto_warn
from ..storage.session_store import get_warn_counter, save_warn_counter
from ..utils.message import get_message_text
from ..utils.template import render_template
from ..utils.time import warn_date_str
from ..utils.permissions import muted_permissions
from ..utils.telegram import is_admin

logger = logging.getLogger(__name__)


def rule_match(text: str, keyword: str, mode: str) -> bool:
    if not text or not keyword:
        return False
    if mode == "exact":
        return text.strip() == keyword
    if mode == "regex":
        try:
            return re.search(keyword, text) is not None
        except re.error:
            return False
    return keyword in text


def increment_warn(group_id: int, user_id: int):
    data = get_warn_counter(group_id, user_id)
    today = warn_date_str()
    if data.get("date") != today:
        data = {"date": today, "count": 0}
    data["count"] = int(data.get("count", 0)) + 1
    save_warn_counter(group_id, user_id, data)
    return data


async def apply_warn(context, chat, user, cfg):
    data = increment_warn(chat.id, user.id)
    limit = int(cfg.get("warn_limit", 3))
    count = int(data.get("count", 0))
    text = cfg.get("warn_text", "")
    if text:
        try:
            await context.bot.send_message(
                chat_id=chat.id,
                text=render_template(text, user, chat, {"count": count, "limit": limit}),
                parse_mode=ParseMode.HTML,
            )
        except TelegramError:
            pass
    if count >= limit:
        action = cfg.get("action", "mute")
        try:
            if action == "kick":
                await context.bot.ban_chat_member(chat_id=chat.id, user_id=user.id)
                await context.bot.unban_chat_member(chat_id=chat.id, user_id=user.id)
            else:
                mute_seconds = int(cfg.get("mute_seconds", 86400))
                await context.bot.restrict_chat_member(
                    chat_id=chat.id,
                    user_id=user.id,
                    permissions=muted_permissions(),
                    until_date=int(time.time()) + mute_seconds,
                )
        except TelegramError as exc:
            logger.warning("auto_warn action failed: %s", exc)
        return True
    return False


async def handle_auto_warn(context, message, user, chat) -> bool:
    cfg = get_group_auto_warn(chat.id)
    if not cfg.get("enabled", True):
        return False
    if await is_admin(context, chat.id, user.id):
        return False

    # command abuse
    if cfg.get("cmd_mute_enabled") and message.text and message.text.strip().startswith("/"):
        await apply_warn(context, chat, user, cfg)
        return True

    rules = cfg.get("rules", []) or []
    if not rules:
        return False
    text = get_message_text(message)
    for rule in rules:
        keyword = (rule.get("keyword") or "").strip()
        mode = rule.get("mode", "contains")
        if rule_match(text, keyword, mode):
            await apply_warn(context, chat, user, cfg)
            return True
    return False
