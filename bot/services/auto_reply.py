import re
import logging

from telegram.constants import ParseMode
from telegram.error import TelegramError

from ..storage.config_store import get_group_auto_replies
from ..utils.message import get_message_text
from ..utils.template import render_template
from ..utils.telegram import build_buttons, send_rich_message

logger = logging.getLogger(__name__)


def rule_match(text: str, rule: dict) -> bool:
    if not text:
        return False
    keyword = (rule.get("keyword") or "").strip()
    if not keyword:
        return False
    mode = rule.get("mode", "contains")
    if mode == "exact":
        return text.strip() == keyword
    if mode == "regex":
        try:
            return re.search(keyword, text) is not None
        except re.error:
            return False
    return keyword in text


def build_reply_payload(rule: dict, user, chat):
    text = render_template(rule.get("reply_text", ""), user, chat)
    photo = rule.get("photo_file_id", "")
    buttons = rule.get("buttons", []) or []
    return text, photo, buttons


async def handle_auto_reply(context, message, user, chat) -> bool:
    rules = get_group_auto_replies(chat.id)
    if not rules:
        return False
    text = get_message_text(message)
    for rule_idx, rule in enumerate(rules):
        if not rule.get("enabled", True):
            continue
        if rule_match(text, rule):
            reply_text, photo, buttons = build_reply_payload(rule, user, chat)
            markup = build_buttons(buttons, chat.id, f"arb:{rule_idx}") if buttons else None
            try:
                if photo:
                    await context.bot.send_photo(
                        chat_id=chat.id,
                        photo=photo,
                        caption=reply_text or " ",
                        parse_mode=ParseMode.HTML,
                        reply_markup=markup,
                    )
                else:
                    await context.bot.send_message(
                        chat_id=chat.id,
                        text=reply_text or " ",
                        parse_mode=ParseMode.HTML,
                        reply_markup=markup,
                        disable_web_page_preview=True,
                    )
            except TelegramError as exc:
                logger.warning("auto_reply failed: %s", exc)
            return True
    return False

