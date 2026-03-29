import re


def get_message_text(message):
    if message is None:
        return ""
    return (message.text or "") + ("\n" + message.caption if message.caption else "")


def iter_entities(message):
    if message is None:
        return []
    entities = []
    if message.entities:
        entities += list(message.entities)
    if message.caption_entities:
        entities += list(message.caption_entities)
    return entities


def has_entity_type(message, types):
    for ent in iter_entities(message):
        if ent.type in types:
            return True
    return False


def message_has_link(message) -> bool:
    text = get_message_text(message)
    if not text:
        return False
    if has_entity_type(message, ("url", "text_link")):
        return True
    return re.search(r"(https?://|t\.me/|tg://|www\.)", text, re.I) is not None


def message_has_mention(message) -> bool:
    text = get_message_text(message)
    if not text:
        return False
    if has_entity_type(message, ("mention", "text_mention")):
        return True
    return "@" in text


def message_has_custom_emoji(message) -> bool:
    return has_entity_type(message, ("custom_emoji",))


def message_only_custom_emoji(message) -> bool:
    text = message.text or ""
    entities = message.entities or []
    if not text or not entities:
        return False
    if any(ent.type != "custom_emoji" for ent in entities):
        return False
    unit_len = 0
    for ch in text:
        unit_len += len(ch.encode("utf-16-le")) // 2
    if unit_len == 0:
        return False
    mask = [False] * unit_len
    for ent in entities:
        start = ent.offset
        end = ent.offset + ent.length
        for i in range(start, min(end, unit_len)):
            mask[i] = True
    pos = 0
    for ch in text:
        span = len(ch.encode("utf-16-le")) // 2
        if ch.strip():
            for i in range(span):
                idx = pos + i
                if idx >= unit_len or not mask[idx]:
                    return False
        pos += span
    return True


def is_system_message(message) -> bool:
    if message is None:
        return False
    return bool(
        message.new_chat_members
        or message.left_chat_member
        or message.new_chat_title
        or message.new_chat_photo
        or message.delete_chat_photo
        or message.group_chat_created
        or message.supergroup_chat_created
        or message.channel_chat_created
        or message.pinned_message
        or getattr(message, "message_auto_delete_timer_changed", None)
        or getattr(message, "migrate_to_chat_id", None)
        or getattr(message, "migrate_from_chat_id", None)
    )


def is_long_message(message, limit: int) -> bool:
    text = get_message_text(message)
    return bool(text) and len(text) > limit


def is_forwarded_message(message) -> bool:
    if message is None:
        return False
    return bool(
        getattr(message, "forward_origin", None)
        or getattr(message, "forward_from", None)
        or getattr(message, "forward_sender_name", None)
        or getattr(message, "forward_date", None)
    )


def has_qr_hint(message) -> bool:
    text = get_message_text(message)
    if re.search(r"(qr|qrcode|二维码)", text, re.I):
        return True
    if message and message.document and message.document.file_name:
        name = message.document.file_name.lower()
        if "qr" in name or "qrcode" in name or "erweima" in name:
            return True
    return False


def is_external_reply(message) -> bool:
    if not message or not message.reply_to_message:
        return False
    reply = message.reply_to_message
    if reply.forward_origin and getattr(reply.forward_origin, "chat", None):
        if reply.forward_origin.chat.id != message.chat.id:
            return True
    if reply.sender_chat and reply.sender_chat.id != message.chat.id:
        return True
    return False


def normalize_link_value(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    u = u.rstrip("/").strip()
    return u.lower()


def extract_links(message):
    urls = []
    if message is None:
        return urls
    if message.entities:
        for ent in message.entities:
            if ent.type == "text_link" and getattr(ent, "url", None):
                urls.append(ent.url)
    if message.caption_entities:
        for ent in message.caption_entities:
            if ent.type == "text_link" and getattr(ent, "url", None):
                urls.append(ent.url)
    if not urls:
        text = get_message_text(message)
        if text:
            urls = re.findall(r"(https?://\S+|t\.me/\S+|tg://\S+|www\.\S+)", text, re.I)
    return [normalize_link_value(u) for u in urls if u]


def is_notice_text(message) -> bool:
    text = get_message_text(message).strip()
    if not text:
        return False
    keywords = ["提醒", "注意", "警告", "请勿", "禁止", "勿", "群规"]
    return any(k in text for k in keywords)


def normalize_spam_text(text: str) -> str:
    value = (text or "").lower()
    value = value.replace("​", "").replace("﻿", "")
    value = re.sub(r"\s+", " ", value).strip()
    return value


def get_message_fingerprint(message, types: set[str]):
    if message is None:
        return None, None
    if "photo" in types and message.photo:
        return "photo", f"photo:{message.photo[-1].file_unique_id}"
    if "video" in types and message.video:
        return "video", f"video:{message.video.file_unique_id}"
    if "document" in types and message.document:
        return "document", f"document:{message.document.file_unique_id}"
    if "voice" in types and message.voice:
        return "voice", f"voice:{message.voice.file_unique_id}"
    if "sticker" in types and message.sticker:
        return "sticker", f"sticker:{message.sticker.file_unique_id}"
    if "link" in types and message_has_link(message):
        links = extract_links(message)
        if links:
            return "link", "link:" + "|".join(links)
    if "text" in types:
        txt = normalize_spam_text(get_message_text(message))
        if txt:
            return "text", "text:" + txt
    return None, None
