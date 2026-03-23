import datetime
import random
import re
import time

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import TelegramError

from ..models.config import STATE_NONE, SUPER_ADMIN_ID
from ..storage.config_store import (
    clear_clone_launch_request,
    get_manual_order,
    get_user_profile,
    save_admin_state,
    save_clone_launch_request,
    save_manual_order,
    save_user_profile,
)
from ..services.membership import clone_launch_state, launch_ready_clone_count
from ..utils.telegram import safe_answer, safe_edit_message

STATE_HOME_CLONE_TOKEN = "home_clone_token"

LANGUAGE_OPTIONS = [
    ("en", "英语"),
    ("ru", "俄语"),
    ("it", "意大利语"),
    ("es", "西班牙语"),
    ("pt", "葡萄牙语"),
    ("de", "德语"),
    ("fr", "法语"),
    ("id", "印尼语"),
    ("tr", "土耳其语"),
    ("uk", "乌克兰语"),
    ("uz", "乌兹别克语"),
    ("ar", "阿拉伯语"),
    ("fa", "波斯语"),
    ("az", "阿塞拜疆语"),
    ("ms", "马来语"),
    ("my", "缅甸语"),
    ("hi", "印地语"),
    ("kk", "哈萨克语"),
    ("zh_cn", "简体中文"),
    ("zh_tw", "繁体中文"),
    ("ja", "日语"),
    ("ko", "韩语"),
    ("vi", "越南语"),
    ("th", "泰语"),
]
LANGUAGE_LABELS = dict(LANGUAGE_OPTIONS)

MEMBERSHIP_PLANS = {
    "m1": {"label": "10U 1\u4e2a\u6708", "amount": 10, "days": 30},
    "m3": {"label": "29U 3\u4e2a\u6708", "amount": 29, "days": 90},
    "m6": {"label": "55U 6\u4e2a\u6708", "amount": 55, "days": 180},
    "m12": {"label": "100U 1\u5e74", "amount": 100, "days": 365},
}

CLONE_PLANS = {
    "c1": {"label": "50U 1\u4e2a\u6708", "amount": 50, "days": 30},
    "c12": {"label": "500U 1\u5e74", "amount": 500, "days": 365},
}

TOKEN_RE = re.compile(r"(\d{7,}:[A-Za-z0-9_-]{20,})")


EMOJI_PREFIXES = ("🏠", "📣", "👥", "⚡", "💎", "🤖", "🕒", "🌐", "🔑", "↩️", "✅", "❌", "📩")

HOME_LABEL_TRANSLATIONS = {
    "Languages": "语言设置",
    "提交 Bot Token": "提交机器人令牌",
    "开通克隆Bot": "开通克隆机器人",
}


def _button_icon(data: str, label: str) -> str:
    text = str(label or "").strip()
    route = str(data or "")
    if text.startswith("返回"):
        return "↩️"
    if route == "admin:cancel_input":
        return "❌"
    if route == "admin:home":
        return "🏠"
    if route.startswith("admin:home:channels"):
        return "📣"
    if route.startswith("admin:home:groups"):
        return "👥"
    if route.startswith("admin:home:quick"):
        return "⚡"
    if route.startswith("admin:home:membership"):
        return "💎"
    if route.startswith("admin:home:clone:token"):
        return "🔑"
    if route.startswith("admin:home:clone"):
        return "🤖"
    if route.startswith("admin:home:timezone"):
        return "🕒"
    if route.startswith("admin:home:language"):
        return "🌐"
    if route.startswith("admin:billing:approve:"):
        return "✅"
    if route.startswith("admin:billing:reject:"):
        return "❌"
    return "🔹"


def _button_text(label: str, data: str) -> str:
    text = HOME_LABEL_TRANSLATIONS.get(str(label or "").strip(), str(label or "").strip())
    if text.startswith(EMOJI_PREFIXES):
        return text
    icon = _button_icon(data, text)
    return f"{icon} {text}" if text else icon


def _btn(label: str, data: str):
    return InlineKeyboardButton(_button_text(label, data), callback_data=data)


def _rows(items, width: int = 2):
    rows = []
    for idx in range(0, len(items), width):
        rows.append(items[idx:idx + width])
    return rows


def _state_with(base_state: dict | None, **kwargs):
    state = dict(base_state or {})
    state.update(kwargs)
    return state


def _save_state(user_id: int, state: dict):
    save_admin_state(user_id, state)


def _offset_label(offset: int) -> str:
    sign = "+" if int(offset) >= 0 else ""
    return f"UTC{sign}{int(offset)}"


def _now_text(offset: int) -> str:
    now = datetime.datetime.utcnow() + datetime.timedelta(hours=int(offset))
    return f"{now.strftime('%Y-%m-%d %H:%M:%S')} {_offset_label(offset)}"


def _format_expire_text(expires_at: int, offset: int) -> str:
    if not expires_at or int(expires_at) <= int(time.time()):
        return "\u672a\u5f00\u901a"
    dt = datetime.datetime.utcfromtimestamp(int(expires_at)) + datetime.timedelta(hours=int(offset))
    return f"{dt.strftime('%Y-%m-%d %H:%M:%S')} {_offset_label(offset)}"


def _clone_status_text(clone: dict, offset: int) -> str:
    state = clone_launch_state(clone)
    if state == "launch_ready":
        return f"\u5f85\u542f\u52a8\uff08\u5230\u671f {_format_expire_text(clone.get('expires_at', 0), offset)}\uff09"
    if state == "expired":
        return "\u5df2\u8fc7\u671f\uff0c\u9700\u7eed\u8d39\u540e\u91cd\u65b0\u542f\u52a8"
    if state == "rejected":
        return "\u5df2\u9a73\u56de"
    if state == "pending_payment":
        return "\u5f85\u652f\u4ed8"
    return "Token \u5df2\u4fdd\u5b58"


def _mask_username(username: str | None) -> str:
    if not username:
        return "(\u672a\u8bc6\u522b\u7528\u6237\u540d)"
    return f"@{str(username).lstrip('@')}"


async def _send_or_edit(update, text: str, reply_markup=None):
    if getattr(update, "callback_query", None):
        return await safe_edit_message(update.callback_query, text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    if update.effective_message:
        return await update.effective_message.reply_text(
            text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    return None


async def _send_prompt(update, text: str):
    markup = InlineKeyboardMarkup([[_btn("\u53d6\u6d88\u8f93\u5165", "admin:cancel_input")]])
    if update.effective_message:
        return await update.effective_message.reply_text(
            text,
            reply_markup=markup,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    return await _send_or_edit(update, text, markup)


def _new_order_id(prefix: str) -> str:
    return f"{prefix}{time.strftime('%y%m%d%H%M%S')}{random.randint(100, 999)}"


def _append_user_order(profile: dict, order_id: str):
    orders = list(profile.get("orders") or [])
    if order_id not in orders:
        orders.insert(0, order_id)
    profile["orders"] = orders[:30]


def _upsert_clone_bot(profile: dict, token: str, identity: dict) -> dict:
    clones = list(profile.get("clone_bots") or [])
    bot_id = int(identity.get("id"))
    existing = next((item for item in clones if int(item.get("bot_id", 0)) == bot_id), None)
    now_ts = int(time.time())
    if existing is None:
        existing = {
            "request_id": f"cb{bot_id}",
            "bot_id": bot_id,
            "token": token,
            "username": identity.get("username") or "",
            "first_name": identity.get("first_name") or "",
            "status": "saved",
            "expires_at": 0,
            "created_at": now_ts,
        }
        clones.insert(0, existing)
    else:
        existing.update({
            "token": token,
            "username": identity.get("username") or existing.get("username") or "",
            "first_name": identity.get("first_name") or existing.get("first_name") or "",
            "updated_at": now_ts,
        })
    profile["clone_bots"] = clones
    return existing


def _find_clone(profile: dict, clone_id: str) -> dict | None:
    for item in profile.get("clone_bots") or []:
        if str(item.get("request_id")) == str(clone_id):
            return item
    return None


def _build_clone_launch_payload(user_id: int, profile: dict, clone: dict) -> dict:
    return {
        "request_id": clone.get("request_id"),
        "user_id": int(user_id),
        "bot_id": int(clone.get("bot_id", 0) or 0),
        "username": clone.get("username") or "",
        "first_name": clone.get("first_name") or "",
        "token": clone.get("token") or "",
        "language": profile.get("language") or "zh_cn",
        "timezone_offset": int(profile.get("timezone_offset", 8) or 8),
        "expires_at": int(clone.get("expires_at", 0) or 0),
        "status": "ready",
        "saved_at": int(time.time()),
    }


async def _fetch_bot_identity(token: str) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"https://api.telegram.org/bot{token}/getMe")
            resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            return None
        result = data.get("result") or {}
        return {
            "id": result.get("id"),
            "username": result.get("username") or "",
            "first_name": result.get("first_name") or "",
        }
    except Exception:
        return None


async def show_private_home(update, context, state: dict):
    me = await context.bot.get_me()
    username = f"@{me.username}" if getattr(me, "username", None) else "bot"
    text = "\n".join([
        f"{username} 能帮你更安全地管理频道和群组。",
        "",
        "💬 请先把我设为频道或群组管理员。",
    ])
    rows = [
        [_btn("设置频道", "admin:home:channels"), _btn("设置群组", "admin:home:groups")],
        [_btn("快捷发布", "admin:home:quick"), _btn("订阅会员", "admin:home:membership")],
        [_btn("克隆", "admin:home:clone")],
        [_btn("设置时区", "admin:home:timezone"), _btn("语言设置", "admin:home:language")],
    ]
    await _send_or_edit(update, text, InlineKeyboardMarkup(rows))


async def show_channel_placeholder(update, context):
    text = "\n".join([
        "\u8bbe\u7f6e\u9891\u9053",
        "\u8bf7\u5148\u628a\u673a\u5668\u4eba\u52a0\u5165\u76ee\u6807\u9891\u9053\u5e76\u6388\u4e88\u7ba1\u7406\u5458\u6743\u9650\u3002",
        "\u9891\u9053\u5217\u8868\u548c\u9891\u9053\u4e13\u5c5e\u914d\u7f6e\u4f1a\u5728\u4e0b\u4e00\u9636\u6bb5\u63a5\u5165\u3002",
    ])
    rows = [
        [_btn("\u8bbe\u7f6e\u7fa4\u7ec4", "admin:home:groups")],
        [_btn("\u9996\u9875", "admin:home")],
    ]
    await _send_or_edit(update, text, InlineKeyboardMarkup(rows))


async def show_quick_publish_placeholder(update, context):
    text = "\n".join([
        "\u5feb\u6377\u53d1\u5e03",
        "\u5feb\u6377\u53d1\u5e03\u5165\u53e3\u5df2\u9884\u7559\u3002",
        "\u540e\u7eed\u4f1a\u63a5\u5165\u9891\u9053/\u7fa4\u7ec4\u5feb\u6377\u6295\u9012\u4e0e\u7d20\u6750\u590d\u7528\u3002",
    ])
    await _send_or_edit(update, text, InlineKeyboardMarkup([[_btn("\u9996\u9875", "admin:home")]]))


async def show_membership_menu(update, context, user_id: int):
    profile = get_user_profile(user_id)
    offset = int(profile.get("timezone_offset", 8) or 8)
    expire_text = _format_expire_text((profile.get("membership") or {}).get("expires_at", 0), offset)
    text = "\n".join([
        "\U0001f48e \u9ad8\u7ea7\u4f1a\u5458\u4e13\u5c5e\u529f\u80fd",
        f"\u5230\u671f\u65f6\u95f4: {expire_text}",
        "",
        "\u5f00\u901a\u4f1a\u5458\uff0c\u89e3\u9501\u5168\u9762\u8fdb\u9636\u529f\u80fd\uff0c\u52a9\u4f60\u9ad8\u6548\u7ba1\u7406\u9891\u9053\u4e0e\u7fa4\u7ec4\uff01",
        "",
        "1. \u5b9a\u65f6\u6d88\u606f\u6570\u91cf\u63d0\u5347\uff1a5 -> 50",
        "2. \u9891\u9053\u540c\u6b65\u6570\u91cf\u63d0\u5347\uff1a2 -> 20",
        "3. \u7fa4\u7ec4\u81ea\u52a8\u56de\u590d\u6269\u5c55\uff1a10 -> 200",
        "4. \u652f\u6301\u5c4f\u853d\u95ea\u8fdb\u95ea\u9000",
        "5. \u4efb\u52a1\u4f18\u5148\u5904\u7406\u66f4\u7a33\u5b9a",
    ])
    rows = [
        [_btn(MEMBERSHIP_PLANS["m1"]["label"], "admin:home:membership:buy:m1"), _btn(MEMBERSHIP_PLANS["m3"]["label"], "admin:home:membership:buy:m3")],
        [_btn(MEMBERSHIP_PLANS["m6"]["label"], "admin:home:membership:buy:m6"), _btn(MEMBERSHIP_PLANS["m12"]["label"], "admin:home:membership:buy:m12")],
        [_btn("\u9996\u9875", "admin:home")],
    ]
    await _send_or_edit(update, text, InlineKeyboardMarkup(rows))


async def show_timezone_menu(update, context, user_id: int):
    profile = get_user_profile(user_id)
    offset = int(profile.get("timezone_offset", 8) or 8)
    text = "\n".join([
        f"\u60a8\u7684\u9ed8\u8ba4\u65f6\u533a: {_offset_label(offset)}",
        f"\u5f53\u524d\u65f6\u95f4: {_now_text(offset)}",
        "",
        "\u70b9\u51fb\u4e0b\u65b9\u6309\u94ae\u8bbe\u7f6e\u9ed8\u8ba4\u65f6\u533a:\n",
    ])
    rows = [[_btn("UTC", "admin:home:timezone:none")]]
    for values in ([n for n in range(1, 13)], [n for n in range(-12, 0)]):
        items = []
        for val in values:
            label = _offset_label(val)
            if val == offset:
                label = f"\u2705 {label}"
            items.append(_btn(label, f"admin:home:timezone:set:{val}"))
        rows.extend(_rows(items, 4))
    rows.append([_btn("\u9996\u9875", "admin:home")])
    await _send_or_edit(update, text, InlineKeyboardMarkup(rows))


async def show_language_menu(update, context, user_id: int):
    profile = get_user_profile(user_id)
    current = str(profile.get("language") or "zh_cn")
    items = []
    for code, label in LANGUAGE_OPTIONS:
        display = f"\u2705 {label}" if code == current else label
        items.append(_btn(display, f"admin:home:language:set:{code}"))
    rows = _rows(items, 2)
    rows.append([_btn("\u8bbe\u7f6e\u65f6\u533a", "admin:home:timezone"), _btn("\u9996\u9875", "admin:home")])
    await _send_or_edit(update, "🌐 请选择界面语言", InlineKeyboardMarkup(rows))


async def show_clone_menu(update, context, user_id: int):
    profile = get_user_profile(user_id)
    clones = list(profile.get("clone_bots") or [])
    ready_count = launch_ready_clone_count(user_id)
    text = "\n".join([
        "\U0001f916\u514b\u9686",
        "",
        "\u514b\u9686\u673a\u5668\u4eba\u529f\u80fd\u5b8c\u5168\u76f8\u540c\uff0c\u4f60\u53ef\u4ee5\u81ea\u5b9a\u4e49\u540d\u5b57\u3001\u5934\u50cf\u548c /start \u5185\u5bb9\u3002",
        f"\u5f53\u524d\u5df2\u4fdd\u5b58 {len(clones)} \u4e2a\u673a\u5668\u4eba\u4ee4\u724c\uff0c\u5f85\u542f\u52a8 {ready_count} \u4e2a\u3002",
        "",
        "\u521b\u5efa\u6d41\u7a0b\uff1a",
        "1. \u6253\u5f00 @BotFather \u53d1\u9001 /newbot",
        "2. \u5b8c\u6210\u673a\u5668\u4eba\u540d\u79f0\u4e0e\u7528\u6237\u540d\u8bbe\u7f6e",
        "3. \u628a BotFather \u8fd4\u56de\u7684\u673a\u5668\u4eba\u4ee4\u724c\u53d1\u7ed9\u672c\u673a\u5668\u4eba",
        "4. \u4ee4\u724c\u6821\u9a8c\u901a\u8fc7\u540e\u751f\u6210\u4eba\u5de5\u5ba1\u6838\u8ba2\u5355",
    ])
    rows = []
    for clone in clones[:8]:
        rows.append([_btn(_mask_username(clone.get("username")), f"admin:home:clone:view:{clone.get('request_id')}")])
    rows.append([_btn("\u63d0\u4ea4\u673a\u5668\u4eba\u4ee4\u724c", "admin:home:clone:token")])
    rows.append([_btn("\u9996\u9875", "admin:home")])
    await _send_or_edit(update, text, InlineKeyboardMarkup(rows))


async def show_clone_detail_menu(update, context, user_id: int, clone_id: str):
    profile = get_user_profile(user_id)
    clone = _find_clone(profile, clone_id)
    if not clone:
        if getattr(update, "callback_query", None):
            await safe_answer(update.callback_query, "\u672a\u627e\u5230\u8be5\u514b\u9686\u673a\u5668\u4eba", show_alert=True)
        return
    offset = int(profile.get("timezone_offset", 8) or 8)
    launch_state = clone_launch_state(clone)
    launch_hint = "- \u540e\u53f0\u72b6\u6001: \u6682\u672a\u8fdb\u5165\u5f85\u542f\u52a8\u6e05\u5355"
    if launch_state == "launch_ready":
        launch_hint = "- \u540e\u53f0\u72b6\u6001: \u5df2\u5199\u5165\u5f85\u542f\u52a8\u6e05\u5355"
    elif launch_state == "expired":
        launch_hint = "- \u540e\u53f0\u72b6\u6001: \u5df2\u8fc7\u671f\uff0c\u9700\u7eed\u8d39\u540e\u91cd\u65b0\u5165\u961f"
    text = "\n".join([
        f"\u5f00\u901a\u514b\u9686\u673a\u5668\u4eba: {_mask_username(clone.get('username'))}",
        f"\u5f53\u524d\u72b6\u6001: {_clone_status_text(clone, offset)}",
        launch_hint,
        "- \u652f\u6301\u4fee\u6539\u5934\u50cf\u3001\u540d\u79f0\u3001\u7b80\u4ecb",
        "- \u652f\u6301\u4fee\u6539 /start \u4ecb\u7ecd\u4e0e\u6309\u94ae",
        "- \u652f\u6301\u4fee\u6539\u5168\u5c40\u9ed8\u8ba4\u8bed\u8a00",
        "- \u5f00\u901a\u540e\u4fdd\u7559\u4ee4\u724c\uff0c\u5f85\u7ba1\u7406\u5458\u542f\u52a8",
    ])
    rows = [
        [_btn(CLONE_PLANS["c1"]["label"], f"admin:home:clone:buy:{clone_id}:c1")],
        [_btn(CLONE_PLANS["c12"]["label"], f"admin:home:clone:buy:{clone_id}:c12")],
        [_btn("\u8fd4\u56de\u514b\u9686", "admin:home:clone"), _btn("\u9996\u9875", "admin:home")],
    ]
    await _send_or_edit(update, text, InlineKeyboardMarkup(rows))


def _build_order_summary(order: dict) -> str:
    lines = [
        "\u4eba\u5de5\u5ba1\u6838\u8ba2\u5355",
        f"\u8ba2\u5355\u53f7: {order.get('order_id')}",
        f"\u7c7b\u578b: {order.get('kind_label')}",
        f"\u5957\u9910: {order.get('plan_label')}",
        f"\u91d1\u989d: {order.get('amount')}U",
        f"\u72b6\u6001: {order.get('status_label', '\u5f85\u4eba\u5de5\u786e\u8ba4')}",
    ]
    target = order.get("target_label")
    if target:
        lines.append(f"\u76ee\u6807: {target}")
    return "\n".join(lines)


async def _notify_super_admin_order(context, order: dict):
    rows = [[
        _btn("\u786e\u8ba4\u5f00\u901a", f"admin:billing:approve:{order.get('order_id')}"),
        _btn("\u9a73\u56de", f"admin:billing:reject:{order.get('order_id')}"),
    ]]
    try:
        await context.bot.send_message(
            chat_id=SUPER_ADMIN_ID,
            text=_build_order_summary(order),
            reply_markup=InlineKeyboardMarkup(rows),
        )
    except TelegramError:
        pass


async def _notify_user_order_created(update, order: dict):
    text = "\n".join([
        "\u8ba2\u5355\u5df2\u521b\u5efa\uff0c\u7b49\u5f85\u7ba1\u7406\u5458\u4eba\u5de5\u786e\u8ba4\u3002",
        f"\u8ba2\u5355\u53f7: {order.get('order_id')}",
        f"\u5957\u9910: {order.get('plan_label')}",
        f"\u91d1\u989d: {order.get('amount')}U",
        "\u8bf7\u8054\u7cfb\u7ba1\u7406\u5458\u786e\u8ba4\u5f00\u901a\u3002",
    ])
    if update.effective_message:
        await update.effective_message.reply_text(text)
    elif getattr(update, 'callback_query', None) and update.callback_query.message:
        await update.callback_query.message.reply_text(text)


def _create_membership_order(user, plan_code: str) -> dict:
    plan = MEMBERSHIP_PLANS[plan_code]
    order_id = _new_order_id("sub")
    return {
        "order_id": order_id,
        "user_id": user.id,
        "username": getattr(user, "username", "") or "",
        "display_name": getattr(user, "full_name", "") or str(user.id),
        "kind": "membership",
        "kind_label": "\u4f1a\u5458\u8ba2\u9605",
        "plan_code": plan_code,
        "plan_label": plan["label"],
        "amount": plan["amount"],
        "days": plan["days"],
        "status": "pending",
        "status_label": "\u5f85\u4eba\u5de5\u786e\u8ba4",
        "created_at": int(time.time()),
        "target_label": "\u4f1a\u5458\u6743\u9650",
    }


def _create_clone_order(user, clone: dict, plan_code: str) -> dict:
    plan = CLONE_PLANS[plan_code]
    order_id = _new_order_id("cln")
    return {
        "order_id": order_id,
        "user_id": user.id,
        "username": getattr(user, "username", "") or "",
        "display_name": getattr(user, "full_name", "") or str(user.id),
        "kind": "clone",
        "kind_label": "\u514b\u9686\u673a\u5668\u4eba",
        "plan_code": plan_code,
        "plan_label": plan["label"],
        "amount": plan["amount"],
        "days": plan["days"],
        "status": "pending",
        "status_label": "\u5f85\u4eba\u5de5\u786e\u8ba4",
        "created_at": int(time.time()),
        "clone_request_id": clone.get("request_id"),
        "target_label": _mask_username(clone.get("username")),
    }


async def _create_and_dispatch_order(update, context, order: dict):
    user_id = int(order.get("user_id"))
    profile = get_user_profile(user_id)
    _append_user_order(profile, order["order_id"])
    save_user_profile(user_id, profile)
    save_manual_order(order["order_id"], order)
    await _notify_super_admin_order(context, order)
    await _notify_user_order_created(update, order)


async def _apply_order_decision(context, order: dict, approved: bool, approver_id: int):
    user_id = int(order.get("user_id"))
    profile = get_user_profile(user_id)
    now_ts = int(time.time())
    order["status"] = "approved" if approved else "rejected"
    order["status_label"] = "\u5df2\u786e\u8ba4" if approved else "\u5df2\u9a73\u56de"
    order["approved_at"] = now_ts
    order["approver_id"] = approver_id
    if approved:
        if order.get("kind") == "membership":
            current_expiry = int((profile.get("membership") or {}).get("expires_at", 0) or 0)
            base_ts = max(now_ts, current_expiry)
            profile.setdefault("membership", {})["expires_at"] = base_ts + int(order.get("days", 0)) * 86400
            save_user_profile(user_id, profile)
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"\u4f60\u7684\u4f1a\u5458\u8ba2\u5355\u5df2\u786e\u8ba4\uff0c\u5230\u671f\u65f6\u95f4\uff1a{_format_expire_text(profile['membership']['expires_at'], int(profile.get('timezone_offset', 8) or 8))}",
                )
            except TelegramError:
                pass
        elif order.get("kind") == "clone":
            clone = _find_clone(profile, str(order.get("clone_request_id") or ""))
            if clone:
                current_expiry = int(clone.get("expires_at", 0) or 0)
                base_ts = max(now_ts, current_expiry)
                clone["expires_at"] = base_ts + int(order.get("days", 0)) * 86400
                clone["status"] = "approved_pending_launch"
                clone["plan_code"] = order.get("plan_code")
                clone["approved_at"] = now_ts
                save_user_profile(user_id, profile)
                save_clone_launch_request(str(clone.get("request_id")), _build_clone_launch_payload(user_id, profile, clone))
                try:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=f"\u4f60\u7684\u514b\u9686\u673a\u5668\u4eba\u8ba2\u5355\u5df2\u786e\u8ba4\uff1a{_mask_username(clone.get('username'))}\n\u5f53\u524d\u72b6\u6001\uff1a\u5f85\u7ba1\u7406\u5458\u542f\u52a8",
                    )
                except TelegramError:
                    pass
    else:
        if order.get("kind") == "clone":
            clear_clone_launch_request(str(order.get("clone_request_id") or ""))
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"\u4f60\u7684\u8ba2\u5355 {order.get('order_id')} \u5df2\u88ab\u9a73\u56de\uff0c\u5982\u6709\u7591\u95ee\u8bf7\u8054\u7cfb\u7ba1\u7406\u5458\u3002",
            )
        except TelegramError:
            pass
    save_manual_order(order["order_id"], order)


async def handle_private_home_callback(update, context, state: dict) -> bool:
    query = getattr(update, "callback_query", None)
    data = getattr(query, "data", "") or ""
    if not data.startswith("admin:home") and not data.startswith("admin:billing:"):
        return False

    user_id = update.effective_user.id

    if data == "admin:home":
        new_state = _state_with(state, active_group_id=None, state=STATE_NONE, tmp={})
        _save_state(user_id, new_state)
        await show_private_home(update, context, new_state)
        return True

    if data == "admin:home:groups":
        new_state = _state_with(state, active_group_id=None, state=STATE_NONE, tmp={})
        _save_state(user_id, new_state)
        from .admin import show_group_select
        await show_group_select(update, context, new_state)
        return True

    if data == "admin:home:channels":
        await show_channel_placeholder(update, context)
        return True

    if data == "admin:home:quick":
        await show_quick_publish_placeholder(update, context)
        return True

    if data == "admin:home:membership":
        await show_membership_menu(update, context, user_id)
        return True

    if data.startswith("admin:home:membership:buy:"):
        plan_code = data.rsplit(":", 1)[-1]
        if plan_code in MEMBERSHIP_PLANS:
            order = _create_membership_order(update.effective_user, plan_code)
            await _create_and_dispatch_order(update, context, order)
        return True

    if data == "admin:home:clone":
        await show_clone_menu(update, context, user_id)
        return True

    if data == "admin:home:clone:token":
        new_state = _state_with(state, active_group_id=None, state=STATE_HOME_CLONE_TOKEN, tmp={})
        _save_state(user_id, new_state)
        await _send_prompt(update, "\u8bf7\u53d1\u9001\u4ece @BotFather \u83b7\u53d6\u7684\u673a\u5668\u4eba\u4ee4\u724c\uff0c\u652f\u6301\u76f4\u63a5\u7c98\u8d34\u3002")
        return True

    if data.startswith("admin:home:clone:view:"):
        clone_id = data.rsplit(":", 1)[-1]
        await show_clone_detail_menu(update, context, user_id, clone_id)
        return True

    if data.startswith("admin:home:clone:buy:"):
        parts = data.split(":")
        if len(parts) >= 6:
            clone_id = parts[-2]
            plan_code = parts[-1]
            profile = get_user_profile(user_id)
            clone = _find_clone(profile, clone_id)
            if clone and plan_code in CLONE_PLANS:
                order = _create_clone_order(update.effective_user, clone, plan_code)
                await _create_and_dispatch_order(update, context, order)
            else:
                await safe_answer(query, "\u672a\u627e\u5230\u5bf9\u5e94\u514b\u9686\u673a\u5668\u4eba", show_alert=True)
        return True

    if data == "admin:home:timezone":
        await show_timezone_menu(update, context, user_id)
        return True

    if data == "admin:home:timezone:none":
        return True

    if data.startswith("admin:home:timezone:set:"):
        offset = int(data.rsplit(":", 1)[-1])
        profile = get_user_profile(user_id)
        profile["timezone_offset"] = offset
        save_user_profile(user_id, profile)
        await show_timezone_menu(update, context, user_id)
        return True

    if data == "admin:home:language":
        await show_language_menu(update, context, user_id)
        return True

    if data.startswith("admin:home:language:set:"):
        code = data.rsplit(":", 1)[-1]
        if code in LANGUAGE_LABELS:
            profile = get_user_profile(user_id)
            profile["language"] = code
            save_user_profile(user_id, profile)
            await show_language_menu(update, context, user_id)
        return True

    if data.startswith("admin:billing:"):
        if int(user_id) != int(SUPER_ADMIN_ID):
            await safe_answer(query, "\u65e0\u6743\u9650", show_alert=True)
            return True
        parts = data.split(":", 3)
        if len(parts) < 4:
            return True
        action = parts[2]
        order_id = parts[3]
        order = get_manual_order(order_id)
        if not order:
            await safe_answer(query, "\u8ba2\u5355\u4e0d\u5b58\u5728", show_alert=True)
            return True
        if order.get("status") != "pending":
            await safe_answer(query, f"\u8ba2\u5355\u5df2\u5904\u7406\uff1a{order.get('status_label', order.get('status'))}", show_alert=True)
            return True
        approved = action == "approve"
        await _apply_order_decision(context, order, approved, user_id)
        status_text = "\u5df2\u786e\u8ba4\u5f00\u901a" if approved else "\u5df2\u9a73\u56de"
        base_text = query.message.text or _build_order_summary(order)
        await safe_edit_message(query, f"{base_text}\n\n\u5904\u7406\u7ed3\u679c\uff1a{status_text}", reply_markup=None)
        return True

    return False


async def handle_private_home_message(update, context, state: dict, msg_text: str) -> bool:
    if (state or {}).get("state") != STATE_HOME_CLONE_TOKEN:
        return False
    match = TOKEN_RE.search(msg_text or "")
    if not match:
        await update.effective_message.reply_text("\u672a\u8bc6\u522b\u5230\u673a\u5668\u4eba\u4ee4\u724c\uff0c\u8bf7\u76f4\u63a5\u53d1\u9001 BotFather \u8fd4\u56de\u7684\u4ee4\u724c\u6587\u672c\u3002")
        return True
    token = match.group(1).strip()
    identity = await _fetch_bot_identity(token)
    if not identity:
        await update.effective_message.reply_text("\u673a\u5668\u4eba\u4ee4\u724c\u6821\u9a8c\u5931\u8d25\uff0c\u8bf7\u786e\u8ba4\u4ee4\u724c\u6765\u81ea @BotFather\uff0c\u4e14\u673a\u5668\u4eba\u5df2\u521b\u5efa\u6210\u529f\u3002")
        return True
    profile = get_user_profile(update.effective_user.id)
    clone = _upsert_clone_bot(profile, token, identity)
    save_user_profile(update.effective_user.id, profile)
    if clone_launch_state(clone) == "launch_ready":
        save_clone_launch_request(str(clone.get("request_id")), _build_clone_launch_payload(update.effective_user.id, profile, clone))
    else:
        clear_clone_launch_request(str(clone.get("request_id")))
    new_state = _state_with(state, state=STATE_NONE, tmp={})
    _save_state(update.effective_user.id, new_state)
    await show_clone_detail_menu(update, context, update.effective_user.id, clone.get("request_id"))
    return True
