from __future__ import annotations

import logging
import re

from telegram.constants import ParseMode
from telegram.error import TelegramError

from ..storage.config_store import get_group_config
from ..utils.message import get_message_text, iter_entities
from ..utils.telegram import build_buttons
from ..utils.template import render_template

logger = logging.getLogger(__name__)

VERIFIED_BUTTON_CALLBACK_PREFIX = "vub"
_USERNAME_PATTERN_TEMPLATE = r"(?<![A-Za-z0-9_])@?{name}(?![A-Za-z0-9_])"


def _normalize_buttons(buttons) -> list[dict]:
    result = []
    for item in list(buttons or []):
        if not isinstance(item, dict):
            continue
        try:
            row = max(0, int(item.get("row", 0) or 0))
        except (TypeError, ValueError):
            row = 0
        result.append(
            {
                "text": str(item.get("text") or "按钮"),
                "type": str(item.get("type") or "url"),
                "value": str(item.get("value") or ""),
                "row": row,
            }
        )
    return result


def normalize_verified_member(value) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    lower = text.lower()
    for prefix in ("https://t.me/", "http://t.me/", "t.me/"):
        if lower.startswith(prefix):
            text = text[len(prefix) :]
            break
    text = text.strip().strip("/")
    if text.startswith("@"):
        text = text[1:]
    if not text:
        return ""
    if text.isdigit():
        return text
    return re.sub(r"[^a-z0-9_]", "", text.lower())


def normalize_verified_members(values) -> list[str]:
    result = []
    seen = set()
    for value in list(values or []):
        item = normalize_verified_member(value)
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def verified_member_label(value: str) -> str:
    member = normalize_verified_member(value)
    if not member:
        return ""
    if member.isdigit():
        return member
    return f"@{member}"


def build_verified_user_message_payload(data: dict | None) -> dict:
    payload = dict(data or {})
    return {
        "text": str(payload.get("reply_text") or payload.get("text") or ""),
        "photo_file_id": str(payload.get("reply_photo_file_id") or payload.get("photo_file_id") or ""),
        "buttons": _normalize_buttons(payload.get("reply_buttons") or payload.get("buttons") or []),
    }


def find_verified_member_match(message, members) -> dict | None:
    normalized_members = normalize_verified_members(members)
    if not normalized_members:
        return None

    text = get_message_text(message)
    mention_ids = set()
    for entity in iter_entities(message):
        if str(getattr(entity, "type", "") or "") != "text_mention":
            continue
        entity_user = getattr(entity, "user", None)
        entity_user_id = getattr(entity_user, "id", None)
        if entity_user_id is not None:
            mention_ids.add(str(entity_user_id))

    for member in normalized_members:
        if member.isdigit():
            if member in mention_ids:
                return {"value": member, "label": member, "kind": "id"}
            if text and re.search(rf"(?<!\d){re.escape(member)}(?!\d)", text):
                return {"value": member, "label": member, "kind": "id"}
            continue
        if not text:
            continue
        pattern = _USERNAME_PATTERN_TEMPLATE.format(name=re.escape(member))
        if re.search(pattern, text.lower()):
            return {"value": member, "label": f"@{member}", "kind": "username"}
    return None


async def handle_verified_user_reply(context, message, user, chat) -> bool:
    verified_cfg = get_group_config(chat.id).get("verified_user", {}) or {}
    if not bool(verified_cfg.get("enabled")):
        return False

    members = normalize_verified_members(verified_cfg.get("members") or [])
    if not members:
        return False

    payload = build_verified_user_message_payload(verified_cfg)
    if not payload["text"] and not payload["photo_file_id"] and not payload["buttons"]:
        return False

    matched = find_verified_member_match(message, members)
    if not matched:
        return False

    rendered = render_template(
        payload["text"],
        user,
        chat,
        {
            "verified": matched["label"],
            "verifiedUser": matched["label"],
            "matchedUser": matched["label"],
        },
    )
    reply_markup = build_buttons(payload["buttons"], chat.id, VERIFIED_BUTTON_CALLBACK_PREFIX) if payload["buttons"] else None

    try:
        if payload["photo_file_id"]:
            await message.reply_photo(
                payload["photo_file_id"],
                caption=rendered or " ",
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
            )
        else:
            await message.reply_text(
                rendered or " ",
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
                disable_web_page_preview=True,
            )
    except TelegramError as exc:
        logger.warning("verified_user_reply failed: %s", exc)
        return False
    return True
