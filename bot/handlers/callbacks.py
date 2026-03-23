import logging

from .admin import admin_callback
from ..services.verify import (
    complete_verification,
    get_missing_verify_targets,
    get_verify_max_attempts,
    get_verify_message,
    handle_verification_failure,
    is_session_expired,
    send_verify_prompt,
    start_verification_on_join,
)
from ..storage.config_store import get_group_auto_replies, get_group_config, get_group_targets
from ..services.extra_features import load_schedule_items
from ..storage.session_store import clear_verify_session, get_verify_session, save_verify_session
from ..services.extra_features import (
    handle_gomoku_join_callback,
    handle_gomoku_move_callback,
    handle_gomoku_noop_callback,
    handle_gomoku_stop_callback,
    handle_lottery_draw_callback,
    handle_lottery_join_callback,
)
from ..utils.telegram import safe_answer
from ..web.login_bot import handle_web_login_callback

logger = logging.getLogger(__name__)
CALLBACK_TEXT_LIMIT = 180


def _truncate_callback_text(text: str) -> str:
    value = (text or "").strip()
    if not value:
        return "???????"
    if len(value) <= CALLBACK_TEXT_LIMIT:
        return value
    return value[: CALLBACK_TEXT_LIMIT - 3] + "..."


def _resolve_welcome_button(group_id: int, button_idx: int):
    buttons = get_group_config(group_id).get("welcome_buttons", []) or []
    if 0 <= button_idx < len(buttons):
        return buttons[button_idx]
    return None


def _resolve_verify_button(group_id: int, mode: str | None, button_idx: int):
    cfg = get_group_config(group_id)
    resolved_mode = (mode or cfg.get("verify_mode") or "join").strip() or "join"
    buttons = get_verify_message(cfg, resolved_mode).get("buttons", []) or []
    if 0 <= button_idx < len(buttons):
        return buttons[button_idx]
    return None


def _resolve_auto_reply_button(group_id: int, rule_idx: int, button_idx: int):
    rules = get_group_auto_replies(group_id)
    if not (0 <= rule_idx < len(rules)):
        return None
    buttons = rules[rule_idx].get("buttons", []) or []
    if 0 <= button_idx < len(buttons):
        return buttons[button_idx]
    return None


def _resolve_schedule_button(group_id: int, schedule_id: int, button_idx: int):
    items = load_schedule_items(group_id)
    for item in items:
        try:
            item_id = int(item.get("id") or 0)
        except (TypeError, ValueError):
            item_id = 0
        if item_id != schedule_id:
            continue
        buttons = item.get("buttons", []) or []
        if 0 <= button_idx < len(buttons):
            return buttons[button_idx]
        return None
    return None


def _resolve_invite_notify_button(group_id: int, button_idx: int):
    invite_cfg = get_group_config(group_id).get("invite_links", {}) or {}
    buttons = invite_cfg.get("notify_buttons", []) or []
    if 0 <= button_idx < len(buttons):
        return buttons[button_idx]
    return None


def _resolve_related_comment_button(group_id: int, button_idx: int):
    related_cfg = get_group_config(group_id).get("related_channel", {}) or {}
    buttons = related_cfg.get("occupy_comment_buttons", []) or []
    if 0 <= button_idx < len(buttons):
        return buttons[button_idx]
    return None

async def handle_custom_button_callback(update, context):
    del context
    query = update.callback_query
    data = query.data or ""
    button = None
    stale_hint = "????????????????"
    try:
        if data.startswith("wb:"):
            _, group_id, button_idx = data.split(":", 2)
            button = _resolve_welcome_button(int(group_id), int(button_idx))
        elif data.startswith("vcb:"):
            parts = data.split(":")
            if len(parts) == 4:
                _, group_id, mode, button_idx = parts
            elif len(parts) == 3:
                _, group_id, button_idx = parts
                mode = None
            else:
                raise ValueError("invalid verify button callback")
            button = _resolve_verify_button(int(group_id), mode, int(button_idx))
        elif data.startswith("arb:"):
            parts = data.split(":")
            if len(parts) == 4:
                _, rule_idx, group_id, button_idx = parts
                button = _resolve_auto_reply_button(int(group_id), int(rule_idx), int(button_idx))
            elif len(parts) == 3:
                await safe_answer(query, "????????????????????", show_alert=True)
                return
            else:
                raise ValueError("invalid auto reply button callback")
        elif data.startswith("smb:"):
            _, schedule_id, group_id, button_idx = data.split(":", 3)
            button = _resolve_schedule_button(int(group_id), int(schedule_id), int(button_idx))
        elif data.startswith("ivb:"):
            _, group_id, button_idx = data.split(":", 2)
            button = _resolve_invite_notify_button(int(group_id), int(button_idx))
        elif data.startswith("rcb:"):
            _, group_id, button_idx = data.split(":", 2)
            button = _resolve_related_comment_button(int(group_id), int(button_idx))
        else:
            return
    except Exception:
        logger.warning("custom_button_callback_parse_error: %s", data)
        await safe_answer(query, stale_hint, show_alert=True)
        return

    if not button:
        await safe_answer(query, stale_hint, show_alert=True)
        return
    await safe_answer(query, _truncate_callback_text(button.get("value") or button.get("text") or ""), show_alert=True)


async def handle_verify_check(update, context, chat_id: int, user_id: int):
    query = update.callback_query
    if query.from_user.id != user_id:
        await safe_answer(query, "??????")
        return
    cfg = get_group_config(chat_id)
    targets = get_group_targets(chat_id)
    session = get_verify_session(chat_id, user_id)
    chat = query.message.chat
    if chat.id != chat_id:
        chat = await context.bot.get_chat(chat_id)
    if session and is_session_expired(session):
        await start_verification_on_join(context, chat, query.from_user)
        await safe_answer(query, "???????????")
        return
    missing_targets = await get_missing_verify_targets(context, query.from_user, targets)
    if not missing_targets:
        await safe_answer(query, "????")
        await complete_verification(context, chat, query.from_user)
        return

    preview = "?".join(missing_targets[:3])
    if len(missing_targets) > 3:
        preview += " ?"
    await safe_answer(query, f"??????{preview}", show_alert=True)
    await send_verify_prompt(
        context,
        chat,
        query.from_user,
        cfg,
        targets,
        "join",
        session,
        send_private=cfg.get("verify_private"),
    )


async def handle_verify_answer(update, context, chat_id: int, user_id: int, index: int):
    query = update.callback_query
    if query.from_user.id != user_id:
        await safe_answer(query, "??????")
        return
    cfg = get_group_config(chat_id)
    session = get_verify_session(chat_id, user_id)
    chat = query.message.chat
    if chat.id != chat_id:
        chat = await context.bot.get_chat(chat_id)
    if not session or is_session_expired(session):
        await safe_answer(query, "???????????")
        await start_verification_on_join(context, chat, query.from_user)
        return
    if int(index) == int(session.get("correct_index", -1)):
        await safe_answer(query, "????")
        await complete_verification(context, chat, query.from_user)
        return

    attempts = int(session.get("attempts", 0) or 0) + 1
    max_attempts = get_verify_max_attempts(cfg)
    session["attempts"] = attempts
    if max_attempts > 0 and attempts >= max_attempts:
        await safe_answer(query, "????????????", show_alert=True)
        await handle_verification_failure(context, chat, query.from_user, cfg, reason="max_attempts")
        clear_verify_session(chat_id, user_id)
        return

    save_verify_session(chat_id, user_id, session)
    if max_attempts > 0:
        remaining = max_attempts - attempts
        await safe_answer(query, f"????????? {remaining} ?", show_alert=True)
        return
    await safe_answer(query, "????????", show_alert=True)


async def callback_router(update, context):
    data = update.callback_query.data or ""
    if data.startswith("weblogin:"):
        handled = await handle_web_login_callback(update, context)
        if handled:
            return
    if data.startswith("admin:") or data.startswith("adminx:"):
        await admin_callback(update, context)
        return
    if data.startswith("verify:check:"):
        _, _, chat_id, user_id = data.split(":", 3)
        await handle_verify_check(update, context, int(chat_id), int(user_id))
        return
    if data.startswith("verify:answer:"):
        _, _, chat_id, user_id, idx = data.split(":", 4)
        await handle_verify_answer(update, context, int(chat_id), int(user_id), int(idx))
        return
    if data.startswith("gomoku:join:"):
        _, _, chat_id, game_id = data.split(":", 3)
        await handle_gomoku_join_callback(update, context, int(chat_id), game_id)
        return
    if data.startswith("gomoku:move:"):
        _, _, chat_id, game_id, x, y = data.split(":", 5)
        await handle_gomoku_move_callback(update, context, int(chat_id), game_id, int(x), int(y))
        return
    if data.startswith("gomoku:stop:"):
        _, _, chat_id, game_id = data.split(":", 3)
        await handle_gomoku_stop_callback(update, context, int(chat_id), game_id)
        return
    if data.startswith("gomoku:noop:"):
        await handle_gomoku_noop_callback(update, context)
        return
    if data.startswith("lottery:join:"):
        _, _, chat_id, lottery_id = data.split(":", 3)
        await handle_lottery_join_callback(update, context, int(chat_id), lottery_id)
        return
    if data.startswith("lottery:draw:"):
        _, _, chat_id, lottery_id = data.split(":", 3)
        await handle_lottery_draw_callback(update, context, int(chat_id), lottery_id)
        return
    if data.startswith("vcb:") or data.startswith("wb:") or data.startswith("arb:") or data.startswith("smb:") or data.startswith("ivb:") or data.startswith("rcb:"):
        await handle_custom_button_callback(update, context)
        return
