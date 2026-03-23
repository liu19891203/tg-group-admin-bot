import html
import io
import random
import time
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import TelegramError

try:
    from PIL import Image, ImageDraw, ImageFont
except Exception:
    Image = ImageDraw = ImageFont = None

from ..storage.config_store import get_group_config, get_group_targets
from ..storage.session_store import (
    clear_verify_session,
    get_verify_session,
    get_verify_session_users,
    save_verify_session,
)
from ..utils.permissions import muted_permissions, full_permissions
from ..utils.template import render_template
from ..utils.telegram import build_chat_link, normalize_url, send_rich_message
from .welcome import send_welcome

logger = logging.getLogger(__name__)


class _TemplateChat:
    def __init__(self, chat_id: int, title: str = ""):
        self.id = int(chat_id)
        self.title = title or str(chat_id)


class _TemplateUser:
    def __init__(self, user_id: int, full_name: str | None = None):
        self.id = int(user_id)
        self.full_name = full_name or str(user_id)

    def mention_html(self) -> str:
        label = html.escape(self.full_name or str(self.id))
        return f'<a href="tg://user?id={self.id}">{label}</a>'


def verify_mode_label(mode: str) -> str:
    mapping = {
        "join": "关注频道",
        "calc": "计算",
        "image_calc": "图片计算",
        "captcha": "验证码",
    }
    return mapping.get(mode, mode)


def get_verify_message(cfg: dict, mode: str):
    messages = cfg.get("verify_messages", {}) or {}
    msg = messages.get(mode, {}) if isinstance(messages, dict) else {}
    defaults = {
        "join": "{userName} 请先加入所有验证目标后点击验证。",
        "calc": "{userName} 请完成计算：{question}",
        "image_calc": "{userName} 请完成图片计算：{question}",
        "captcha": "{userName} 请选择验证码正确答案。",
    }
    return {
        "text": msg.get("text") or defaults.get(mode, ""),
        "photo_file_id": msg.get("photo_file_id", ""),
        "buttons": msg.get("buttons", []) or [],
    }


def set_verify_message(cfg: dict, mode: str, text=None, photo_file_id=None, buttons=None):
    messages = cfg.get("verify_messages", {}) or {}
    msg = messages.get(mode, {}) if isinstance(messages, dict) else {}
    if text is not None:
        msg["text"] = text
    if photo_file_id is not None:
        msg["photo_file_id"] = photo_file_id
    if buttons is not None:
        msg["buttons"] = buttons
    messages[mode] = msg
    cfg["verify_messages"] = messages


def is_session_expired(session: dict) -> bool:
    if not session:
        return True
    expires_at = session.get("expires_at")
    if not expires_at:
        return False
    return int(time.time()) > int(expires_at)


def build_verify_custom_buttons(buttons, group_id: int, mode: str | None = None):
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
            callback_data = f"vcb:{group_id}:{idx}" if not mode else f"vcb:{group_id}:{mode}:{idx}"
            rows[row].append(InlineKeyboardButton(btn.get("text", "按钮"), callback_data=callback_data))
    return [rows[k] for k in sorted(rows.keys()) if rows[k]]


def build_verify_prompt_buttons(chat_id: int, user_id: int, mode: str, session: dict, targets: list, custom_buttons: list):
    rows = []

    if mode == "join":
        join_buttons = []
        for t in targets or []:
            join_url = t.get("join_url") or build_chat_link(t.get("chat_id"), t.get("username"), t.get("join_url"))
            if not join_url:
                continue
            label = t.get("title") or t.get("username") or str(t.get("chat_id"))
            if not t.get("checkable", True):
                label = f"{label}(不可校验)"
            join_buttons.append(InlineKeyboardButton(label, url=normalize_url(join_url)))
        for idx, btn in enumerate(join_buttons):
            if idx % 2 == 0:
                rows.append([])
            rows[-1].append(btn)

    if custom_buttons:
        rows.extend(custom_buttons)

    if mode == "join":
        rows.append([InlineKeyboardButton("✅ 已加入，立即验证", callback_data=f"verify:check:{chat_id}:{user_id}")])
    else:
        options = session.get("options", []) if session else []
        for idx, opt in enumerate(options):
            if idx % 2 == 0:
                rows.append([])
            rows[-1].append(InlineKeyboardButton(str(opt), callback_data=f"verify:answer:{chat_id}:{user_id}:{idx}"))

    return InlineKeyboardMarkup(rows) if rows else None


async def _send_verify_prompt_to_dest(context, dest: int, text: str, photo, markup):
    if photo:
        await context.bot.send_photo(
            chat_id=dest,
            photo=photo,
            caption=text or " ",
            parse_mode=ParseMode.HTML,
            reply_markup=markup,
        )
        return True
    await context.bot.send_message(
        chat_id=dest,
        text=text or " ",
        parse_mode=ParseMode.HTML,
        reply_markup=markup,
        disable_web_page_preview=True,
    )
    return True


async def send_verify_prompt(context, chat, user, cfg, targets, mode, session, send_private: bool = False, image_data=None):
    msg_cfg = get_verify_message(cfg, mode)
    text = msg_cfg.get("text", "")
    extra = {"question": session.get("question", "")} if session else {}
    text = render_template(text, user, chat, extra)

    custom_rows = build_verify_custom_buttons(msg_cfg.get("buttons", []), chat.id, mode=mode)
    markup = build_verify_prompt_buttons(chat.id, user.id, mode, session, targets, custom_rows)
    photo = image_data or msg_cfg.get("photo_file_id", "")

    delivery_order = [user.id, chat.id] if send_private else [chat.id]
    for dest in delivery_order:
        try:
            await _send_verify_prompt_to_dest(context, dest, text, photo, markup)
            if send_private and dest == chat.id:
                logger.warning("send_verify_prompt fell back to group delivery chat=%s user=%s", chat.id, user.id)
            return True
        except TelegramError as exc:
            logger.warning("send_verify_prompt failed chat=%s user=%s dest=%s: %s", chat.id, user.id, dest, exc)
    return False

def build_calc_question():
    a = random.randint(1, 50)
    b = random.randint(1, 50)
    op = random.choice(["+", "-"])
    if op == "-" and a < b:
        a, b = b, a
    answer = a + b if op == "+" else a - b
    question = f"{a} {op} {b} = ?"
    return question, answer


def build_options(answer: int):
    options = {answer}
    while len(options) < 4:
        options.add(answer + random.randint(-10, 10))
    opts = list(options)
    random.shuffle(opts)
    correct_index = opts.index(answer)
    return opts, correct_index


def create_text_image(text: str):
    if Image is None:
        return None
    w, h = 400, 160
    img = Image.new("RGB", (w, h), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    font = None
    try:
        font = ImageFont.truetype("arial.ttf", 48)
    except Exception:
        font = None
    draw.text((20, 40), text, fill=(0, 0, 0), font=font)
    bio = io.BytesIO()
    img.save(bio, format="PNG")
    bio.seek(0)
    return bio


def generate_challenge(mode: str):
    if mode in ("calc", "image_calc"):
        question, answer = build_calc_question()
        options, correct_index = build_options(answer)
        image = None
        if mode == "image_calc":
            image = create_text_image(question)
        return {"question": question, "options": options, "correct_index": correct_index, "image": image}
    if mode == "captcha":
        code = random.randint(1000, 9999)
        options, correct_index = build_options(code)
        image = create_text_image(str(code))
        question = "请选择正确验证码"
        return {"question": question, "options": options, "correct_index": correct_index, "image": image}
    return {"question": "", "options": [], "correct_index": 0, "image": None}


async def check_member_in_target(context, user_id: int, target: dict) -> bool:
    if not target.get("checkable", True):
        return True
    chat_id = target.get("chat_id")
    if not chat_id:
        return False
    try:
        cm = await context.bot.get_chat_member(chat_id, user_id)
        status = cm.status
        return status not in ("left", "kicked", "banned")
    except TelegramError:
        return False


def verify_target_label(target: dict) -> str:
    if not target:
        return "-"
    return str(target.get("title") or target.get("username") or target.get("chat_id") or target.get("join_url") or "-")


async def get_missing_verify_targets(context, user, targets: list) -> list[str]:
    missing = []
    for target in targets or []:
        ok = await check_member_in_target(context, user.id, target)
        if not ok:
            missing.append(verify_target_label(target))
    return missing


async def verify_member_targets(context, user, targets: list) -> bool:
    if not targets:
        return True
    results = []
    for t in targets:
        results.append(await check_member_in_target(context, user.id, t))
    return all(results)


def session_payload(mode: str, timeout: int, question: str, options: list, correct_index: int):
    now_ts = int(time.time())
    return {
        "mode": mode,
        "question": question,
        "options": options,
        "correct_index": correct_index,
        "attempts": 0,
        "expires_at": now_ts + int(timeout),
    }


def is_verify_enabled(cfg: dict) -> bool:
    return bool(cfg.get("verify_enabled", True))


def get_verify_max_attempts(cfg: dict) -> int:
    try:
        value = int(cfg.get("verify_max_attempts", 3) or 0)
    except (TypeError, ValueError):
        return 3
    return max(0, value)


def has_checkable_targets(targets: list) -> bool:
    return any(t.get("checkable", True) and t.get("chat_id") for t in (targets or []))


async def complete_verification(context, chat, user):
    try:
        await context.bot.restrict_chat_member(chat_id=chat.id, user_id=user.id, permissions=full_permissions())
    except TelegramError:
        pass
    clear_verify_session(chat.id, user.id)
    await send_welcome(context, chat, user)


async def start_verification_on_join(context, chat, user):
    cfg = get_group_config(chat.id)
    if not is_verify_enabled(cfg):
        return True
    mode = cfg.get("verify_mode", "join")
    targets = get_group_targets(chat.id)

    if mode == "join":
        if not targets:
            return True
        has_checkable = has_checkable_targets(targets)
        ok = await verify_member_targets(context, user, targets)
        if ok and has_checkable:
            try:
                await context.bot.restrict_chat_member(chat_id=chat.id, user_id=user.id, permissions=full_permissions())
            except TelegramError:
                pass
            return True
        try:
            await context.bot.restrict_chat_member(chat_id=chat.id, user_id=user.id, permissions=muted_permissions())
        except TelegramError:
            pass
        session = {"mode": "join", "expires_at": int(time.time()) + int(cfg.get("verify_timeout_sec", 60))}
        save_verify_session(chat.id, user.id, session)
        await send_verify_prompt(context, chat, user, cfg, targets, "join", session, send_private=cfg.get("verify_private"))
        return False

    try:
        await context.bot.restrict_chat_member(chat_id=chat.id, user_id=user.id, permissions=muted_permissions())
    except TelegramError:
        pass
    challenge = generate_challenge(mode)
    session = session_payload(
        mode,
        cfg.get("verify_timeout_sec", 60),
        challenge.get("question"),
        challenge.get("options"),
        challenge.get("correct_index"),
    )
    save_verify_session(chat.id, user.id, session)
    await send_verify_prompt(
        context,
        chat,
        user,
        cfg,
        targets,
        mode,
        session,
        send_private=cfg.get("verify_private"),
        image_data=challenge.get("image"),
    )
    return False


async def ensure_user_verified(context, chat, user):
    cfg = get_group_config(chat.id)
    if not is_verify_enabled(cfg):
        return True
    mode = cfg.get("verify_mode", "join")
    if mode == "join":
        targets = get_group_targets(chat.id)
        if not targets:
            return True
        has_checkable = has_checkable_targets(targets)
        ok = await verify_member_targets(context, user, targets)
        if ok and has_checkable:
            return True
        session = get_verify_session(chat.id, user.id)
        if not session:
            await start_verification_on_join(context, chat, user)
        elif is_session_expired(session):
            await handle_verification_failure(context, chat, user, cfg, reason="timeout")
            clear_verify_session(chat.id, user.id)
            await start_verification_on_join(context, chat, user)
        return False

    session = get_verify_session(chat.id, user.id)
    if session and not is_session_expired(session):
        return False
    if session and is_session_expired(session):
        await handle_verification_failure(context, chat, user, cfg, reason="timeout")
        clear_verify_session(chat.id, user.id)
    await start_verification_on_join(context, chat, user)
    return False


async def handle_verification_failure(context, chat, user, cfg, reason: str = "timeout"):
    text = cfg.get("verify_fail_text", "")
    if text:
        try:
            await context.bot.send_message(chat_id=chat.id, text=render_template(text, user, chat), parse_mode=ParseMode.HTML)
        except TelegramError:
            pass
    action = cfg.get("verify_fail_action", "mute")
    try:
        if action == "ban":
            await context.bot.ban_chat_member(chat_id=chat.id, user_id=user.id)
        elif action == "kick":
            await context.bot.ban_chat_member(chat_id=chat.id, user_id=user.id)
            await context.bot.unban_chat_member(chat_id=chat.id, user_id=user.id)
        elif action == "mute":
            await context.bot.restrict_chat_member(chat_id=chat.id, user_id=user.id, permissions=muted_permissions())
    except TelegramError:
        pass


async def _resolve_verify_chat(context, group_id: int, cfg: dict):
    try:
        return await context.bot.get_chat(group_id)
    except TelegramError:
        return _TemplateChat(group_id, str(cfg.get("group_title") or ""))


async def _resolve_verify_user(context, group_id: int, user_id: int):
    try:
        member = await context.bot.get_chat_member(chat_id=group_id, user_id=user_id)
        if getattr(member, "user", None):
            return member.user
    except TelegramError:
        pass
    return _TemplateUser(user_id)


async def sweep_expired_verify_sessions(context, group_id: int) -> int:
    user_ids = get_verify_session_users(group_id)
    if not user_ids:
        return 0

    cfg = get_group_config(group_id)
    if not is_verify_enabled(cfg):
        for user_id in list(user_ids):
            clear_verify_session(group_id, user_id)
        return 0

    chat = None
    expired_count = 0
    for user_id in list(user_ids):
        session = get_verify_session(group_id, user_id)
        if not session:
            clear_verify_session(group_id, user_id)
            continue
        if not is_session_expired(session):
            continue
        if chat is None:
            chat = await _resolve_verify_chat(context, group_id, cfg)
        user = await _resolve_verify_user(context, group_id, user_id)
        await handle_verification_failure(context, chat, user, cfg, reason="timeout")
        clear_verify_session(group_id, user_id)
        expired_count += 1

    if expired_count:
        logger.info("expired_verify_sessions_swept group=%s count=%s", group_id, expired_count)
    return expired_count


def parse_target_input(text: str):
    if not text:
        return {"type": "invalid"}
    t = text.strip()
    if t.startswith("@"):
        return {"type": "username", "value": t}
    if t.startswith("https://t.me/") or t.startswith("http://t.me/") or t.startswith("t.me/"):
        raw = t.replace("https://", "").replace("http://", "")
        raw = raw.replace("t.me/", "")
        if raw.startswith("+") or raw.startswith("joinchat"):
            return {"type": "invite", "value": t}
        return {"type": "username", "value": "@" + raw.lstrip("@")}
    if t.startswith("https://t.me/+") or t.startswith("t.me/+") or "joinchat" in t:
        return {"type": "invite", "value": t}
    if t.lstrip("-").isdigit():
        return {"type": "chat_id", "value": int(t)}
    return {"type": "invalid"}

