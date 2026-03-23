from __future__ import annotations

import copy
import html
import os
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import TelegramError

from ..models import config as model_config
from ..services.membership import auto_reply_limit_for_group, group_service_owner_id, maybe_bind_group_service_owner
from ..services.verify import get_verify_message, parse_target_input, set_verify_message, verify_mode_label
from ..storage.config_store import (
    get_admin_state,
    get_group_anti_spam,
    get_group_auto_ban,
    get_group_auto_delete,
    get_group_auto_mute,
    get_group_auto_replies,
    get_group_auto_warn,
    get_group_config,
    get_group_targets,
    get_known_groups,
    save_admin_state,
    save_group_anti_spam,
    save_group_auto_ban,
    save_group_auto_delete,
    save_group_auto_mute,
    save_group_auto_replies,
    save_group_auto_warn,
    save_group_config,
    save_group_targets,
)
from ..utils.telegram import get_bot_username, is_admin, normalize_url, safe_answer, safe_edit_message
from .private_home import handle_private_home_callback, handle_private_home_message, show_private_home
from ..web.login_bot import parse_web_login_start_arg, show_web_login_prompt

SUPER_ADMIN_ID = model_config.SUPER_ADMIN_ID
STATE_NONE = model_config.STATE_NONE
STATE_TARGET_INPUT = model_config.STATE_TARGET_INPUT
STATE_WELCOME_TEXT = model_config.STATE_WELCOME_TEXT
STATE_WELCOME_PHOTO = model_config.STATE_WELCOME_PHOTO
STATE_WELCOME_TTL = model_config.STATE_WELCOME_TTL
STATE_VERIFY_TEXT = model_config.STATE_VERIFY_TEXT
STATE_VERIFY_PHOTO = model_config.STATE_VERIFY_PHOTO
STATE_VERIFY_TIMEOUT = model_config.STATE_VERIFY_TIMEOUT
STATE_VERIFY_FAIL_TEXT = model_config.STATE_VERIFY_FAIL_TEXT
STATE_BTN_TEXT = model_config.STATE_BTN_TEXT
STATE_BTN_VALUE = model_config.STATE_BTN_VALUE
STATE_BTN_ROW = model_config.STATE_BTN_ROW
STATE_AR_KEYWORD = model_config.STATE_AR_KEYWORD
STATE_AR_TEXT = model_config.STATE_AR_TEXT
STATE_AR_PHOTO = model_config.STATE_AR_PHOTO
STATE_AD_RULE_KEYWORD = model_config.STATE_AD_RULE_KEYWORD
STATE_AB_KEYWORD = model_config.STATE_AB_KEYWORD
STATE_AB_DURATION = model_config.STATE_AB_DURATION
STATE_AM_KEYWORD = model_config.STATE_AM_KEYWORD
STATE_AM_DURATION = model_config.STATE_AM_DURATION
STATE_AW_RULE_KEYWORD = model_config.STATE_AW_RULE_KEYWORD
STATE_AW_LIMIT = model_config.STATE_AW_LIMIT
STATE_AW_MUTE = model_config.STATE_AW_MUTE
STATE_AW_TEXT = model_config.STATE_AW_TEXT
STATE_SPAM_WINDOW = model_config.STATE_SPAM_WINDOW
STATE_SPAM_THRESHOLD = model_config.STATE_SPAM_THRESHOLD
STATE_SPAM_MUTE = model_config.STATE_SPAM_MUTE

KV_ENABLED = True


VERIFY_MODES = ["join", "calc", "image_calc", "captcha"]
SPAM_TYPE_LABELS = {
    "text": "文本",
    "photo": "图片",
    "video": "视频",
    "document": "文件",
    "voice": "语音",
    "sticker": "贴纸",
    "link": "链接",
}
RULE_MODE_LABELS = {
    "exact": "精确",
    "contains": "包含",
    "regex": "正则",
}
VERIFY_FAIL_ACTION_LABELS = {
    "mute": "禁言",
    "ban": "封禁",
    "kick": "踢出",
}


def _button_icon(data: str, label: str) -> str:
    text = str(label or "").strip()
    route = str(data or "")
    if route == "admin:home":
        return "🏠"
    if route == "admin:main":
        return "🏠" if "主菜单" in text else "↩️"
    if route == "admin:groups":
        return "🔄"
    if route.startswith("admin:select_group:"):
        return "🧭"
    if route == "admin:none":
        return "📭"
    if route.startswith("admin:cancel"):
        return "❌"
    if route == "admin:btn_type:url":
        return "🔗"
    if route == "admin:btn_type:callback":
        return "🧩"
    if ":text" in route:
        return "📝"
    if "clear:photo" in route or "photo:clear" in route:
        return "🧽"
    if ":photo" in route:
        return "🖼️"
    if ":buttons" in route:
        return "🔘"
    if ":add" in route:
        return "➕"
    if ":del:" in route or route.endswith(":del") or ":clear" in route:
        return "🗑️"
    if ":edit" in route:
        return "✏️"
    if any(token in route for token in (":timeout", ":ttl", ":duration", ":window", ":threshold", ":mute", ":limit")):
        return "⏱️"
    if route.endswith(":best"):
        return "⭐"
    module_icons = (
        ("admin:verify", "🛂"),
        ("admin:welcome", "👋"),
        ("admin:auto", "💬"),
        ("admin:del", "🧹"),
        ("admin:ab", "⛔"),
        ("admin:am", "🔇"),
        ("admin:aw", "⚠️"),
        ("admin:spam", "🚫"),
        ("adminx:invite", "🔗"),
        ("adminx:member", "👥"),
        ("adminx:fun", "🎮"),
        ("adminx:lang", "🌐"),
        ("adminx:crypto", "💎"),
        ("adminx:related", "📡"),
        ("adminx:admin_access", "🛡️"),
        ("adminx:nsfw", "🔞"),
        ("adminx:schedule", "⏰"),
        ("adminx:ad", "📵"),
        ("adminx:cmd", "⌨️"),
        ("adminx:lottery", "🎁"),
        ("adminx:verified", "🪪"),
    )
    for prefix, icon in module_icons:
        if route.startswith(prefix):
            return icon
    if text.startswith("返回"):
        return "↩️"
    return "🔹"


def _button_text(label: str, data: str) -> str:
    text = str(label or "").strip()
    text = {
        "Fun": "娱乐功能",
        "Admin Access": "管理权限",
        "NSFW": "NSFW过滤",
        "Schedule": "定时消息",
        "Lottery": "抽奖活动",
        "Verified": "认证用户",
        "URL": "链接",
    }.get(text, text)
    icon = _button_icon(data, text)
    return text if text.startswith(f"{icon} ") else (f"{icon} {text}" if text else icon)


def _btn(label: str, data: str):
    return InlineKeyboardButton(_button_text(label, data), callback_data=data)


def _url_btn(label: str, url: str):
    return InlineKeyboardButton(str(label), url=normalize_url(url))

def _menu_two_cols(items):
    rows = []
    row = []
    for item in list(items or []):
        row.append(item)
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return rows



def _state_with(base_state: dict | None, **kwargs):
    state = dict(base_state or {})
    state.update(kwargs)
    return state



def _save_state(user_id: int, state: dict):
    save_admin_state(user_id, state)



def _current_group(state: dict | None) -> int:
    try:
        return int((state or {}).get("active_group_id") or 0)
    except (TypeError, ValueError):
        return 0



def _verify_preview_text(value: str, fallback: str = "未设置", limit: int = 48) -> str:
    text = str(value or "").strip()
    if not text:
        return fallback
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"



def _cancel_markup():
    return InlineKeyboardMarkup([[_btn("取消输入", "admin:cancel_input")]])



async def _send_or_edit(update, context, text: str, reply_markup=None):
    del context
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



def is_super_admin(user_id: int) -> bool:
    try:
        return int(user_id) == int(SUPER_ADMIN_ID)
    except Exception:
        return False



async def _can_manage_group(context, user_id: int, group_id: int) -> bool:
    if not group_id:
        return False
    if is_super_admin(user_id):
        return True
    try:
        gid = int(group_id)
        uid = int(user_id)
    except Exception:
        return False
    try:
        if not await is_admin(context, gid, uid):
            return False
    except Exception:
        return False
    mode = str((get_group_config(gid).get("admin_access", {}) or {}).get("mode", "all_admins") or "all_admins").strip()
    if mode != "service_owner":
        return True
    owner_id = group_service_owner_id(gid)
    if owner_id == uid:
        return True
    return int(maybe_bind_group_service_owner(gid, uid) or 0) == uid


async def _ensure_active_group_access(update, context, state: dict | None, note: str = "当前群组权限已失效，请重新选择群组。") -> bool:
    group_id = _current_group(state)
    if not group_id:
        return True
    user_id = update.effective_user.id
    if await _can_manage_group(context, user_id, group_id):
        return True
    next_state = _state_with(state, active_group_id=0, state=STATE_NONE, tmp={})
    _save_state(user_id, next_state)
    query = getattr(update, "callback_query", None)
    if query is not None:
        await safe_answer(query, "当前群组权限已失效", show_alert=True)
    await show_group_select(update, context, next_state, note=note)
    return False



async def _manageable_groups(context, user_id: int):
    groups = []
    for item in get_known_groups() or []:
        try:
            group_id = int(item.get("id") or 0)
        except (TypeError, ValueError, AttributeError):
            continue
        if not group_id:
            continue
        if not is_super_admin(user_id) and not await _can_manage_group(context, user_id, group_id):
            continue
        groups.append({"id": group_id, "title": str(item.get("title") or group_id)})
    groups.sort(key=lambda item: (str(item.get("title") or ""), int(item.get("id") or 0)))
    return groups



def _append_group_id(url: str, group_id: int) -> str:
    if not url:
        return ""
    parts = urlsplit(normalize_url(url))
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["group_id"] = str(int(group_id))
    return urlunsplit((parts.scheme, parts.netloc, parts.path or "/web/", urlencode(query), parts.fragment))



async def _web_admin_base_url(context) -> str:
    configured = normalize_url(os.environ.get("WEB_APP_URL", "").strip())
    if configured:
        return configured
    try:
        webhook = await context.bot.get_webhook_info()
        webhook_url = normalize_url(getattr(webhook, "url", "") or "")
        if webhook_url.startswith("http://") or webhook_url.startswith("https://"):
            parts = urlsplit(webhook_url)
            return urlunsplit((parts.scheme, parts.netloc, "/web/", "", ""))
    except TelegramError:
        return ""
    except Exception:
        return ""
    return ""



def _rule_id() -> str:
    return str(int(time.time() * 1000))



def _normalize_buttons(buttons) -> list[dict]:
    normalized = []
    for item in list(buttons or []):
        if not isinstance(item, dict):
            continue
        try:
            row = int(item.get("row", 0) or 0)
        except (TypeError, ValueError):
            row = 0
        normalized.append(
            {
                "text": str(item.get("text") or "按钮"),
                "type": str(item.get("type") or "url"),
                "value": str(item.get("value") or ""),
                "row": max(0, row),
            }
        )
    return normalized



def _normalize_auto_reply_rule(rule: dict | None) -> dict:
    data = dict(rule or {})
    return {
        "id": str(data.get("id") or _rule_id()),
        "keyword": str(data.get("keyword") or ""),
        "mode": str(data.get("mode") or "contains"),
        "reply_text": str(data.get("reply_text") or data.get("response_text") or ""),
        "photo_file_id": str(data.get("photo_file_id") or data.get("response_photo_file_id") or ""),
        "buttons": _normalize_buttons(data.get("buttons") or data.get("response_buttons") or []),
        "enabled": bool(data.get("enabled", True)),
    }



def _normalize_reply_rules(rules) -> list[dict]:
    return [_normalize_auto_reply_rule(rule) for rule in list(rules or []) if isinstance(rule, dict)]



def _find_rule_index(rules, rule_id: str | None) -> int:
    for idx, rule in enumerate(list(rules or [])):
        if str(rule.get("id") or "") == str(rule_id or ""):
            return idx
    return -1



async def show_group_select(update, context, state: dict | None = None, note: str = ""):
    user_id = update.effective_user.id
    groups = await _manageable_groups(context, user_id)
    web_base_url = await _web_admin_base_url(context)
    lines = ["群组设置", "请选择要管理的群组。", "请确保机器人为管理员。"]
    if note:
        lines = [note, "", *lines]
    rows = []
    for group in groups:
        group_id = int(group.get("id") or 0)
        row = [_btn(group.get("title") or group_id, f"admin:select_group:{group_id}")]
        if web_base_url:
            row.append(_url_btn("🌐 进入Web", _append_group_id(web_base_url, group_id)))
        rows.append(row)
    if not rows:
        rows.append([_btn("暂无群组", "admin:none")])
    rows.append([_btn("🏠 首页", "admin:home")])
    await _send_or_edit(update, context, "\n".join(lines), InlineKeyboardMarkup(rows))



async def show_main_menu(update, context, state: dict | None = None, note: str = ""):
    state = dict(state or get_admin_state(update.effective_user.id) or {})
    group_id = _current_group(state)
    if not group_id:
        await show_group_select(update, context, state, note=note or "请先选择群组")
        return
    cfg = get_group_config(group_id)
    lines = [
        "群组管理",
        f"当前群组: {html.escape(str(cfg.get('group_title') or group_id))}",
        "请选择需要调整的功能。",
    ]
    if note:
        lines = [note, "", *lines]
    items = [
        _btn("入群验证", "admin:verify:menu"),
        _btn("欢迎消息", "admin:welcome:menu"),
        _btn("自动回复", "admin:auto:menu"),
        _btn("自动删除", "admin:del:menu"),
        _btn("自动封禁", "admin:ab:menu"),
        _btn("自动禁言", "admin:am:menu"),
        _btn("自动警告", "admin:aw:menu"),
        _btn("禁止刷屏", "admin:spam:menu"),
        _btn("邀请链接", "adminx:invite:menu"),
        _btn("群组成员", "adminx:member:menu"),
        _btn("娱乐功能", "adminx:fun:menu"),
        _btn("语言白名单", "adminx:lang:menu"),
        _btn("地址查询", "adminx:crypto:menu"),
        _btn("关联频道", "adminx:related:menu"),
        _btn("管理权限", "adminx:admin_access:menu"),
        _btn("NSFW过滤", "adminx:nsfw:menu"),
        _btn("定时消息", "adminx:schedule:menu"),
        _btn("广告过滤", "adminx:ad:menu"),
        _btn("群组命令", "adminx:cmd:menu"),
        _btn("抽奖活动", "adminx:lottery:menu"),
        _btn("认证用户", "adminx:verified:menu"),
    ]
    rows = _menu_two_cols(items)
    rows.append([_btn("切换群组", "admin:groups"), _btn("🏠 首页", "admin:home")])
    await _send_or_edit(update, context, "\n".join(lines), InlineKeyboardMarkup(rows))



async def show_targets_menu(update, context, state: dict | None = None, note: str = ""):
    state = dict(state or get_admin_state(update.effective_user.id) or {})
    group_id = _current_group(state)
    if not group_id:
        await show_group_select(update, context, state, note="请先选择群组")
        return
    targets = get_group_targets(group_id)
    lines = ["验证目标", f"当前目标数: {len(targets)}", ""]
    if not targets:
        lines.append("暂无验证目标")
    for idx, target in enumerate(targets):
        title = target.get("title") or target.get("username") or target.get("chat_id") or "-"
        join_url = target.get("join_url") or "-"
        lines.append(f"{idx + 1}. {html.escape(str(title))}")
        lines.append(f"   {html.escape(str(join_url))}")
    if note:
        lines = [note, "", *lines]
    rows = [[_btn("添加目标", "admin:targets:add")]]
    for idx in range(len(targets)):
        rows.append([_btn(f"删除 #{idx + 1}", f"admin:targets:del:{idx}")])
    rows.append([_btn("返回验证消息", "admin:verify:msg:join")])
    await _send_or_edit(update, context, "\n".join(lines), InlineKeyboardMarkup(rows))



async def show_verify_message_menu(update, context, state: dict | None = None, mode: str = "join", note: str = ""):
    state = dict(state or get_admin_state(update.effective_user.id) or {})
    group_id = _current_group(state)
    if not group_id:
        await show_group_select(update, context, state, note="请先选择群组")
        return
    cfg = get_group_config(group_id)
    msg_cfg = get_verify_message(cfg, mode)
    lines = [
        f"{verify_mode_label(mode)}消息设置",
        f"文本: {html.escape(_verify_preview_text(msg_cfg.get('text', ''), limit=64))}",
        f"图片: {'已设置' if msg_cfg.get('photo_file_id') else '未设置'}",
        f"按钮数量: {len(msg_cfg.get('buttons') or [])}",
    ]
    if mode == "join":
        lines.append(f"验证目标: {len(get_group_targets(group_id))} 个")
    if note:
        lines = [note, "", *lines]
    rows = [
        [_btn("设置文本", f"admin:verify:text:{mode}"), _btn("设置图片", f"admin:verify:photo:{mode}")],
        [_btn("清除图片", f"admin:verify:photo:clear:{mode}"), _btn("设置按钮", f"admin:verify:buttons:{mode}")],
    ]
    if mode == "join":
        rows.append([_btn("设置关注目标", "admin:targets")])
    rows.append([_btn("返回验证菜单", "admin:verify:menu")])
    await _send_or_edit(update, context, "\n".join(lines), InlineKeyboardMarkup(rows))



async def show_verify_buttons_menu(update, context, state: dict | None = None, mode: str = "join", note: str = ""):
    state = dict(state or get_admin_state(update.effective_user.id) or {})
    group_id = _current_group(state)
    if not group_id:
        await show_group_select(update, context, state, note="请先选择群组")
        return
    cfg = get_group_config(group_id)
    buttons = list(get_verify_message(cfg, mode).get("buttons") or [])
    lines = [f"{verify_mode_label(mode)}按钮设置", f"按钮数量: {len(buttons)}"]
    for idx, btn in enumerate(buttons):
        lines.append(f"{idx + 1}. {html.escape(str(btn.get('text') or '-'))} ({html.escape(str(btn.get('type') or 'url'))}, 第 {int(btn.get('row', 0)) + 1} 行)")
    if note:
        lines = [note, "", *lines]
    rows = [[_btn("新增按钮", f"admin:verify:buttons:add:{mode}")], [_btn("清空按钮", f"admin:verify:buttons:clear:{mode}")]]
    for idx in range(len(buttons)):
        rows.append([_btn(f"删除 #{idx + 1}", f"admin:verify:buttons:del:{mode}:{idx}")])
    rows.append([_btn("返回消息设置", f"admin:verify:msg:{mode}")])
    await _send_or_edit(update, context, "\n".join(lines), InlineKeyboardMarkup(rows))



async def show_verify_menu(update, context, state: dict | None = None, note: str = ""):
    state = dict(state or get_admin_state(update.effective_user.id) or {})
    group_id = _current_group(state)
    if not group_id:
        await show_group_select(update, context, state, note="请先选择群组")
        return
    cfg = get_group_config(group_id)
    enabled = bool(cfg.get("verify_enabled", True))
    mode = str(cfg.get("verify_mode") or "join")
    timeout = int(cfg.get("verify_timeout_sec", 60) or 60)
    fail_action = str(cfg.get("verify_fail_action") or "mute")
    fail_action_label = VERIFY_FAIL_ACTION_LABELS.get(fail_action, fail_action)
    private = bool(cfg.get("verify_private", False))
    lines = [
        "入群验证",
        f"当前状态: {'已开启' if enabled else '已关闭'}",
        f"当前模式: {verify_mode_label(mode)}",
        f"验证时长: {timeout} 秒",
        f"失败处理: {fail_action_label}",
        f"私聊验证: {'已开启' if private else '已关闭'}",
        f"验证失败消息: {html.escape(_verify_preview_text(cfg.get('verify_fail_text', ''), limit=48))}",
    ]
    if note:
        lines = [note, "", *lines]
    rows = [
        [_btn(f"{'✅' if enabled else '❌'} 开启入群验证", "admin:verify_toggle")],
        [_btn(f"{'✅' if private else '❌'} 开启私聊验证", "admin:verify_private")],
        [_btn("验证失败消息", "admin:verify:fail:text"), _btn(f"验证时长 {timeout} 秒", "admin:verify:timeout")],
        [_btn("关注频道消息", "admin:verify:msg:join"), _btn("计算消息", "admin:verify:msg:calc")],
        [_btn("图片计算消息", "admin:verify:msg:image_calc"), _btn("验证码消息", "admin:verify:msg:captcha")],
        [_btn(f"{'✅' if mode == 'join' else '▫️'} 关注频道", "admin:verify:mode:set:join"), _btn(f"{'✅' if mode == 'calc' else '▫️'} 计算", "admin:verify:mode:set:calc")],
        [_btn(f"{'✅' if mode == 'image_calc' else '▫️'} 图片计算", "admin:verify:mode:set:image_calc"), _btn(f"{'✅' if mode == 'captcha' else '▫️'} 验证码", "admin:verify:mode:set:captcha")],
        [_btn(f"{'✅' if fail_action == 'ban' else '▫️'} 封禁", "admin:verify:fail:action:ban"), _btn(f"{'✅' if fail_action == 'mute' else '▫️'} 禁言", "admin:verify:fail:action:mute")],
        [_btn(f"{'✅' if fail_action == 'kick' else '▫️'} 踢出", "admin:verify:fail:action:kick"), _btn("最佳配置", "admin:verify:best")],
        [_btn("返回主菜单", "admin:main")],
    ]
    await _send_or_edit(update, context, "\n".join(lines), InlineKeyboardMarkup(rows))



async def show_welcome_message_menu(update, context, state: dict | None = None, note: str = ""):
    state = dict(state or get_admin_state(update.effective_user.id) or {})
    group_id = _current_group(state)
    if not group_id:
        await show_group_select(update, context, state, note="请先选择群组")
        return
    cfg = get_group_config(group_id)
    lines = [
        "欢迎消息设置",
        f"文本: {html.escape(_verify_preview_text(cfg.get('welcome_text', ''), limit=64))}",
        f"图片: {'已设置' if cfg.get('welcome_photo_file_id') else '未设置'}",
        f"按钮数量: {len(cfg.get('welcome_buttons') or [])}",
    ]
    if note:
        lines = [note, "", *lines]
    rows = [
        [_btn("设置文本", "admin:welcome:text"), _btn("设置图片", "admin:welcome:photo")],
        [_btn("清除图片", "admin:welcome:photo:clear"), _btn("设置按钮", "admin:welcome:buttons")],
        [_btn("返回欢迎菜单", "admin:welcome:menu")],
    ]
    await _send_or_edit(update, context, "\n".join(lines), InlineKeyboardMarkup(rows))



async def show_welcome_buttons_menu(update, context, state: dict | None = None, note: str = ""):
    state = dict(state or get_admin_state(update.effective_user.id) or {})
    group_id = _current_group(state)
    if not group_id:
        await show_group_select(update, context, state, note="请先选择群组")
        return
    cfg = get_group_config(group_id)
    buttons = list(cfg.get("welcome_buttons") or [])
    lines = ["欢迎按钮设置", f"按钮数量: {len(buttons)}"]
    for idx, btn in enumerate(buttons):
        lines.append(f"{idx + 1}. {html.escape(str(btn.get('text') or '-'))} ({html.escape(str(btn.get('type') or 'url'))}, 第 {int(btn.get('row', 0)) + 1} 行)")
    if note:
        lines = [note, "", *lines]
    rows = [[_btn("新增按钮", "admin:welcome:buttons:add")], [_btn("清空按钮", "admin:welcome:buttons:clear")]]
    for idx in range(len(buttons)):
        rows.append([_btn(f"删除 #{idx + 1}", f"admin:welcome:buttons:del:{idx}")])
    rows.append([_btn("返回欢迎消息", "admin:welcome:edit")])
    await _send_or_edit(update, context, "\n".join(lines), InlineKeyboardMarkup(rows))



async def show_welcome_menu(update, context, state: dict | None = None, note: str = ""):
    state = dict(state or get_admin_state(update.effective_user.id) or {})
    group_id = _current_group(state)
    if not group_id:
        await show_group_select(update, context, state, note="请先选择群组")
        return
    cfg = get_group_config(group_id)
    enabled = bool(cfg.get("welcome_enabled", True))
    ttl = int(cfg.get("welcome_ttl_sec", 0) or 0)
    delete_prev = bool(cfg.get("welcome_delete_prev", False))
    lines = [
        "欢迎消息",
        f"当前状态: {'已开启' if enabled else '已关闭'}",
        f"倒计时删除: {ttl} 秒",
        f"删除上一条: {'已开启' if delete_prev else '已关闭'}",
        f"文本预览: {html.escape(_verify_preview_text(cfg.get('welcome_text', ''), limit=48))}",
    ]
    if note:
        lines = [note, "", *lines]
    rows = [
        [_btn(f"{'✅' if enabled else '❌'} 开启欢迎消息", "admin:welcome_toggle")],
        [_btn("编辑欢迎消息", "admin:welcome:edit")],
        [_btn(f"{'✅' if delete_prev else '❌'} 删除上一条", "admin:welcome_delete_prev"), _btn(f"设置倒计时 {ttl} 秒", "admin:welcome:ttl")],
        [_btn("返回主菜单", "admin:main")],
    ]
    await _send_or_edit(update, context, "\n".join(lines), InlineKeyboardMarkup(rows))

async def show_auto_reply_menu(update, context, state: dict | None = None, note: str = ""):
    state = dict(state or get_admin_state(update.effective_user.id) or {})
    group_id = _current_group(state)
    if not group_id:
        await show_group_select(update, context, state, note="请先选择群组")
        return
    rules = _normalize_reply_rules(get_group_auto_replies(group_id))
    save_group_auto_replies(group_id, rules)
    limit = auto_reply_limit_for_group(group_id)
    lines = ["自动回复", f"当前规则数: {len(rules)}/{limit}", ""]
    if not rules:
        lines.append("暂无规则")
    for idx, rule in enumerate(rules):
        lines.append(
            f"{idx + 1}. {'✅' if rule.get('enabled', True) else '❌'} {html.escape(rule.get('keyword') or '-') } ({RULE_MODE_LABELS.get(rule.get('mode'), '包含')})"
        )
    if note:
        lines = [note, "", *lines]
    rows = [[_btn("新增规则", "admin:auto:add")]]
    for idx, rule in enumerate(rules):
        rows.append([_btn(f"编辑 #{idx + 1}", f"admin:auto:edit:{rule.get('id')}") , _btn(f"删除 #{idx + 1}", f"admin:auto:del:{rule.get('id')}")])
    rows.append([_btn("返回主菜单", "admin:main")])
    await _send_or_edit(update, context, "\n".join(lines), InlineKeyboardMarkup(rows))



async def show_auto_reply_buttons_menu(update, context, state: dict | None = None, note: str = ""):
    state = dict(state or get_admin_state(update.effective_user.id) or {})
    rule = _normalize_auto_reply_rule((state.get("tmp") or {}).get("ar_rule") or {})
    buttons = list(rule.get("buttons") or [])
    lines = ["自动回复按钮设置", f"按钮数量: {len(buttons)}"]
    for idx, btn in enumerate(buttons):
        lines.append(f"{idx + 1}. {html.escape(str(btn.get('text') or '-'))} ({html.escape(str(btn.get('type') or 'url'))}, 第 {int(btn.get('row', 0)) + 1} 行)")
    if note:
        lines = [note, "", *lines]
    rows = [[_btn("新增按钮", "admin:auto:buttons:add")]]
    for idx in range(len(buttons)):
        rows.append([_btn(f"删除 #{idx + 1}", f"admin:auto:buttons:del:{idx}")])
    rows.append([_btn("返回规则编辑", "admin:auto:edit:menu")])
    await _send_or_edit(update, context, "\n".join(lines), InlineKeyboardMarkup(rows))



async def show_ar_rule_menu(update, context, state: dict | None = None, idx: int | None = None, note: str = ""):
    state = dict(state or get_admin_state(update.effective_user.id) or {})
    group_id = _current_group(state)
    if not group_id:
        await show_group_select(update, context, state, note="请先选择群组")
        return
    rules = _normalize_reply_rules(get_group_auto_replies(group_id))
    rule = _normalize_auto_reply_rule((state.get("tmp") or {}).get("ar_rule") or {})
    tmp_idx = (state.get("tmp") or {}).get("ar_rule_idx")
    resolved_idx = idx if idx is not None else tmp_idx
    if resolved_idx is not None:
        try:
            resolved_idx = int(resolved_idx)
        except (TypeError, ValueError):
            resolved_idx = None
    if resolved_idx is not None and 0 <= resolved_idx < len(rules):
        rule = _normalize_auto_reply_rule(rules[resolved_idx])
        state.setdefault("tmp", {})["ar_rule"] = copy.deepcopy(rule)
        state.setdefault("tmp", {})["ar_rule_idx"] = resolved_idx
        state.setdefault("tmp", {})["ar_editing"] = True
        _save_state(update.effective_user.id, state)
    lines = [
        "自动回复规则编辑",
        f"状态: {'已启用' if rule.get('enabled', True) else '已停用'}",
        f"关键词: {html.escape(rule.get('keyword') or '-')}",
        f"匹配模式: {RULE_MODE_LABELS.get(rule.get('mode'), '包含')}",
        f"文本: {html.escape(_verify_preview_text(rule.get('reply_text', ''), limit=56))}",
        f"图片: {'已设置' if rule.get('photo_file_id') else '未设置'}",
        f"按钮数量: {len(rule.get('buttons') or [])}",
    ]
    if note:
        lines = [note, "", *lines]
    rows = []
    if resolved_idx is not None:
        rows.append([_btn(f"{'✅' if rule.get('enabled', True) else '❌'} 启用规则", f"admin:ar_toggle:{resolved_idx}")])
    rows.extend(
        [
            [_btn("设置关键词", "admin:auto:edit:keyword"), _btn("设置模式", "admin:auto:edit:mode")],
            [_btn("设置文本", "admin:auto:edit:text"), _btn("设置图片", "admin:auto:edit:photo")],
            [_btn("清除图片", "admin:auto:edit:photo:clear"), _btn("设置按钮", "admin:auto:edit:buttons")],
            [_btn("保存规则", "admin:auto:done"), _btn("取消", "admin:auto:cancel")],
        ]
    )
    await _send_or_edit(update, context, "\n".join(lines), InlineKeyboardMarkup(rows))



async def show_auto_delete_rules_menu(update, context, state: dict | None = None, note: str = ""):
    state = dict(state or get_admin_state(update.effective_user.id) or {})
    group_id = _current_group(state)
    if not group_id:
        await show_group_select(update, context, state, note="请先选择群组")
        return
    cfg = get_group_auto_delete(group_id)
    rules = list(cfg.get("custom_rules") or [])
    lines = ["自定义删除规则", f"当前规则数: {len(rules)}", ""]
    if not rules:
        lines.append("暂无规则")
    for idx, rule in enumerate(rules):
        lines.append(f"{idx + 1}. {html.escape(str(rule.get('keyword') or '-'))} ({RULE_MODE_LABELS.get(rule.get('mode'), '包含')})")
    if note:
        lines = [note, "", *lines]
    rows = [[_btn("添加规则", "admin:del:rule:add")]]
    for idx, rule in enumerate(rules):
        rows.append([_btn(f"删除 #{idx + 1}", f"admin:del:rule:del:{rule.get('id')}")])
    rows.append([_btn("返回自动删除", "admin:del:menu")])
    await _send_or_edit(update, context, "\n".join(lines), InlineKeyboardMarkup(rows))



def _auto_delete_toggle_text(cfg: dict, key: str, label: str) -> str:
    return f"{'✅' if cfg.get(key) else '❌'} {label}"



async def show_autodelete_menu(update, context, state: dict | None = None, note: str = ""):
    state = dict(state or get_admin_state(update.effective_user.id) or {})
    group_id = _current_group(state)
    if not group_id:
        await show_group_select(update, context, state, note="请先选择群组")
        return
    cfg = get_group_auto_delete(group_id)
    lines = [
        "自动删除消息",
        "✅ = 开启删除",
        "❌ = 关闭删除",
    ]
    if note:
        lines = [note, "", *lines]
    item_defs = [
        ("delete_system", "系统消息"),
        ("delete_channel_mask", "频道马甲"),
        ("delete_links", "链接消息"),
        ("delete_long", "超长消息"),
        ("delete_videos", "视频消息"),
        ("delete_stickers", "贴纸消息"),
        ("delete_forwarded", "禁止转发"),
        ("delete_ad_stickers", "删除广告贴纸"),
        ("delete_archives", "压缩包"),
        ("delete_executables", "可执行文件"),
        ("delete_notice_text", "提醒文字"),
        ("delete_documents", "文档"),
        ("delete_mentions", "删除@"),
        ("delete_other_commands", "其他命令"),
        ("delete_qr", "二维码"),
        ("delete_edited", "编辑消息"),
        ("delete_member_emoji", "会员表情"),
        ("delete_member_emoji_only", "仅表情"),
        ("delete_external_reply", "删除外部回复"),
        ("delete_shared_contact", "删除分享联系人"),
        ("exclude_admins", "排除管理员"),
    ]
    rows = _menu_two_cols([_btn(_auto_delete_toggle_text(cfg, key, label), f"admin:ad_toggle:{key}") for key, label in item_defs])
    rows.append([_btn("添加自定义规则", "admin:del:rule:add"), _btn("全部自定义规则", "admin:del:rule:list")])
    rows.append([_btn("返回主菜单", "admin:main")])
    await _send_or_edit(update, context, "\n".join(lines), InlineKeyboardMarkup(rows))



async def show_autoban_menu(update, context, state: dict | None = None, note: str = ""):
    state = dict(state or get_admin_state(update.effective_user.id) or {})
    group_id = _current_group(state)
    if not group_id:
        await show_group_select(update, context, state, note="请先选择群组")
        return
    cfg = get_group_auto_ban(group_id)
    rules = list(cfg.get("rules") or [])
    lines = [
        "自动封禁",
        f"当前状态: {'已开启' if cfg.get('enabled', True) else '已关闭'}",
        f"默认封禁时长: {int(cfg.get('default_duration_sec', 86400) or 86400)} 秒",
        f"规则数量: {len(rules)}",
    ]
    for idx, rule in enumerate(rules[:10]):
        lines.append(f"{idx + 1}. {html.escape(str(rule.get('keyword') or '-'))} ({RULE_MODE_LABELS.get(rule.get('mode'), '包含')})")
    if note:
        lines = [note, "", *lines]
    rows = [
        [_btn(f"{'✅' if cfg.get('enabled', True) else '❌'} 开启自动封禁", "admin:ab:toggle:enabled")],
        [_btn("设置默认时长", "admin:ab:duration")],
        [_btn("添加规则", "admin:ab:add")],
    ]
    for idx, rule in enumerate(rules):
        rows.append([_btn(f"删除 #{idx + 1}", f"admin:ab:del:{rule.get('id')}")])
    rows.append([_btn("返回主菜单", "admin:main")])
    await _send_or_edit(update, context, "\n".join(lines), InlineKeyboardMarkup(rows))



async def show_automute_menu(update, context, state: dict | None = None, note: str = ""):
    state = dict(state or get_admin_state(update.effective_user.id) or {})
    group_id = _current_group(state)
    if not group_id:
        await show_group_select(update, context, state, note="请先选择群组")
        return
    cfg = get_group_auto_mute(group_id)
    rules = list(cfg.get("rules") or [])
    lines = [
        "自动禁言",
        f"默认禁言时长: {int(cfg.get('default_duration_sec', 60) or 60)} 秒",
        f"规则数量: {len(rules)}",
    ]
    for idx, rule in enumerate(rules[:10]):
        lines.append(f"{idx + 1}. {html.escape(str(rule.get('keyword') or '-'))} ({RULE_MODE_LABELS.get(rule.get('mode'), '包含')})")
    if note:
        lines = [note, "", *lines]
    rows = [
        [_btn("设置默认时长", "admin:am:duration")],
        [_btn("添加规则", "admin:am:add")],
    ]
    for idx, rule in enumerate(rules):
        rows.append([_btn(f"删除 #{idx + 1}", f"admin:am:del:{rule.get('id')}")])
    rows.append([_btn("返回主菜单", "admin:main")])
    await _send_or_edit(update, context, "\n".join(lines), InlineKeyboardMarkup(rows))



async def show_autowarn_menu(update, context, state: dict | None = None, note: str = ""):
    state = dict(state or get_admin_state(update.effective_user.id) or {})
    group_id = _current_group(state)
    if not group_id:
        await show_group_select(update, context, state, note="请先选择群组")
        return
    cfg = get_group_auto_warn(group_id)
    rules = list(cfg.get("rules") or [])
    lines = [
        "自动警告",
        f"当前状态: {'已开启' if cfg.get('enabled', True) else '已关闭'}",
        f"警告上限: {int(cfg.get('warn_limit', 3) or 3)}",
        f"处罚时长: {int(cfg.get('mute_seconds', 86400) or 86400)} 秒",
        f"命令警告: {'已开启' if cfg.get('cmd_mute_enabled', False) else '已关闭'}",
        f"提示文案: {html.escape(_verify_preview_text(cfg.get('warn_text', ''), limit=56))}",
        f"规则数量: {len(rules)}",
    ]
    for idx, rule in enumerate(rules[:10]):
        lines.append(f"{idx + 1}. {html.escape(str(rule.get('keyword') or '-'))} ({RULE_MODE_LABELS.get(rule.get('mode'), '包含')})")
    if note:
        lines = [note, "", *lines]
    rows = [
        [_btn(f"{'✅' if cfg.get('enabled', True) else '❌'} 开启自动警告", "admin:aw:toggle:enabled")],
        [_btn(f"{'✅' if cfg.get('cmd_mute_enabled', False) else '❌'} 命令触发警告", "admin:aw_cmd_toggle")],
        [_btn("设置警告上限", "admin:aw:limit"), _btn("设置处罚时长", "admin:aw:mute")],
        [_btn("设置警告文案", "admin:aw:text"), _btn("添加规则", "admin:aw:add")],
    ]
    for idx, rule in enumerate(rules):
        rows.append([_btn(f"删除 #{idx + 1}", f"admin:aw:del:{rule.get('id')}")])
    rows.append([_btn("返回主菜单", "admin:main")])
    await _send_or_edit(update, context, "\n".join(lines), InlineKeyboardMarkup(rows))



async def show_antispam_menu(update, context, state: dict | None = None, note: str = ""):
    state = dict(state or get_admin_state(update.effective_user.id) or {})
    group_id = _current_group(state)
    if not group_id:
        await show_group_select(update, context, state, note="请先选择群组")
        return
    cfg = get_group_anti_spam(group_id)
    types = set(cfg.get("types") or [])
    type_names = "、".join(SPAM_TYPE_LABELS[key] for key in SPAM_TYPE_LABELS if key in types) or "无"
    lines = [
        "✋ 禁止刷屏",
        f"当前状态: {'已开启' if cfg.get('enabled', False) else '已关闭'}",
        f"触发条件: {int(cfg.get('window_sec', 10) or 10)} 秒内发送 {int(cfg.get('threshold', 3) or 3)} 次",
        f"处理方式: {'封禁' if str(cfg.get('action') or 'mute') == 'ban' else '禁言'} {int(cfg.get('mute_seconds', 300) or 300)} 秒",
        f"检测类型: {type_names}",
    ]
    if note:
        lines = [note, "", *lines]
    type_buttons = [_btn(f"{'✅' if key in types else '❌'} {label}", f"admin:spam_type:{key}") for key, label in SPAM_TYPE_LABELS.items()]
    rows = [
        [_btn(f"{'✅' if cfg.get('enabled', False) else '❌'} 开启刷屏检测", "admin:spam_toggle")],
        [_btn("在 10 秒" if False else f"在 {int(cfg.get('window_sec', 10) or 10)} 秒", "admin:spam:window"), _btn(f"发送 {int(cfg.get('threshold', 3) or 3)} 条触发", "admin:spam:threshold")],
        [_btn(f"禁言时间 {int(cfg.get('mute_seconds', 300) or 300)} 秒", "admin:spam:mute")],
        [_btn(f"{'✅' if str(cfg.get('action') or 'mute') == 'mute' else '▫️'} 禁言", "admin:spam:action:mute"), _btn(f"{'✅' if str(cfg.get('action') or 'mute') == 'ban' else '▫️'} 封禁", "admin:spam:action:ban")],
    ]
    rows.extend(_menu_two_cols(type_buttons))
    rows.append([_btn("返回主菜单", "admin:main")])
    await _send_or_edit(update, context, "\n".join(lines), InlineKeyboardMarkup(rows))



async def admin_start(update, context):
    if getattr(update.effective_chat, "type", "") != "private":
        return
    web_login_request_id = parse_web_login_start_arg((getattr(context, "args", None) or [None])[0])
    user_id = update.effective_user.id
    new_state = {"active_group_id": None, "state": STATE_NONE, "tmp": {}}
    _save_state(user_id, new_state)
    if web_login_request_id:
        await show_web_login_prompt(update, context, web_login_request_id)
        return
    await show_private_home(update, context, new_state)

async def _begin_input(update, context, user_id: int, state: dict, new_state: str, prompt: str, **tmp_updates):
    tmp = dict((state or {}).get("tmp") or {})
    tmp.update(tmp_updates)
    next_state = _state_with(state, state=new_state, tmp=tmp)
    _save_state(user_id, next_state)
    await _send_or_edit(update, context, prompt, _cancel_markup())



def _button_type_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[_btn("链接", "admin:btn_type:url"), _btn("回调", "admin:btn_type:callback")]])



def _rule_mode_markup(prefix: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [_btn("精确", f"{prefix}:exact"), _btn("包含", f"{prefix}:contains")],
            [_btn("正则", f"{prefix}:regex")],
        ]
    )



async def _resolve_pending_target(context, message) -> dict | None:
    forwarded_chat = getattr(message, "forward_from_chat", None) or getattr(message, "sender_chat", None)
    if forwarded_chat and getattr(forwarded_chat, "id", None):
        username = getattr(forwarded_chat, "username", None)
        return {
            "chat_id": int(forwarded_chat.id),
            "title": getattr(forwarded_chat, "title", None) or username or str(forwarded_chat.id),
            "username": username,
            "join_url": f"https://t.me/{str(username).lstrip('@')}" if username else "",
            "checkable": True,
        }

    raw_text = (getattr(message, "text", None) or getattr(message, "caption", None) or "").strip()
    parsed = parse_target_input(raw_text)
    if parsed.get("type") == "invalid":
        return None
    if parsed.get("type") == "username":
        username = str(parsed.get("value") or "").strip()
        username_clean = username.lstrip("@")
        try:
            chat = await context.bot.get_chat(username)
        except Exception:
            chat = None
        if chat and getattr(chat, "id", None):
            resolved_username = getattr(chat, "username", None) or username_clean
            return {
                "chat_id": int(chat.id),
                "title": getattr(chat, "title", None) or resolved_username or username_clean,
                "username": resolved_username,
                "join_url": f"https://t.me/{str(resolved_username).lstrip('@')}",
                "checkable": True,
            }
        return {
            "chat_id": None,
            "title": username_clean,
            "username": username_clean,
            "join_url": f"https://t.me/{username_clean}",
            "checkable": False,
        }
    if parsed.get("type") == "invite":
        return {
            "chat_id": None,
            "title": raw_text,
            "username": None,
            "join_url": normalize_url(raw_text),
            "checkable": False,
        }
    if parsed.get("type") == "chat_id":
        chat_id = int(parsed.get("value") or 0)
        try:
            chat = await context.bot.get_chat(chat_id)
        except Exception:
            chat = None
        if chat and getattr(chat, "id", None):
            username = getattr(chat, "username", None)
            return {
                "chat_id": int(chat.id),
                "title": getattr(chat, "title", None) or username or str(chat.id),
                "username": username,
                "join_url": f"https://t.me/{str(username).lstrip('@')}" if username else "",
                "checkable": True,
            }
        return {
            "chat_id": chat_id,
            "title": str(chat_id),
            "username": None,
            "join_url": "",
            "checkable": False,
        }
    return None



def _save_current_ar_rule(group_id: int, state: dict) -> tuple[bool, str]:
    tmp = dict((state or {}).get("tmp") or {})
    rule = _normalize_auto_reply_rule(tmp.get("ar_rule") or {})
    if not rule.get("keyword"):
        return False, "请先设置关键词"
    if not rule.get("reply_text") and not rule.get("photo_file_id") and not rule.get("buttons"):
        return False, "规则至少需要文本、图片或按钮"
    rules = _normalize_reply_rules(get_group_auto_replies(group_id))
    rule_idx = tmp.get("ar_rule_idx")
    if rule_idx is None or bool(tmp.get("ar_new")):
        rule["id"] = rule.get("id") or _rule_id()
        rules.append(rule)
    else:
        try:
            idx = int(rule_idx)
        except (TypeError, ValueError):
            idx = -1
        if 0 <= idx < len(rules):
            rule["id"] = rules[idx].get("id") or rule.get("id") or _rule_id()
            rules[idx] = rule
        else:
            rules.append(rule)
    save_group_auto_replies(group_id, rules)
    return True, "已保存自动回复规则"



async def admin_message(update, context):
    if getattr(update.effective_chat, "type", "") != "private":
        return
    user_id = update.effective_user.id
    state = get_admin_state(user_id)
    msg_text = (update.effective_message.text or update.effective_message.caption or "").strip()

    if await handle_private_home_message(update, context, state, msg_text):
        return

    if not await _ensure_active_group_access(update, context, state):
        return

    current_state = str(state.get("state") or "")
    if current_state.startswith("x:"):
        from . import admin_extra

        handled = await admin_extra.handle_admin_extra_message(update, context, state, msg_text)
        if handled:
            return

    group_id = _current_group(state)

    if current_state == str(STATE_TARGET_INPUT):
        pending = await _resolve_pending_target(context, update.effective_message)
        if not pending:
            await update.effective_message.reply_text("请输入 @username / t.me 链接 / chat_id，或直接转发目标群频道消息")
            return
        tmp = dict(state.get("tmp") or {})
        tmp["pending_target"] = pending
        next_state = _state_with(state, state=STATE_NONE, tmp=tmp)
        _save_state(user_id, next_state)
        lines = [
            "识别到验证目标：",
            f"名称: {pending.get('title') or '-'}",
            f"ID: {pending.get('chat_id') or '-'}",
            f"链接: {pending.get('join_url') or '-'}",
            "请确认是否添加。",
        ]
        markup = InlineKeyboardMarkup([[_btn("确认添加", "admin:targets:confirm"), _btn("取消", "admin:targets:cancel")]])
        await update.effective_message.reply_text("\n".join(lines), reply_markup=markup)
        return

    if not group_id and current_state not in ("", str(STATE_NONE)):
        await show_group_select(update, context, state, note="请先选择群组")
        return

    if current_state == str(STATE_WELCOME_TEXT):
        cfg = get_group_config(group_id)
        cfg["welcome_text"] = msg_text
        save_group_config(group_id, cfg)
        next_state = _state_with(state, state=STATE_NONE, tmp={})
        _save_state(user_id, next_state)
        await show_welcome_message_menu(update, context, next_state, note="欢迎文本已更新")
        return

    if current_state == str(STATE_WELCOME_TTL):
        try:
            ttl = int(msg_text)
        except ValueError:
            await update.effective_message.reply_text("请输入整数秒数")
            return
        cfg = get_group_config(group_id)
        cfg["welcome_ttl_sec"] = max(0, ttl)
        save_group_config(group_id, cfg)
        next_state = _state_with(state, state=STATE_NONE, tmp={})
        _save_state(user_id, next_state)
        await show_welcome_menu(update, context, next_state, note="欢迎倒计时已更新")
        return

    if current_state == str(STATE_VERIFY_TEXT):
        mode = str((state.get("tmp") or {}).get("verify_msg_type") or "join")
        cfg = get_group_config(group_id)
        set_verify_message(cfg, mode, text=msg_text)
        save_group_config(group_id, cfg)
        next_state = _state_with(state, state=STATE_NONE, tmp={})
        _save_state(user_id, next_state)
        await show_verify_message_menu(update, context, next_state, mode, note="验证文本已更新")
        return

    if current_state == str(STATE_VERIFY_FAIL_TEXT):
        cfg = get_group_config(group_id)
        cfg["verify_fail_text"] = msg_text
        save_group_config(group_id, cfg)
        next_state = _state_with(state, state=STATE_NONE, tmp={})
        _save_state(user_id, next_state)
        await show_verify_menu(update, context, next_state, note="验证失败文案已更新")
        return

    if current_state == str(STATE_VERIFY_TIMEOUT):
        try:
            timeout = int(msg_text)
        except ValueError:
            await update.effective_message.reply_text("请输入整数秒数")
            return
        cfg = get_group_config(group_id)
        cfg["verify_timeout_sec"] = max(1, timeout)
        save_group_config(group_id, cfg)
        next_state = _state_with(state, state=STATE_NONE, tmp={})
        _save_state(user_id, next_state)
        await show_verify_menu(update, context, next_state, note="验证时长已更新")
        return

    if current_state == str(STATE_BTN_TEXT):
        tmp = dict(state.get("tmp") or {})
        tmp["btn_text"] = msg_text or "按钮"
        next_state = _state_with(state, state=STATE_NONE, tmp=tmp)
        _save_state(user_id, next_state)
        await update.effective_message.reply_text("请选择按钮类型", reply_markup=_button_type_markup())
        return

    if current_state == str(STATE_BTN_VALUE):
        tmp = dict(state.get("tmp") or {})
        tmp["btn_value"] = msg_text
        next_state = _state_with(state, state=STATE_BTN_ROW, tmp=tmp)
        _save_state(user_id, next_state)
        await update.effective_message.reply_text("请输入按钮行号，从 1 开始")
        return
    if current_state == str(STATE_BTN_ROW):
        try:
            row = max(1, int(msg_text))
        except ValueError:
            await update.effective_message.reply_text("请输入数字行号")
            return
        tmp = dict(state.get("tmp") or {})
        btn = {
            "text": str(tmp.get("btn_text") or "按钮"),
            "type": str(tmp.get("btn_type") or "url"),
            "value": str(tmp.get("btn_value") or ""),
            "row": row - 1,
        }
        target = str(tmp.get("btn_target") or "")
        if target == "welcome":
            cfg = get_group_config(group_id)
            buttons = _normalize_buttons(cfg.get("welcome_buttons") or [])
            buttons.append(btn)
            cfg["welcome_buttons"] = buttons
            save_group_config(group_id, cfg)
            next_state = _state_with(state, state=STATE_NONE, tmp={})
            _save_state(user_id, next_state)
            await show_welcome_buttons_menu(update, context, next_state, note="欢迎按钮已添加")
            return
        if target.startswith("verify:"):
            mode = target.split(":", 1)[1] or "join"
            cfg = get_group_config(group_id)
            current = get_verify_message(cfg, mode)
            buttons = _normalize_buttons(current.get("buttons") or [])
            buttons.append(btn)
            set_verify_message(cfg, mode, buttons=buttons)
            save_group_config(group_id, cfg)
            next_state = _state_with(state, state=STATE_NONE, tmp={})
            _save_state(user_id, next_state)
            await show_verify_buttons_menu(update, context, next_state, mode, note="验证按钮已添加")
            return
        if target == "auto_reply":
            rule = _normalize_auto_reply_rule(tmp.get("ar_rule") or {})
            buttons = _normalize_buttons(rule.get("buttons") or [])
            buttons.append(btn)
            rule["buttons"] = buttons
            tmp["ar_rule"] = rule
            next_state = _state_with(state, state=STATE_NONE, tmp=tmp)
            _save_state(user_id, next_state)
            await show_auto_reply_buttons_menu(update, context, next_state, note="自动回复按钮已添加")
            return
        if target.startswith("rich:"):
            from . import admin_extra

            rich_target = target.split(":", 1)[1]
            payload = admin_extra.get_rich_message_target(group_id, rich_target)
            if payload is None:
                await update.effective_message.reply_text("消息配置不存在或已被删除。")
                next_state = _state_with(state, state=STATE_NONE, tmp={})
                _save_state(user_id, next_state)
                return
            buttons = _normalize_buttons(payload.get("buttons") or [])
            btn["row"] = row - 1
            buttons.append(btn)
            payload["buttons"] = buttons
            if not admin_extra.save_rich_message_target(group_id, rich_target, payload):
                await update.effective_message.reply_text("保存失败，请稍后再试。")
                return
            next_state = _state_with(state, state=STATE_NONE, tmp={})
            _save_state(user_id, next_state)
            await admin_extra.show_rich_message_editor(update, context, next_state, rich_target)
            return
        await update.effective_message.reply_text("未知按钮目标，已取消")
        next_state = _state_with(state, state=STATE_NONE, tmp={})
        _save_state(user_id, next_state)
        return

    if current_state == str(STATE_AR_KEYWORD):
        tmp = dict(state.get("tmp") or {})
        rule = _normalize_auto_reply_rule(tmp.get("ar_rule") or {})
        rule["keyword"] = msg_text
        tmp["ar_rule"] = rule
        next_state = _state_with(state, state=STATE_NONE, tmp=tmp)
        _save_state(user_id, next_state)
        await update.effective_message.reply_text("请选择匹配模式", reply_markup=_rule_mode_markup("admin:auto:mode"))
        return

    if current_state == str(STATE_AR_TEXT):
        tmp = dict(state.get("tmp") or {})
        rule = _normalize_auto_reply_rule(tmp.get("ar_rule") or {})
        rule["reply_text"] = msg_text
        tmp["ar_rule"] = rule
        next_state = _state_with(state, state=STATE_NONE, tmp=tmp)
        _save_state(user_id, next_state)
        await show_ar_rule_menu(update, context, next_state, note="自动回复文本已更新")
        return

    if current_state == str(STATE_AR_PHOTO):
        await update.effective_message.reply_text("请发送图片")
        return

    if current_state == str(STATE_AD_RULE_KEYWORD):
        tmp = dict(state.get("tmp") or {})
        tmp["ad_rule"] = {"keyword": msg_text}
        next_state = _state_with(state, state=STATE_NONE, tmp=tmp)
        _save_state(user_id, next_state)
        await update.effective_message.reply_text("请选择匹配模式", reply_markup=_rule_mode_markup("admin:del:rule:mode"))
        return

    if current_state == str(STATE_AB_KEYWORD):
        tmp = dict(state.get("tmp") or {})
        tmp["ab_rule"] = {"keyword": msg_text}
        next_state = _state_with(state, state=STATE_NONE, tmp=tmp)
        _save_state(user_id, next_state)
        await update.effective_message.reply_text("请选择匹配模式", reply_markup=_rule_mode_markup("admin:ab:mode"))
        return

    if current_state == str(STATE_AB_DURATION):
        try:
            duration = int(msg_text)
        except ValueError:
            await update.effective_message.reply_text("请输入整数秒数")
            return
        tmp = dict(state.get("tmp") or {})
        if str(tmp.get("ab_kind") or "") == "default":
            cfg = get_group_auto_ban(group_id)
            cfg["default_duration_sec"] = duration
            save_group_auto_ban(group_id, cfg)
            next_state = _state_with(state, state=STATE_NONE, tmp={})
            _save_state(user_id, next_state)
            await show_autoban_menu(update, context, next_state, note="默认封禁时长已更新")
            return
        rule = dict(tmp.get("ab_rule") or {})
        rule["id"] = rule.get("id") or _rule_id()
        rule["duration_sec"] = duration
        cfg = get_group_auto_ban(group_id)
        rules = list(cfg.get("rules") or [])
        rules.append(rule)
        cfg["rules"] = rules
        save_group_auto_ban(group_id, cfg)
        next_state = _state_with(state, state=STATE_NONE, tmp={})
        _save_state(user_id, next_state)
        await show_autoban_menu(update, context, next_state, note="自动封禁规则已添加")
        return

    if current_state == str(STATE_AM_KEYWORD):
        tmp = dict(state.get("tmp") or {})
        tmp["am_rule"] = {"keyword": msg_text}
        next_state = _state_with(state, state=STATE_NONE, tmp=tmp)
        _save_state(user_id, next_state)
        await update.effective_message.reply_text("请选择匹配模式", reply_markup=_rule_mode_markup("admin:am:mode"))
        return

    if current_state == str(STATE_AM_DURATION):
        try:
            duration = int(msg_text)
        except ValueError:
            await update.effective_message.reply_text("请输入整数秒数")
            return
        tmp = dict(state.get("tmp") or {})
        if str(tmp.get("am_kind") or "") == "default":
            cfg = get_group_auto_mute(group_id)
            cfg["default_duration_sec"] = duration
            save_group_auto_mute(group_id, cfg)
            next_state = _state_with(state, state=STATE_NONE, tmp={})
            _save_state(user_id, next_state)
            await show_automute_menu(update, context, next_state, note="默认禁言时长已更新")
            return
        rule = dict(tmp.get("am_rule") or {})
        rule["id"] = rule.get("id") or _rule_id()
        rule["duration_sec"] = duration
        cfg = get_group_auto_mute(group_id)
        rules = list(cfg.get("rules") or [])
        rules.append(rule)
        cfg["rules"] = rules
        save_group_auto_mute(group_id, cfg)
        next_state = _state_with(state, state=STATE_NONE, tmp={})
        _save_state(user_id, next_state)
        await show_automute_menu(update, context, next_state, note="自动禁言规则已添加")
        return

    if current_state == str(STATE_AW_RULE_KEYWORD):
        tmp = dict(state.get("tmp") or {})
        tmp["aw_rule"] = {"keyword": msg_text}
        next_state = _state_with(state, state=STATE_NONE, tmp=tmp)
        _save_state(user_id, next_state)
        await update.effective_message.reply_text("请选择匹配模式", reply_markup=_rule_mode_markup("admin:aw:rule:mode"))
        return

    if current_state == str(STATE_AW_LIMIT):
        try:
            limit = int(msg_text)
        except ValueError:
            await update.effective_message.reply_text("请输入整数")
            return
        cfg = get_group_auto_warn(group_id)
        cfg["warn_limit"] = max(1, limit)
        save_group_auto_warn(group_id, cfg)
        next_state = _state_with(state, state=STATE_NONE, tmp={})
        _save_state(user_id, next_state)
        await show_autowarn_menu(update, context, next_state, note="警告上限已更新")
        return

    if current_state == str(STATE_AW_MUTE):
        try:
            mute_seconds = int(msg_text)
        except ValueError:
            await update.effective_message.reply_text("请输入整数秒数")
            return
        cfg = get_group_auto_warn(group_id)
        cfg["mute_seconds"] = max(1, mute_seconds)
        save_group_auto_warn(group_id, cfg)
        next_state = _state_with(state, state=STATE_NONE, tmp={})
        _save_state(user_id, next_state)
        await show_autowarn_menu(update, context, next_state, note="处罚时长已更新")
        return

    if current_state == str(STATE_AW_TEXT):
        cfg = get_group_auto_warn(group_id)
        cfg["warn_text"] = msg_text
        save_group_auto_warn(group_id, cfg)
        next_state = _state_with(state, state=STATE_NONE, tmp={})
        _save_state(user_id, next_state)
        await show_autowarn_menu(update, context, next_state, note="警告文案已更新")
        return

    if current_state == str(STATE_SPAM_WINDOW):
        try:
            seconds = int(msg_text)
        except ValueError:
            await update.effective_message.reply_text("请输入整数秒数")
            return
        cfg = get_group_anti_spam(group_id)
        cfg["window_sec"] = max(1, seconds)
        save_group_anti_spam(group_id, cfg)
        next_state = _state_with(state, state=STATE_NONE, tmp={})
        _save_state(user_id, next_state)
        await show_antispam_menu(update, context, next_state, note="刷屏检测时间窗口已更新")
        return

    if current_state == str(STATE_SPAM_THRESHOLD):
        try:
            threshold = int(msg_text)
        except ValueError:
            await update.effective_message.reply_text("请输入整数")
            return
        cfg = get_group_anti_spam(group_id)
        cfg["threshold"] = max(1, threshold)
        save_group_anti_spam(group_id, cfg)
        next_state = _state_with(state, state=STATE_NONE, tmp={})
        _save_state(user_id, next_state)
        await show_antispam_menu(update, context, next_state, note="刷屏触发次数已更新")
        return

    if current_state == str(STATE_SPAM_MUTE):
        try:
            mute_seconds = int(msg_text)
        except ValueError:
            await update.effective_message.reply_text("请输入整数秒数")
            return
        cfg = get_group_anti_spam(group_id)
        cfg["mute_seconds"] = max(1, mute_seconds)
        save_group_anti_spam(group_id, cfg)
        next_state = _state_with(state, state=STATE_NONE, tmp={})
        _save_state(user_id, next_state)
        await show_antispam_menu(update, context, next_state, note="刷屏处罚时长已更新")
        return



async def admin_photo(update, context):
    if getattr(update.effective_chat, "type", "") != "private":
        return
    photos = getattr(update.effective_message, "photo", None) or []
    if not photos:
        return
    user_id = update.effective_user.id
    state = get_admin_state(user_id)
    group_id = _current_group(state)
    if not group_id:
        await show_group_select(update, context, state, note="请先选择群组")
        return
    photo_file_id = photos[-1].file_id
    current_state = str(state.get("state") or "")

    if current_state == str(STATE_WELCOME_PHOTO):
        cfg = get_group_config(group_id)
        cfg["welcome_photo_file_id"] = photo_file_id
        save_group_config(group_id, cfg)
        next_state = _state_with(state, state=STATE_NONE, tmp={})
        _save_state(user_id, next_state)
        await show_welcome_message_menu(update, context, next_state, note="欢迎图片已更新")
        return

    if current_state == str(STATE_VERIFY_PHOTO):
        mode = str((state.get("tmp") or {}).get("verify_msg_type") or "join")
        cfg = get_group_config(group_id)
        set_verify_message(cfg, mode, photo_file_id=photo_file_id)
        save_group_config(group_id, cfg)
        next_state = _state_with(state, state=STATE_NONE, tmp={})
        _save_state(user_id, next_state)
        await show_verify_message_menu(update, context, next_state, mode, note="验证图片已更新")
        return

    if current_state == str(STATE_AR_PHOTO):
        tmp = dict(state.get("tmp") or {})
        rule = _normalize_auto_reply_rule(tmp.get("ar_rule") or {})
        rule["photo_file_id"] = photo_file_id
        tmp["ar_rule"] = rule
        next_state = _state_with(state, state=STATE_NONE, tmp=tmp)
        _save_state(user_id, next_state)
        await show_ar_rule_menu(update, context, next_state, note="自动回复图片已更新")
        return

async def admin_callback(update, context):
    query = update.callback_query
    data = getattr(query, "data", "") or ""
    user_id = query.from_user.id
    state = get_admin_state(user_id)

    if await handle_private_home_callback(update, context, state):
        return

    if data not in {"admin:none", "admin:home", "admin:start", "admin:groups"} and not data.startswith("admin:select_group:"):
        if not await _ensure_active_group_access(update, context, state):
            return

    if data.startswith("adminx:"):
        from . import admin_extra

        if await admin_extra.handle_admin_extra_callback(update, context, state):
            return

    if data == "admin:none":
        await safe_answer(query)
        return

    if data in {"admin:home", "admin:start"}:
        await show_private_home(update, context, state)
        return

    if data == "admin:groups":
        await show_group_select(update, context, state)
        return

    if data == "admin:cancel_input":
        next_state = _state_with(state, state=STATE_NONE, tmp={})
        _save_state(user_id, next_state)
        if _current_group(next_state):
            await show_main_menu(update, context, next_state, note="已取消输入")
        else:
            await show_group_select(update, context, next_state, note="已取消输入")
        return

    if data.startswith("admin:select_group:"):
        try:
            group_id = int(data.split(":", 2)[2])
        except (IndexError, ValueError):
            await safe_answer(query, "群组参数错误", show_alert=True)
            return
        if not await _can_manage_group(context, user_id, group_id):
            await safe_answer(query, "当前群组无权限", show_alert=True)
            return
        next_state = {"active_group_id": group_id, "state": STATE_NONE, "tmp": {}}
        _save_state(user_id, next_state)
        await show_main_menu(update, context, next_state, note="已切换群组")
        return

    group_id = _current_group(state)
    if data.startswith("admin:") and data not in {"admin:home", "admin:groups"} and not group_id:
        await show_group_select(update, context, state, note="请先选择群组")
        return

    if data == "admin:main":
        await show_main_menu(update, context, state)
        return

    if data in {"admin:verify", "admin:verify:menu"}:
        await show_verify_menu(update, context, state)
        return
    if data == "admin:verify_toggle":
        cfg = get_group_config(group_id)
        cfg["verify_enabled"] = not bool(cfg.get("verify_enabled", True))
        save_group_config(group_id, cfg)
        await safe_answer(query, "已打开" if cfg["verify_enabled"] else "已关闭", show_alert=True)
        await show_verify_menu(update, context, state)
        return
    if data == "admin:verify_private":
        cfg = get_group_config(group_id)
        cfg["verify_private"] = not bool(cfg.get("verify_private", False))
        save_group_config(group_id, cfg)
        await safe_answer(query, "已打开" if cfg["verify_private"] else "已关闭", show_alert=True)
        await show_verify_menu(update, context, state)
        return
    if data.startswith("admin:verify:mode:set:"):
        mode = data.rsplit(":", 1)[-1]
        cfg = get_group_config(group_id)
        cfg["verify_mode"] = mode
        save_group_config(group_id, cfg)
        await show_verify_menu(update, context, state, note="验证模式已更新")
        return
    if data.startswith("admin:verify:fail:action:"):
        action = data.rsplit(":", 1)[-1]
        cfg = get_group_config(group_id)
        cfg["verify_fail_action"] = action
        save_group_config(group_id, cfg)
        await show_verify_menu(update, context, state, note="失败处理方式已更新")
        return
    if data == "admin:verify:best":
        cfg = get_group_config(group_id)
        cfg["verify_enabled"] = True
        cfg["verify_mode"] = "join"
        cfg["verify_private"] = False
        cfg["verify_timeout_sec"] = 120
        cfg["verify_fail_action"] = "mute"
        save_group_config(group_id, cfg)
        await show_verify_menu(update, context, state, note="已应用推荐配置")
        return
    if data == "admin:verify:fail:text":
        await _begin_input(update, context, user_id, state, STATE_VERIFY_FAIL_TEXT, "请输入验证失败提示文案")
        return
    if data == "admin:verify:timeout":
        await _begin_input(update, context, user_id, state, STATE_VERIFY_TIMEOUT, "请输入验证时长，单位秒")
        return
    if data.startswith("admin:verify:text:"):
        mode = data.rsplit(":", 1)[-1]
        await _begin_input(update, context, user_id, state, STATE_VERIFY_TEXT, f"请输入 {verify_mode_label(mode)} 文本", verify_msg_type=mode)
        return
    if data.startswith("admin:verify:photo:") and ":clear:" not in data:
        mode = data.rsplit(":", 1)[-1]
        await _begin_input(update, context, user_id, state, STATE_VERIFY_PHOTO, f"请发送 {verify_mode_label(mode)} 图片", verify_msg_type=mode)
        return
    if data.startswith("admin:verify:photo:clear:"):
        mode = data.rsplit(":", 1)[-1]
        cfg = get_group_config(group_id)
        set_verify_message(cfg, mode, photo_file_id="")
        save_group_config(group_id, cfg)
        await show_verify_message_menu(update, context, state, mode, note="验证图片已清除")
        return
    if data.startswith("admin:verify:msg:"):
        mode = data.rsplit(":", 1)[-1]
        await show_verify_message_menu(update, context, state, mode)
        return
    if data == "admin:targets":
        await show_targets_menu(update, context, state)
        return
    if data == "admin:targets:add":
        await _begin_input(update, context, user_id, state, STATE_TARGET_INPUT, "请发送 @username / t.me 链接 / chat_id，或直接转发目标群频道消息")
        return
    if data == "admin:targets:confirm":
        pending = (state.get("tmp") or {}).get("pending_target")
        if not pending:
            await safe_answer(query, "没有待确认的目标", show_alert=True)
            return
        targets = list(get_group_targets(group_id) or [])
        targets.append(dict(pending))
        save_group_targets(group_id, targets)
        next_state = _state_with(state, state=STATE_NONE, tmp={})
        _save_state(user_id, next_state)
        await show_targets_menu(update, context, next_state, note="验证目标已添加")
        return
    if data == "admin:targets:cancel":
        next_state = _state_with(state, state=STATE_NONE, tmp={})
        _save_state(user_id, next_state)
        await show_targets_menu(update, context, next_state, note="已取消添加目标")
        return
    if data.startswith("admin:targets:del:"):
        try:
            idx = int(data.rsplit(":", 1)[-1])
        except ValueError:
            idx = -1
        targets = list(get_group_targets(group_id) or [])
        if 0 <= idx < len(targets):
            targets.pop(idx)
            save_group_targets(group_id, targets)
        await show_targets_menu(update, context, state, note="验证目标已删除")
        return
    if data.startswith("admin:verify:buttons:"):
        parts = data.split(":")
        if len(parts) == 4:
            _, _, _, mode = parts
            await show_verify_buttons_menu(update, context, state, mode)
            return
        if len(parts) == 5 and parts[3] == "add":
            mode = parts[4]
            await _begin_input(update, context, user_id, state, STATE_BTN_TEXT, "请输入按钮文字", btn_target=f"verify:{mode}")
            return
        if len(parts) == 5 and parts[3] == "clear":
            mode = parts[4]
            cfg = get_group_config(group_id)
            set_verify_message(cfg, mode, buttons=[])
            save_group_config(group_id, cfg)
            await show_verify_buttons_menu(update, context, state, mode, note="验证按钮已清空")
            return
        if len(parts) == 6 and parts[3] == "del":
            mode = parts[4]
            try:
                idx = int(parts[5])
            except ValueError:
                idx = -1
            cfg = get_group_config(group_id)
            message_cfg = get_verify_message(cfg, mode)
            buttons = _normalize_buttons(message_cfg.get("buttons") or [])
            if 0 <= idx < len(buttons):
                buttons.pop(idx)
                set_verify_message(cfg, mode, buttons=buttons)
                save_group_config(group_id, cfg)
            await show_verify_buttons_menu(update, context, state, mode, note="验证按钮已删除")
            return

    if data in {"admin:welcome", "admin:welcome:menu"}:
        await show_welcome_menu(update, context, state)
        return
    if data == "admin:welcome_toggle":
        cfg = get_group_config(group_id)
        cfg["welcome_enabled"] = not bool(cfg.get("welcome_enabled", True))
        save_group_config(group_id, cfg)
        await safe_answer(query, "已打开" if cfg["welcome_enabled"] else "已关闭", show_alert=True)
        await show_welcome_menu(update, context, state)
        return
    if data == "admin:welcome_delete_prev":
        cfg = get_group_config(group_id)
        cfg["welcome_delete_prev"] = not bool(cfg.get("welcome_delete_prev", False))
        save_group_config(group_id, cfg)
        await safe_answer(query, "已打开" if cfg["welcome_delete_prev"] else "已关闭", show_alert=True)
        await show_welcome_menu(update, context, state)
        return
    if data == "admin:welcome:edit":
        await show_welcome_message_menu(update, context, state)
        return
    if data == "admin:welcome:text":
        await _begin_input(update, context, user_id, state, STATE_WELCOME_TEXT, "请输入欢迎消息文本")
        return
    if data == "admin:welcome:photo":
        await _begin_input(update, context, user_id, state, STATE_WELCOME_PHOTO, "请发送欢迎消息图片")
        return
    if data == "admin:welcome:photo:clear":
        cfg = get_group_config(group_id)
        cfg["welcome_photo_file_id"] = ""
        save_group_config(group_id, cfg)
        await show_welcome_message_menu(update, context, state, note="欢迎图片已清除")
        return
    if data == "admin:welcome:buttons":
        await show_welcome_buttons_menu(update, context, state)
        return
    if data == "admin:welcome:buttons:add":
        await _begin_input(update, context, user_id, state, STATE_BTN_TEXT, "请输入按钮文字", btn_target="welcome")
        return
    if data == "admin:welcome:buttons:clear":
        cfg = get_group_config(group_id)
        cfg["welcome_buttons"] = []
        save_group_config(group_id, cfg)
        await show_welcome_buttons_menu(update, context, state, note="欢迎按钮已清空")
        return
    if data.startswith("admin:welcome:buttons:del:"):
        try:
            idx = int(data.rsplit(":", 1)[-1])
        except ValueError:
            idx = -1
        cfg = get_group_config(group_id)
        buttons = _normalize_buttons(cfg.get("welcome_buttons") or [])
        if 0 <= idx < len(buttons):
            buttons.pop(idx)
            cfg["welcome_buttons"] = buttons
            save_group_config(group_id, cfg)
        await show_welcome_buttons_menu(update, context, state, note="欢迎按钮已删除")
        return
    if data == "admin:welcome:ttl":
        await _begin_input(update, context, user_id, state, STATE_WELCOME_TTL, "请输入欢迎消息删除倒计时，单位秒")
        return
    if data.startswith("admin:welcome:delete_mode:"):
        mode = data.rsplit(":", 1)[-1]
        cfg = get_group_config(group_id)
        if mode == "previous":
            cfg["welcome_delete_prev"] = True
            cfg["welcome_ttl_sec"] = 0
        elif mode == "ttl":
            cfg["welcome_delete_prev"] = False
            cfg["welcome_ttl_sec"] = max(30, int(cfg.get("welcome_ttl_sec", 0) or 0))
        else:
            cfg["welcome_delete_prev"] = False
            cfg["welcome_ttl_sec"] = 0
        save_group_config(group_id, cfg)
        await show_welcome_menu(update, context, state, note="欢迎删除模式已更新")
        return

    if data.startswith("admin:btn_type:"):
        btn_type = data.rsplit(":", 1)[-1]
        tmp = dict(state.get("tmp") or {})
        tmp["btn_type"] = btn_type
        next_state = _state_with(state, state=STATE_BTN_VALUE, tmp=tmp)
        _save_state(user_id, next_state)
        prompt = "请输入按钮链接" if btn_type == "url" else "请输入点击按钮后的弹窗文本"
        await _send_or_edit(update, context, prompt, _cancel_markup())
        return

    if data in {"admin:auto", "admin:auto:menu"}:
        await show_auto_reply_menu(update, context, state)
        return
    if data == "admin:auto:add":
        rules = _normalize_reply_rules(get_group_auto_replies(group_id))
        limit = auto_reply_limit_for_group(group_id)
        if len(rules) >= limit:
            await safe_answer(query, f"当前套餐最多支持 {limit} 条自动回复", show_alert=True)
            return
        tmp = {"ar_rule": _normalize_auto_reply_rule({"id": _rule_id()}), "ar_editing": True, "ar_new": True}
        next_state = _state_with(state, state=STATE_AR_KEYWORD, tmp=tmp)
        _save_state(user_id, next_state)
        await _send_or_edit(update, context, "请输入自动回复关键词", _cancel_markup())
        return
    if data.startswith("admin:auto:edit:"):
        suffix = data.split(":", 3)[3]
        if suffix == "menu":
            await show_ar_rule_menu(update, context, state)
            return
        if suffix == "keyword":
            await _begin_input(update, context, user_id, state, STATE_AR_KEYWORD, "请输入关键词")
            return
        if suffix == "mode":
            await _send_or_edit(update, context, "请选择匹配模式", _rule_mode_markup("admin:auto:mode"))
            return
        if suffix == "text":
            await _begin_input(update, context, user_id, state, STATE_AR_TEXT, "请输入自动回复文本")
            return
        if suffix == "photo":
            await _begin_input(update, context, user_id, state, STATE_AR_PHOTO, "请发送自动回复图片")
            return
        if suffix == "photo:clear":
            tmp = dict(state.get("tmp") or {})
            rule = _normalize_auto_reply_rule(tmp.get("ar_rule") or {})
            rule["photo_file_id"] = ""
            tmp["ar_rule"] = rule
            next_state = _state_with(state, state=STATE_NONE, tmp=tmp)
            _save_state(user_id, next_state)
            await show_ar_rule_menu(update, context, next_state, note="自动回复图片已清除")
            return
        if suffix == "buttons":
            await show_auto_reply_buttons_menu(update, context, state)
            return
        rules = _normalize_reply_rules(get_group_auto_replies(group_id))
        idx = _find_rule_index(rules, suffix)
        if idx < 0:
            await safe_answer(query, "规则不存在", show_alert=True)
            return
        tmp = {"ar_rule": copy.deepcopy(rules[idx]), "ar_rule_idx": idx, "ar_editing": True, "ar_new": False}
        next_state = _state_with(state, state=STATE_NONE, tmp=tmp)
        _save_state(user_id, next_state)
        await show_ar_rule_menu(update, context, next_state)
        return
    if data.startswith("admin:auto:del:"):
        rule_id = data.rsplit(":", 1)[-1]
        rules = _normalize_reply_rules(get_group_auto_replies(group_id))
        idx = _find_rule_index(rules, rule_id)
        if idx >= 0:
            rules.pop(idx)
            save_group_auto_replies(group_id, rules)
        await show_auto_reply_menu(update, context, state, note="自动回复规则已删除")
        return
    if data.startswith("admin:auto:mode:"):
        mode = data.rsplit(":", 1)[-1]
        tmp = dict(state.get("tmp") or {})
        rule = _normalize_auto_reply_rule(tmp.get("ar_rule") or {})
        rule["mode"] = mode
        tmp["ar_rule"] = rule
        next_state = _state_with(state, state=STATE_NONE, tmp=tmp)
        _save_state(user_id, next_state)
        await show_ar_rule_menu(update, context, next_state, note="匹配模式已更新")
        return
    if data == "admin:auto:buttons:add":
        await _begin_input(update, context, user_id, state, STATE_BTN_TEXT, "请输入按钮文字", btn_target="auto_reply")
        return
    if data.startswith("admin:auto:buttons:del:"):
        try:
            idx = int(data.rsplit(":", 1)[-1])
        except ValueError:
            idx = -1
        tmp = dict(state.get("tmp") or {})
        rule = _normalize_auto_reply_rule(tmp.get("ar_rule") or {})
        buttons = _normalize_buttons(rule.get("buttons") or [])
        if 0 <= idx < len(buttons):
            buttons.pop(idx)
        rule["buttons"] = buttons
        tmp["ar_rule"] = rule
        next_state = _state_with(state, state=STATE_NONE, tmp=tmp)
        _save_state(user_id, next_state)
        await show_auto_reply_buttons_menu(update, context, next_state, note="自动回复按钮已删除")
        return
    if data == "admin:auto:done":
        ok, note = _save_current_ar_rule(group_id, state)
        if not ok:
            await safe_answer(query, note, show_alert=True)
            return
        next_state = _state_with(state, state=STATE_NONE, tmp={})
        _save_state(user_id, next_state)
        await show_auto_reply_menu(update, context, next_state, note=note)
        return
    if data == "admin:auto:cancel":
        next_state = _state_with(state, state=STATE_NONE, tmp={})
        _save_state(user_id, next_state)
        await show_auto_reply_menu(update, context, next_state, note="已取消自动回复编辑")
        return

    if data in {"admin:del", "admin:del:menu"}:
        await show_autodelete_menu(update, context, state)
        return
    if data.startswith("admin:ad_toggle:"):
        key = data.rsplit(":", 1)[-1]
        cfg = get_group_auto_delete(group_id)
        cfg[key] = not bool(cfg.get(key, False))
        save_group_auto_delete(group_id, cfg)
        await safe_answer(query, "已打开" if cfg[key] else "已关闭", show_alert=True)
        await show_autodelete_menu(update, context, state)
        return
    if data == "admin:del:rule:list":
        await show_auto_delete_rules_menu(update, context, state)
        return
    if data == "admin:del:rule:add":
        await _begin_input(update, context, user_id, state, STATE_AD_RULE_KEYWORD, "请输入需要删除的关键词")
        return
    if data.startswith("admin:del:rule:mode:"):
        mode = data.rsplit(":", 1)[-1]
        tmp = dict(state.get("tmp") or {})
        rule = dict(tmp.get("ad_rule") or {})
        if not rule.get("keyword"):
            await safe_answer(query, "请先输入关键词", show_alert=True)
            return
        rule["id"] = rule.get("id") or _rule_id()
        rule["mode"] = mode
        cfg = get_group_auto_delete(group_id)
        rules = list(cfg.get("custom_rules") or [])
        rules.append(rule)
        cfg["custom_rules"] = rules
        save_group_auto_delete(group_id, cfg)
        next_state = _state_with(state, state=STATE_NONE, tmp={})
        _save_state(user_id, next_state)
        await show_auto_delete_rules_menu(update, context, next_state, note="自定义删除规则已添加")
        return
    if data.startswith("admin:del:rule:del:"):
        rule_id = data.rsplit(":", 1)[-1]
        cfg = get_group_auto_delete(group_id)
        rules = [rule for rule in list(cfg.get("custom_rules") or []) if str(rule.get("id") or "") != rule_id]
        cfg["custom_rules"] = rules
        save_group_auto_delete(group_id, cfg)
        await show_auto_delete_rules_menu(update, context, state, note="自定义删除规则已删除")
        return

    if data in {"admin:ab", "admin:ab:menu"}:
        await show_autoban_menu(update, context, state)
        return
    if data == "admin:ab:toggle:enabled":
        cfg = get_group_auto_ban(group_id)
        cfg["enabled"] = not bool(cfg.get("enabled", True))
        save_group_auto_ban(group_id, cfg)
        await safe_answer(query, "已打开" if cfg["enabled"] else "已关闭", show_alert=True)
        await show_autoban_menu(update, context, state)
        return
    if data == "admin:ab:duration":
        await _begin_input(update, context, user_id, state, STATE_AB_DURATION, "请输入默认封禁时长，单位秒", ab_kind="default")
        return
    if data == "admin:ab:add":
        await _begin_input(update, context, user_id, state, STATE_AB_KEYWORD, "请输入封禁关键词")
        return
    if data.startswith("admin:ab:mode:"):
        mode = data.rsplit(":", 1)[-1]
        tmp = dict(state.get("tmp") or {})
        rule = dict(tmp.get("ab_rule") or {})
        rule["mode"] = mode
        tmp["ab_rule"] = rule
        next_state = _state_with(state, state=STATE_AB_DURATION, tmp=tmp)
        _save_state(user_id, next_state)
        await _send_or_edit(update, context, "请输入该规则的封禁时长，单位秒", _cancel_markup())
        return
    if data.startswith("admin:ab:del:"):
        rule_id = data.rsplit(":", 1)[-1]
        cfg = get_group_auto_ban(group_id)
        cfg["rules"] = [rule for rule in list(cfg.get("rules") or []) if str(rule.get("id") or "") != rule_id]
        save_group_auto_ban(group_id, cfg)
        await show_autoban_menu(update, context, state, note="自动封禁规则已删除")
        return

    if data in {"admin:am", "admin:am:menu"}:
        await show_automute_menu(update, context, state)
        return
    if data == "admin:am:duration":
        await _begin_input(update, context, user_id, state, STATE_AM_DURATION, "请输入默认禁言时长，单位秒", am_kind="default")
        return
    if data == "admin:am:add":
        await _begin_input(update, context, user_id, state, STATE_AM_KEYWORD, "请输入禁言关键词")
        return
    if data.startswith("admin:am:mode:"):
        mode = data.rsplit(":", 1)[-1]
        tmp = dict(state.get("tmp") or {})
        rule = dict(tmp.get("am_rule") or {})
        rule["mode"] = mode
        tmp["am_rule"] = rule
        next_state = _state_with(state, state=STATE_AM_DURATION, tmp=tmp)
        _save_state(user_id, next_state)
        await _send_or_edit(update, context, "请输入该规则的禁言时长，单位秒", _cancel_markup())
        return
    if data.startswith("admin:am:del:"):
        rule_id = data.rsplit(":", 1)[-1]
        cfg = get_group_auto_mute(group_id)
        cfg["rules"] = [rule for rule in list(cfg.get("rules") or []) if str(rule.get("id") or "") != rule_id]
        save_group_auto_mute(group_id, cfg)
        await show_automute_menu(update, context, state, note="自动禁言规则已删除")
        return

    if data in {"admin:aw", "admin:aw:menu"}:
        await show_autowarn_menu(update, context, state)
        return
    if data == "admin:aw:toggle:enabled":
        cfg = get_group_auto_warn(group_id)
        cfg["enabled"] = not bool(cfg.get("enabled", True))
        save_group_auto_warn(group_id, cfg)
        await safe_answer(query, "已打开" if cfg["enabled"] else "已关闭", show_alert=True)
        await show_autowarn_menu(update, context, state)
        return
    if data == "admin:aw_cmd_toggle":
        cfg = get_group_auto_warn(group_id)
        cfg["cmd_mute_enabled"] = not bool(cfg.get("cmd_mute_enabled", False))
        save_group_auto_warn(group_id, cfg)
        await safe_answer(query, "已打开" if cfg["cmd_mute_enabled"] else "已关闭", show_alert=True)
        await show_autowarn_menu(update, context, state)
        return
    if data == "admin:aw:limit":
        await _begin_input(update, context, user_id, state, STATE_AW_LIMIT, "请输入警告上限")
        return
    if data == "admin:aw:mute":
        await _begin_input(update, context, user_id, state, STATE_AW_MUTE, "请输入处罚时长，单位秒")
        return
    if data == "admin:aw:text":
        await _begin_input(update, context, user_id, state, STATE_AW_TEXT, "请输入警告文案")
        return
    if data == "admin:aw:add":
        await _begin_input(update, context, user_id, state, STATE_AW_RULE_KEYWORD, "请输入警告关键词")
        return
    if data.startswith("admin:aw:rule:mode:"):
        mode = data.rsplit(":", 1)[-1]
        tmp = dict(state.get("tmp") or {})
        rule = dict(tmp.get("aw_rule") or {})
        if not rule.get("keyword"):
            await safe_answer(query, "请先输入关键词", show_alert=True)
            return
        rule["id"] = rule.get("id") or _rule_id()
        rule["mode"] = mode
        cfg = get_group_auto_warn(group_id)
        rules = list(cfg.get("rules") or [])
        rules.append(rule)
        cfg["rules"] = rules
        save_group_auto_warn(group_id, cfg)
        next_state = _state_with(state, state=STATE_NONE, tmp={})
        _save_state(user_id, next_state)
        await show_autowarn_menu(update, context, next_state, note="自动警告规则已添加")
        return
    if data.startswith("admin:aw:del:"):
        rule_id = data.rsplit(":", 1)[-1]
        cfg = get_group_auto_warn(group_id)
        cfg["rules"] = [rule for rule in list(cfg.get("rules") or []) if str(rule.get("id") or "") != rule_id]
        save_group_auto_warn(group_id, cfg)
        await show_autowarn_menu(update, context, state, note="自动警告规则已删除")
        return

    if data in {"admin:spam", "admin:spam:menu"}:
        await show_antispam_menu(update, context, state)
        return
    if data == "admin:spam_toggle":
        cfg = get_group_anti_spam(group_id)
        cfg["enabled"] = not bool(cfg.get("enabled", False))
        save_group_anti_spam(group_id, cfg)
        await safe_answer(query, "已打开" if cfg["enabled"] else "已关闭", show_alert=True)
        await show_antispam_menu(update, context, state)
        return
    if data.startswith("admin:spam:action:"):
        action = data.rsplit(":", 1)[-1]
        cfg = get_group_anti_spam(group_id)
        cfg["action"] = action
        save_group_anti_spam(group_id, cfg)
        await show_antispam_menu(update, context, state, note="刷屏处理方式已更新")
        return
    if data == "admin:spam:window":
        await _begin_input(update, context, user_id, state, STATE_SPAM_WINDOW, "请输入检测时间窗口，单位秒")
        return
    if data == "admin:spam:threshold":
        await _begin_input(update, context, user_id, state, STATE_SPAM_THRESHOLD, "请输入触发次数")
        return
    if data == "admin:spam:mute":
        await _begin_input(update, context, user_id, state, STATE_SPAM_MUTE, "请输入处罚时长，单位秒")
        return
    if data.startswith("admin:spam_type:"):
        key = data.rsplit(":", 1)[-1]
        cfg = get_group_anti_spam(group_id)
        types = set(cfg.get("types") or [])
        if key in types:
            types.remove(key)
        else:
            types.add(key)
        cfg["types"] = [item for item in SPAM_TYPE_LABELS if item in types]
        save_group_anti_spam(group_id, cfg)
        await safe_answer(query, "已打开" if key in types else "已关闭", show_alert=True)
        await show_antispam_menu(update, context, state)
        return

    await safe_answer(query)
