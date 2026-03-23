import re
import logging

from telegram.error import TelegramError

from ..storage.config_store import get_group_auto_delete
from ..utils.message import (
    get_message_text,
    message_has_link,
    message_has_mention,
    message_has_custom_emoji,
    message_only_custom_emoji,
    is_system_message,
    is_long_message,
    is_forwarded_message,
    has_qr_hint,
    is_external_reply,
    is_notice_text,
)
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


def is_archive_file(message) -> bool:
    if not message or not message.document or not message.document.file_name:
        return False
    name = message.document.file_name.lower()
    return name.endswith((".zip", ".rar", ".7z", ".tar", ".gz"))


def is_executable_file(message) -> bool:
    if not message or not message.document or not message.document.file_name:
        return False
    name = message.document.file_name.lower()
    return name.endswith((".exe", ".apk", ".bat", ".cmd", ".msi"))


def is_ad_sticker(message, cfg: dict) -> bool:
    if not message or not message.sticker:
        return False
    ids = cfg.get("ad_sticker_ids") or []
    if not ids:
        return False
    return message.sticker.file_unique_id in ids


def should_delete(cfg: dict, message) -> bool:
    if cfg.get("delete_system") and is_system_message(message):
        return True
    if cfg.get("delete_channel_mask") and message.sender_chat:
        return True
    if cfg.get("delete_links") and message_has_link(message):
        return True
    if cfg.get("delete_mentions") and message_has_mention(message):
        return True
    if cfg.get("delete_long") and is_long_message(message, int(cfg.get("long_length", 500))):
        return True
    if cfg.get("delete_videos") and message.video:
        return True
    if cfg.get("delete_stickers") and message.sticker:
        return True
    if cfg.get("delete_forwarded") and is_forwarded_message(message):
        return True
    if cfg.get("delete_ad_stickers") and is_ad_sticker(message, cfg):
        return True
    if cfg.get("delete_archives") and is_archive_file(message):
        return True
    if cfg.get("delete_executables") and is_executable_file(message):
        return True
    if cfg.get("delete_notice_text") and is_notice_text(message):
        return True
    if cfg.get("delete_documents") and message.document:
        return True
    if cfg.get("delete_other_commands") and message.text and message.text.strip().startswith("/"):
        return True
    if cfg.get("delete_qr") and has_qr_hint(message):
        return True
    if cfg.get("delete_edited") and message.edit_date:
        return True
    if cfg.get("delete_member_emoji") and message_has_custom_emoji(message):
        return True
    if cfg.get("delete_member_emoji_only") and message_only_custom_emoji(message):
        return True
    if cfg.get("delete_external_reply") and is_external_reply(message):
        return True
    if cfg.get("delete_shared_contact") and message.contact:
        return True

    text = get_message_text(message)
    for rule in cfg.get("custom_rules", []) or []:
        keyword = (rule.get("keyword") or "").strip()
        mode = rule.get("mode", "contains")
        if rule_match(text, keyword, mode):
            return True
    return False


async def handle_auto_delete(context, message, user, chat) -> bool:
    cfg = get_group_auto_delete(chat.id)
    if cfg.get("exclude_admins"):
        if await is_admin(context, chat.id, user.id):
            return False
    if should_delete(cfg, message):
        try:
            await context.bot.delete_message(chat_id=chat.id, message_id=message.message_id)
        except TelegramError as exc:
            logger.warning("auto_delete failed: %s", exc)
        return True
    return False
