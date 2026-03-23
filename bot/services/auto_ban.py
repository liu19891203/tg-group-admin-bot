import re
import logging
import time

from telegram.error import TelegramError

from ..storage.config_store import get_group_auto_ban
from ..utils.message import get_message_text
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


async def handle_auto_ban(context, message, user, chat) -> bool:
    cfg = get_group_auto_ban(chat.id)
    if not cfg.get("enabled", True):
        return False
    if await is_admin(context, chat.id, user.id):
        return False
    rules = cfg.get("rules", []) or []
    if not rules:
        return False
    text = get_message_text(message)
    for rule in rules:
        keyword = (rule.get("keyword") or "").strip()
        mode = rule.get("mode", "contains")
        if rule_match(text, keyword, mode):
            duration = int(rule.get("duration_sec") or cfg.get("default_duration_sec", 86400))
            try:
                if duration > 0:
                    await context.bot.ban_chat_member(
                        chat_id=chat.id,
                        user_id=user.id,
                        until_date=int(time.time()) + duration,
                    )
                else:
                    await context.bot.ban_chat_member(
                        chat_id=chat.id,
                        user_id=user.id,
                    )
            except TelegramError as exc:
                logger.warning("auto_ban failed: %s", exc)
            return True
    return False
