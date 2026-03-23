import time
import logging

from telegram.error import TelegramError

from ..storage.config_store import get_group_anti_spam
from ..storage.kv import kv_get_json, kv_set_json
from ..utils.message import get_message_fingerprint
from ..utils.permissions import muted_permissions
from ..utils.telegram import is_admin

logger = logging.getLogger(__name__)


def get_spam_key(group_id: int, user_id: int):
    return f"spam:{group_id}:{user_id}"


def record_and_count(group_id: int, user_id: int, fingerprint: str, window_sec: int):
    key = get_spam_key(group_id, user_id)
    data = kv_get_json(key, []) or []
    now_ts = int(time.time())
    data = [item for item in data if int(item.get("ts", 0)) >= now_ts - window_sec]
    data.append({"ts": now_ts, "fp": fingerprint})
    kv_set_json(key, data)
    return sum(1 for item in data if item.get("fp") == fingerprint)


async def handle_anti_spam(context, message, user, chat) -> bool:
    cfg = get_group_anti_spam(chat.id)
    if not cfg.get("enabled", False):
        return False
    if await is_admin(context, chat.id, user.id):
        return False

    types = set(cfg.get("types") or [])
    msg_type, fingerprint = get_message_fingerprint(message, types)
    if not fingerprint:
        return False

    window_sec = int(cfg.get("window_sec", 10))
    count = record_and_count(chat.id, user.id, fingerprint, window_sec)
    threshold = int(cfg.get("threshold", 3))
    if count >= threshold:
        action = cfg.get("action", "mute")
        try:
            if action == "ban":
                await context.bot.ban_chat_member(chat_id=chat.id, user_id=user.id)
            else:
                mute_seconds = int(cfg.get("mute_seconds", 300))
                await context.bot.restrict_chat_member(
                    chat_id=chat.id,
                    user_id=user.id,
                    permissions=muted_permissions(),
                    until_date=int(time.time()) + mute_seconds,
                )
        except TelegramError as exc:
            logger.warning("anti_spam failed: %s", exc)
        return True
    return False
