from __future__ import annotations

import html
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

from ..services.extra_features import get_active_lottery, load_schedule_items, parse_schedule_message_input, publish_lottery, save_schedule_items
from ..services.membership import group_plan_label, schedule_limit_for_group
from ..storage.config_store import get_group_auto_warn, get_group_config, save_admin_state, save_group_auto_warn, save_group_config
from ..utils.telegram import safe_answer, safe_edit_message

MENU_ROUTES = {}
SCHEDULE_DRAFTS: dict[int, dict] = {}


COMMAND_GATE_LABELS = {
    "sign": "签到",
    "profile": "资料",
    "warn": "警告",
    "help": "帮助",
    "config": "配置",
    "ban": "封禁",
    "kick": "踢出",
    "mute": "禁言",
}


ADMIN_ACCESS_MODE_LABELS = {
    "all_admins": "所有管理员",
    "service_owner": "仅服务拥有者",
}


SENSITIVITY_LABELS = {
    "low": "低",
    "medium": "中",
    "high": "高",
}


def _button_icon(data: str, label: str) -> str:
    text = str(label or "").strip()
    route = str(data or "")
    if route == "admin:main":
        return "🏠"
    if route.startswith("adminx:cancel_input"):
        return "❌"
    if route.startswith("adminx:rich:text"):
        return "📝"
    if route.startswith("adminx:rich:photo"):
        return "🖼️"
    if route.startswith("adminx:rich:clear_photo"):
        return "🧽"
    if route.startswith("adminx:rich:buttons"):
        return "🔘"
    if route.startswith("adminx:rich:interval"):
        return "⏱️"
    if route.startswith("adminx:rich:save"):
        return "💾"
    if route.startswith("adminx:schedule:delete") or route.startswith("adminx:rich:schedule:delete"):
        return "🗑️"
    if route.startswith("adminx:schedule:edit"):
        return "✏️"
    if route.startswith("adminx:schedule:toggle") or route.startswith("adminx:rich:schedule:toggle"):
        return "🔁"
    if route.startswith("adminx:invite:prompt") or route.startswith("adminx:related:prompt"):
        return "✏️"
    if ":add" in route:
        return "➕"
    if ":delete" in route or ":del:" in route:
        return "🗑️"
    if any(token in route for token in (":interval", ":delay_delete_sec", ":reward_points", ":query_command", ":today_rank_command", ":month_rank_command", ":total_rank_command", ":auto_delete_sec", ":dice_cost", ":dice_command", ":gomoku_command")):
        return "⏱️"
    module_icons = (
        ("adminx:ad", "📵"),
        ("adminx:cmd", "⌨️"),
        ("adminx:crypto", "💎"),
        ("adminx:invite", "🔗"),
        ("adminx:member", "👥"),
        ("adminx:fun", "🎮"),
        ("adminx:related", "📡"),
        ("adminx:schedule", "⏰"),
        ("adminx:lang", "🌐"),
        ("adminx:admin_access", "🛡️"),
        ("adminx:nsfw", "🔞"),
        ("adminx:verified", "🪪"),
        ("adminx:lottery", "🎁"),
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
        "Back": "返回",
        "Cancel input": "取消输入",
        "Delete": "删除",
        "Edit": "编辑",
        "Toggle": "切换",
        "Set text": "设置文本",
        "Set photo": "设置图片",
        "Clear photo": "清除图片",
        "Edit buttons": "设置按钮",
        "Set interval": "设置间隔",
        "Save scheduled message": "保存定时消息",
        "Toggle enabled": "切换启用",
    }.get(text, text)
    if text == "返回" and data == "admin:main":
        text = "返回主菜单"
    icon = _button_icon(data, text)
    return text if text.startswith(f"{icon} ") else (f"{icon} {text}" if text else icon)


def _btn(label: str, data: str):
    return InlineKeyboardButton(_button_text(label, data), callback_data=data)


def _escape(value: str) -> str:
    return html.escape(str(value or ""))


def _preview(value: str, fallback: str = "未设置", limit: int = 40) -> str:
    text = str(value or "").strip()
    if not text:
        return fallback
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _checked(enabled: bool) -> str:
    return "✅" if enabled else "⬜"


def _chosen(enabled: bool) -> str:
    return "✅" if enabled else "⚪"


def _toggle_notice(enabled: bool) -> str:
    return "已开启" if enabled else "已关闭"


def _sensitivity_label(value: str) -> str:
    return SENSITIVITY_LABELS.get(str(value or "").strip().lower(), str(value or ""))

def _nsfw_effective_threshold(cfg: dict | None) -> int:
    level = str((cfg or {}).get("sensitivity") or "medium").strip().lower()
    threshold = {"high": 1, "medium": 2, "low": 3}.get(level, 2)
    if bool((cfg or {}).get("allow_miss")):
        threshold += 1
    return threshold

def _normalize_buttons(buttons) -> list[dict]:
    rows = []
    for item in list(buttons or []):
        if not isinstance(item, dict):
            continue
        try:
            row = max(0, int(item.get("row", 0) or 0))
        except (TypeError, ValueError):
            row = 0
        rows.append({
            "text": str(item.get("text") or "按钮"),
            "type": str(item.get("type") or "url"),
            "value": str(item.get("value") or ""),
            "row": row,
        })
    return rows


def _normalize_message_payload(data: dict | None) -> dict:
    payload = dict(data or {})
    return {
        "text": str(payload.get("text") or ""),
        "photo_file_id": str(payload.get("photo_file_id") or ""),
        "buttons": _normalize_buttons(payload.get("buttons") or []),
    }


def _normalize_schedule_draft(data: dict | None = None) -> dict:
    payload = _normalize_message_payload(data)
    try:
        interval_sec = int((data or {}).get("interval_sec", 3600) or 3600)
    except (TypeError, ValueError):
        interval_sec = 3600
    payload["interval_sec"] = max(60, interval_sec)
    return payload


def _draft_target(user_id: int) -> str:
    return f"schedule_draft:{int(user_id)}"


def _parse_draft_target(target: str) -> int | None:
    text = str(target or "")
    if not text.startswith("schedule_draft:"):
        return None
    try:
        return int(text.split(":", 1)[1])
    except (IndexError, ValueError):
        return None


def _get_schedule_item_by_id(group_id: int, schedule_id: int):
    items = load_schedule_items(group_id)
    for idx, item in enumerate(items):
        try:
            item_id = int(item.get("id") or 0)
        except (TypeError, ValueError):
            item_id = 0
        if item_id == schedule_id:
            return idx, item, items
    return -1, None, items


def _rich_target_meta(target: str) -> dict | None:
    table = {
        "verify_fail": ("验证失败消息", "admin:verify", "verify", "验证失败时发送。"),
        "autowarn": ("自动警告文案", "admin:aw", "autowarn", "命中自动警告规则时发送。"),
        "invite_notify": ("邀请成功消息", "adminx:invite:menu", "invite", "用户通过邀请入群后发送。"),
        "related_comment": ("关联频道评论文案", "adminx:related:menu", "related", "转发关联频道消息时在评论区回复。"),
    }
    if target in table:
        title, back, prompt_module, hint = table[target]
        return {"title": title, "back": back, "prompt_module": prompt_module, "hint": hint}
    if target.startswith("schedule:"):
        return {"title": "定时消息", "back": "adminx:schedule:list", "prompt_module": "schedule", "hint": "支持文字、图片和按钮。"}
    if _parse_draft_target(target) is not None:
        return {"title": "新建定时消息", "back": "adminx:schedule:menu", "prompt_module": "schedule", "hint": "先编辑内容，再保存到定时列表。"}
    return None


def get_rich_message_target(group_id: int, target: str):
    draft_user_id = _parse_draft_target(target)
    if draft_user_id is not None:
        return _normalize_schedule_draft(SCHEDULE_DRAFTS.get(draft_user_id))
    if target == "verify_fail":
        cfg = get_group_config(group_id)
        return _normalize_message_payload({"text": cfg.get("verify_fail_text"), "photo_file_id": cfg.get("verify_fail_photo_file_id"), "buttons": cfg.get("verify_fail_buttons")})
    if target == "autowarn":
        cfg = get_group_auto_warn(group_id) or {}
        return _normalize_message_payload({"text": cfg.get("warn_text"), "photo_file_id": cfg.get("warn_photo_file_id"), "buttons": cfg.get("warn_buttons")})
    if target == "invite_notify":
        cfg = get_group_config(group_id).get("invite_links", {}) or {}
        return _normalize_message_payload({"text": cfg.get("notify_text"), "photo_file_id": cfg.get("notify_photo_file_id"), "buttons": cfg.get("notify_buttons")})
    if target == "related_comment":
        cfg = get_group_config(group_id).get("related_channel", {}) or {}
        return _normalize_message_payload({"text": cfg.get("occupy_comment_text"), "photo_file_id": cfg.get("occupy_comment_photo_file_id"), "buttons": cfg.get("occupy_comment_buttons")})
    if target.startswith("schedule:"):
        try:
            schedule_id = int(target.split(":", 1)[1])
        except (IndexError, ValueError):
            return None
        _, item, _ = _get_schedule_item_by_id(group_id, schedule_id)
        return _normalize_schedule_draft(item) if item else None
    return None


def save_rich_message_target(group_id: int, target: str, payload: dict) -> bool:
    draft_user_id = _parse_draft_target(target)
    message = _normalize_message_payload(payload)
    if draft_user_id is not None:
        current = _normalize_schedule_draft(SCHEDULE_DRAFTS.get(draft_user_id))
        current.update(message)
        SCHEDULE_DRAFTS[draft_user_id] = _normalize_schedule_draft(current)
        return True
    if target == "verify_fail":
        cfg = get_group_config(group_id)
        cfg["verify_fail_text"] = message["text"]
        cfg["verify_fail_photo_file_id"] = message["photo_file_id"]
        cfg["verify_fail_buttons"] = message["buttons"]
        save_group_config(group_id, cfg)
        return True
    if target == "autowarn":
        cfg = get_group_auto_warn(group_id) or {}
        cfg["warn_text"] = message["text"]
        cfg["warn_photo_file_id"] = message["photo_file_id"]
        cfg["warn_buttons"] = message["buttons"]
        save_group_auto_warn(group_id, cfg)
        return True
    if target == "invite_notify":
        cfg = get_group_config(group_id)
        data = dict(cfg.get("invite_links", {}) or {})
        data.update({"notify_text": message["text"], "notify_photo_file_id": message["photo_file_id"], "notify_buttons": message["buttons"]})
        cfg["invite_links"] = data
        save_group_config(group_id, cfg)
        return True
    if target == "related_comment":
        cfg = get_group_config(group_id)
        data = dict(cfg.get("related_channel", {}) or {})
        data.update({"occupy_comment_text": message["text"], "occupy_comment_photo_file_id": message["photo_file_id"], "occupy_comment_buttons": message["buttons"]})
        cfg["related_channel"] = data
        save_group_config(group_id, cfg)
        return True
    if target.startswith("schedule:"):
        try:
            schedule_id = int(target.split(":", 1)[1])
        except (IndexError, ValueError):
            return False
        idx, item, items = _get_schedule_item_by_id(group_id, schedule_id)
        if item is None or idx < 0:
            return False
        item["text"] = message["text"]
        item["photo_file_id"] = message["photo_file_id"]
        item["buttons"] = message["buttons"]
        items[idx] = item
        save_schedule_items(group_id, items)
        return True
    return False


def _state_with(base_state: dict | None, **kwargs):
    state = dict(base_state or {})
    state.update(kwargs)
    return state


def _save_state(user_id: int, state: dict):
    save_admin_state(user_id, state)


def _group_id(state: dict | None) -> int:
    try:
        return int((state or {}).get("active_group_id") or 0)
    except (TypeError, ValueError):
        return 0


async def _send_or_edit(update, text: str, reply_markup=None):
    if getattr(update, "callback_query", None):
        return await safe_edit_message(update.callback_query, text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    if update.effective_message:
        return await update.effective_message.reply_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    return None


def _prompt_markup(module: str):
    return InlineKeyboardMarkup([[_btn("取消输入", f"adminx:cancel_input:{module}")]])


async def _begin_input(update, user_id: int, state: dict, new_state: str, module: str, prompt: str):
    next_state = _state_with(state, state=new_state)
    _save_state(user_id, next_state)
    await _send_or_edit(update, prompt, _prompt_markup(module))


def _nested_set(data: dict, path: tuple[str, ...], value):
    current = data
    for key in path[:-1]:
        node = current.get(key)
        if not isinstance(node, dict):
            node = {}
            current[key] = node
        current = node
    current[path[-1]] = value


def _toggle(data: dict, path: tuple[str, ...]) -> bool:
    current = data
    for key in path[:-1]:
        node = current.get(key)
        if not isinstance(node, dict):
            node = {}
            current[key] = node
        current = node
    current[path[-1]] = not bool(current.get(path[-1], False))
    return bool(current[path[-1]])


def _toggle_config(group_id: int, path: tuple[str, ...]) -> bool:
    cfg = get_group_config(group_id)
    enabled = _toggle(cfg, path)
    save_group_config(group_id, cfg)
    return enabled


async def _show_menu(module_name: str, update, context, state: dict):
    del context
    func = MENU_ROUTES.get(module_name)
    if func:
        return await func(update, None, state)
    await _send_or_edit(update, f"未知模块：{module_name}")


async def show_rich_message_editor(update, context, state: dict, target: str):
    del context
    group_id = _group_id(state)
    meta = _rich_target_meta(target)
    payload = get_rich_message_target(group_id, target) if group_id else None
    if not meta or payload is None:
        await _send_or_edit(update, "未找到消息目标。")
        return
    lines = [meta["title"], f"文本：{_escape(_preview(payload.get('text', ''), '空', 120))}", f"图片：{'已设置' if payload.get('photo_file_id') else '未设置'}", f"按钮：{len(payload.get('buttons', []) or [])}"]
    rows = [
        [_btn("设置文本", f"adminx:rich:text:{target}"), _btn("设置图片", f"adminx:rich:photo:{target}")],
        [_btn("清除图片", f"adminx:rich:clear_photo:{target}"), _btn("设置按钮", f"adminx:rich:buttons:{target}")],
    ]
    draft_user_id = _parse_draft_target(target)
    if draft_user_id is not None or str(target).startswith("schedule:"):
        interval_min = max(1, int(payload.get("interval_sec", 3600) or 3600) // 60)
        lines.append(f"间隔：{interval_min} 分钟")
        rows.append([_btn("设置间隔", f"adminx:rich:interval:{target}")])
        if draft_user_id is not None:
            rows.append([_btn("保存定时消息", f"adminx:rich:save:{target}")])
        else:
            try:
                schedule_id = int(str(target).split(":", 1)[1])
            except (IndexError, ValueError):
                schedule_id = 0
            idx, item, _ = _get_schedule_item_by_id(group_id, schedule_id)
            if item is not None and idx >= 0:
                next_at = int(item.get("next_at", 0) or 0)
                next_text = time.strftime("%m-%d %H:%M", time.localtime(next_at)) if next_at > 0 else "未知"
                lines.append(f"已启用：{_checked(item.get('enabled', True))}")
                lines.append(f"下次执行：{next_text}")
                rows.append([_btn("切换启用", f"adminx:rich:schedule:toggle:{schedule_id}"), _btn("删除", f"adminx:rich:schedule:delete:{schedule_id}")])
    lines.append(meta["hint"])
    rows.append([_btn("返回", meta["back"])])
    await _send_or_edit(update, "\n".join(lines), InlineKeyboardMarkup(rows))


async def show_ad_filter_menu(update, context, state: dict):
    del context
    group_id = _group_id(state)
    cfg = get_group_config(group_id).get("ad_filter", {}) or {}
    rows = [[_btn(f"{_checked(cfg.get(key, False))} {label}", f"adminx:ad:toggle:{key}")] for key, label in (("nickname_enabled", "昵称"), ("sticker_enabled", "贴纸"), ("message_enabled", "消息"), ("block_channel_mask", "频道马甲"))]
    rows.append([_btn("返回主菜单", "admin:main")])
    await _send_or_edit(update, "广告过滤", InlineKeyboardMarkup(rows))


async def show_command_gate_menu(update, context, state: dict):
    del context
    group_id = _group_id(state)
    gate = get_group_config(group_id).get("command_gate", {}) or {}
    rows = [[_btn(f"{_checked(gate.get(key, False))} {COMMAND_GATE_LABELS.get(key, key)}", f"adminx:cmd:toggle:{key}")] for key in ("sign", "profile", "warn", "help", "config", "ban", "kick", "mute")]
    rows.append([_btn("返回主菜单", "admin:main")])
    await _send_or_edit(update, "群组命令", InlineKeyboardMarkup(rows))


async def show_crypto_menu(update, context, state: dict):
    del context
    group_id = _group_id(state)
    cfg = get_group_config(group_id).get("crypto", {}) or {}
    text = "\n".join(["地址查询", f"钱包查询：{_checked(cfg.get('wallet_query_enabled', True))}", f"价格查询：{_checked(cfg.get('price_query_enabled', True))}", f"价格推送：{_checked(cfg.get('push_enabled', False))}"])
    rows = [
        [_btn(f"{_checked(cfg.get('wallet_query_enabled', True))} 钱包查询", "adminx:crypto:toggle:wallet_query_enabled")],
        [_btn(f"{_checked(cfg.get('price_query_enabled', True))} 价格查询", "adminx:crypto:toggle:price_query_enabled")],
        [_btn(f"{_checked(cfg.get('push_enabled', False))} 价格推送", "adminx:crypto:toggle:push_enabled")],
        [_btn("返回主菜单", "admin:main")],
    ]
    await _send_or_edit(update, text, InlineKeyboardMarkup(rows))


async def show_related_menu(update, context, state: dict):
    del context
    group_id = _group_id(state)
    cfg = get_group_config(group_id).get("related_channel", {}) or {}
    rows = [
        [_btn(f"{_checked(cfg.get('cancel_top_pin', False))} 取消置顶", "adminx:related:toggle:cancel_top_pin")],
        [_btn(f"{_checked(cfg.get('occupy_comment', False))} 占位评论", "adminx:related:toggle:occupy_comment")],
        [_btn("编辑评论文案", "adminx:related:prompt:occupy_comment_text")],
        [_btn("返回主菜单", "admin:main")],
    ]
    await _send_or_edit(update, f"关联频道\n评论文案：{_escape(_preview(cfg.get('occupy_comment_text', ''), '空'))}", InlineKeyboardMarkup(rows))


async def show_invite_menu(update, context, state: dict):
    del context
    group_id = _group_id(state)
    cfg = get_group_config(group_id).get("invite_links", {}) or {}
    rows = [
        [_btn(f"{_checked(cfg.get('enabled', False))} 启用邀请链接", "adminx:invite:toggle:enabled")],
        [_btn(f"{_checked(cfg.get('notify_enabled', False))} 群内通知", "adminx:invite:toggle:notify_enabled")],
        [_btn(f"{_checked(cfg.get('join_review', False))} 入群审核", "adminx:invite:toggle:join_review")],
        [_btn("编辑通知文案", "adminx:invite:prompt:notify_text")],
        [_btn("奖励积分", "adminx:invite:prompt:reward_points"), _btn("查询命令", "adminx:invite:prompt:query_command")],
        [_btn("今日排行命令", "adminx:invite:prompt:today_rank_command"), _btn("本月排行命令", "adminx:invite:prompt:month_rank_command")],
        [_btn("总排行命令", "adminx:invite:prompt:total_rank_command"), _btn("自动删除秒数", "adminx:invite:prompt:auto_delete_sec")],
        [_btn("返回主菜单", "admin:main")],
    ]
    await _send_or_edit(update, f"邀请链接\n通知文案：{_escape(_preview(cfg.get('notify_text', ''), '空'))}", InlineKeyboardMarkup(rows))


async def show_member_menu(update, context, state: dict):
    del context
    group_id = _group_id(state)
    cfg = get_group_config(group_id).get("member_watch", {}) or {}
    rows = [
        [_btn(f"{_checked(cfg.get('nickname_change_detect', False))} 昵称变更检测", "adminx:member:toggle:nickname_change_detect")],
        [_btn(f"{_checked(cfg.get('nickname_change_notice', False))} 昵称变更提醒", "adminx:member:toggle:nickname_change_notice")],
        [_btn("返回主菜单", "admin:main")],
    ]
    await _send_or_edit(update, "群组成员", InlineKeyboardMarkup(rows))


async def show_fun_menu(update, context, state: dict):
    del context
    group_id = _group_id(state)
    cfg = get_group_config(group_id).get("entertainment", {}) or {}
    lines = [
        "娱乐功能",
        f"骰子：{_checked(cfg.get('dice_enabled', True))}",
        f"骰子积分：{int(cfg.get('dice_cost', 0) or 0)}",
        f"骰子命令：{_escape(str(cfg.get('dice_command') or '/dice'))}",
        f"五子棋：{_checked(cfg.get('gomoku_enabled', False))}",
        f"五子棋命令：{_escape(str(cfg.get('gomoku_command') or '/gomoku'))}",
    ]
    rows = [
        [_btn(f"{_checked(cfg.get('dice_enabled', True))} 启用骰子", "adminx:fun:toggle:dice_enabled")],
        [_btn("设置骰子积分", "adminx:fun:prompt:dice_cost"), _btn("设置骰子命令", "adminx:fun:prompt:dice_command")],
        [_btn(f"{_checked(cfg.get('gomoku_enabled', False))} 启用五子棋", "adminx:fun:toggle:gomoku_enabled")],
        [_btn("设置五子棋命令", "adminx:fun:prompt:gomoku_command")],
        [_btn("返回主菜单", "admin:main")],
    ]
    await _send_or_edit(update, "\n".join(lines), InlineKeyboardMarkup(rows))


async def show_language_menu(update, context, state: dict):
    del context
    group_id = _group_id(state)
    cfg = get_group_config(group_id).get("language_whitelist", {}) or {}
    rows = [[_btn(f"{_checked(cfg.get('enabled', False))} 启用白名单", "adminx:lang:toggle:enabled")], [_btn("设置允许语言", "adminx:lang:prompt:allowed")], [_btn("返回主菜单", "admin:main")]]
    allowed = ", ".join(cfg.get("allowed", []) or []) or "（空）"
    await _send_or_edit(update, f"语言白名单\n允许语言：{allowed}", InlineKeyboardMarkup(rows))


async def show_admin_access_menu(update, context, state: dict):
    del context
    group_id = _group_id(state)
    cfg = get_group_config(group_id)
    access_cfg = cfg.get("admin_access", {}) or {}
    mode = str(access_cfg.get("mode") or "all_admins")
    owner_id = int(cfg.get("service_owner_user_id") or 0)
    lines = [
        "管理权限",
        f"模式：{_escape(ADMIN_ACCESS_MODE_LABELS.get(mode, mode))}",
        f"服务拥有者：{owner_id or '未绑定'}",
    ]
    rows = [
        [_btn(f"{_chosen(mode == 'all_admins')} 所有管理员", "adminx:admin_access:set:all_admins")],
        [_btn(f"{_chosen(mode == 'service_owner')} 仅服务拥有者", "adminx:admin_access:set:service_owner")],
        [_btn("返回主菜单", "admin:main")],
    ]
    await _send_or_edit(update, "\n".join(lines), InlineKeyboardMarkup(rows))


async def show_nsfw_menu(update, context, state: dict):
    del context
    group_id = _group_id(state)
    cfg = get_group_config(group_id).get("nsfw", {}) or {}
    try:
        delay_delete_sec = max(0, int(cfg.get("delay_delete_sec", 0) or 0))
    except (TypeError, ValueError):
        delay_delete_sec = 0
    sensitivity = str(cfg.get("sensitivity") or "medium").strip().lower() or "medium"
    lines = [
        "NSFW过滤",
        f"已启用：{_checked(cfg.get('enabled', False))}",
        f"敏感度：{_escape(_sensitivity_label(sensitivity))}",
        f"允许漏判：{_checked(cfg.get('allow_miss', False))}",
        f"发送提示：{_checked(cfg.get('notice_enabled', True))}",
        f"提示删除秒数：{delay_delete_sec}",
        f"生效阈值：{_nsfw_effective_threshold(cfg)}",
    ]
    rows = [
        [_btn(f"{_checked(cfg.get('enabled', False))} 启用NSFW过滤", "adminx:nsfw:toggle:enabled")],
        [_btn(f"敏感度：{_sensitivity_label(sensitivity)}", "adminx:nsfw:cycle:sensitivity")],
        [_btn(f"{_checked(cfg.get('allow_miss', False))} 允许漏判", "adminx:nsfw:toggle:allow_miss")],
        [_btn(f"{_checked(cfg.get('notice_enabled', True))} 发送删除提示", "adminx:nsfw:toggle:notice_enabled")],
        [_btn("设置提示删除秒数", "adminx:nsfw:prompt:delay_delete_sec")],
        [_btn("返回主菜单", "admin:main")],
    ]
    await _send_or_edit(update, "\n".join(lines), InlineKeyboardMarkup(rows))


async def show_schedule_menu(update, context, state: dict):
    del context
    group_id = _group_id(state)
    items = load_schedule_items(group_id)
    enabled_count = sum(1 for item in items if item.get("enabled", True))
    limit = schedule_limit_for_group(group_id)
    text = "\n".join(["定时消息", f"套餐：{group_plan_label(group_id)}", f"数量：{len(items)}/{limit}", f"已启用：{enabled_count}"])
    rows = [[_btn("新增定时消息", "adminx:schedule:add")], [_btn("查看全部定时消息", "adminx:schedule:list")], [_btn("返回主菜单", "admin:main")]]
    await _send_or_edit(update, text, InlineKeyboardMarkup(rows))


async def show_schedule_list_menu(update, context, state: dict):
    del context
    group_id = _group_id(state)
    items = load_schedule_items(group_id)
    lines = ["定时消息列表", ""]
    rows = []
    if not items:
        lines.append("还没有定时消息。")
    else:
        for idx, item in enumerate(items):
            interval_min = max(1, int(item.get("interval_sec", 0) or 0) // 60)
            next_at = int(item.get("next_at", 0) or 0)
            next_text = time.strftime("%m-%d %H:%M", time.localtime(next_at)) if next_at > 0 else "未知"
            preview = _escape(_preview(item.get("text", ""), "（仅图片/按钮）", 24))
            lines.append(f"{idx + 1}. {_checked(item.get('enabled', True))} {preview}")
            lines.append(f"   每 {interval_min} 分钟一次，下次执行 {next_text}")
            lines.append(f"   按钮：{len(item.get('buttons', []) or [])}")
            rows.append([_btn("编辑", f"adminx:schedule:edit:{idx}"), _btn("切换", f"adminx:schedule:toggle:{idx}")])
            rows.append([_btn("删除", f"adminx:schedule:delete:{idx}")])
    rows.append([_btn("返回", "adminx:schedule:menu")])
    await _send_or_edit(update, "\n".join(lines), InlineKeyboardMarkup(rows))


async def show_lottery_menu(update, context, state: dict):
    del context
    group_id = _group_id(state)
    cfg = get_group_config(group_id).get("lottery", {}) or {}
    active = get_active_lottery(group_id)
    lines = ["抽奖活动", f"已启用：{_checked(cfg.get('enabled', False))}", f"查询命令：{_escape(str(cfg.get('query_command') or 'lottery'))}", f"置顶消息：{_checked(cfg.get('pin_post', False))}"]
    if active:
        lines.append(f"当前活动：{_escape(_preview(active.get('title', ''), '抽奖活动', 60))}")
        lines.append(f"参与人数：{len(active.get('participants') or [])}")
    else:
        lines.append("当前活动：无")
    rows = [
        [_btn(f"{_checked(cfg.get('enabled', False))} 启用抽奖", "adminx:lottery:toggle:enabled")],
        [_btn(f"{_checked(cfg.get('pin_post', False))} 置顶消息", "adminx:lottery:toggle:pin_post")],
        [_btn("设置查询命令", "adminx:lottery:prompt:query_command")],
        [_btn("发布抽奖", "adminx:lottery:publish")],
        [_btn("返回主菜单", "admin:main")],
    ]
    await _send_or_edit(update, "\n".join(lines), InlineKeyboardMarkup(rows))


async def show_verified_placeholder(update, context, state: dict):
    del context
    group_id = _group_id(state)
    cfg = get_group_config(group_id).get("verified_user", {}) or {}
    rows = [[_btn(f"{_checked(cfg.get('enabled', False))} 启用认证用户模块", "adminx:verified:toggle:enabled")], [_btn("返回主菜单", "admin:main")]]
    text = "\n".join(["认证用户", f"模块开关：{_checked(cfg.get('enabled', False))}", "当前重建版本这里只提供模块开关，详细用户管理仍在 Web 后台。"])
    await _send_or_edit(update, text, InlineKeyboardMarkup(rows))


MENU_ROUTES.update({
    "ad": show_ad_filter_menu,
    "cmd": show_command_gate_menu,
    "crypto": show_crypto_menu,
    "invite": show_invite_menu,
    "member": show_member_menu,
    "fun": show_fun_menu,
    "related": show_related_menu,
    "schedule": show_schedule_menu,
    "lang": show_language_menu,
    "admin_access": show_admin_access_menu,
    "nsfw": show_nsfw_menu,
    "verified": show_verified_placeholder,
    "lottery": show_lottery_menu,
})


_PROMPT_ROUTES = {
    "adminx:invite:prompt:reward_points": ("x:invite:reward_points", "invite", "请输入奖励积分，必须是整数。"),
    "adminx:invite:prompt:query_command": ("x:invite:query_command", "invite", "请输入邀请查询命令。"),
    "adminx:invite:prompt:today_rank_command": ("x:invite:today_rank_command", "invite", "请输入今日排行命令。"),
    "adminx:invite:prompt:month_rank_command": ("x:invite:month_rank_command", "invite", "请输入本月排行命令。"),
    "adminx:invite:prompt:total_rank_command": ("x:invite:total_rank_command", "invite", "请输入总排行命令。"),
    "adminx:invite:prompt:auto_delete_sec": ("x:invite:auto_delete_sec", "invite", "请输入自动删除秒数，必须是整数。"),
    "adminx:lang:prompt:allowed": ("x:lang:allowed", "lang", "请输入允许的语言代码，多个用逗号分隔，例如：zh,en"),
    "adminx:nsfw:prompt:delay_delete_sec": ("x:nsfw:delay_delete_sec", "nsfw", "请输入提示删除秒数，必须是整数。"),
    "adminx:fun:prompt:dice_cost": ("x:fun:dice_cost", "fun", "请输入骰子积分，必须是整数。"),
    "adminx:fun:prompt:dice_command": ("x:fun:dice_command", "fun", "请输入骰子命令。"),
    "adminx:fun:prompt:gomoku_command": ("x:fun:gomoku_command", "fun", "请输入五子棋命令。"),
    "adminx:lottery:prompt:query_command": ("x:lottery:query_command", "lottery", "请输入抽奖查询命令。"),
}


_VALUE_ROUTES = {
    "x:invite:reward_points": (("invite_links", "reward_points"), "invite", lambda value: max(0, int(value))),
    "x:invite:query_command": (("invite_links", "query_command"), "invite", lambda value: value.strip() or "/link"),
    "x:invite:today_rank_command": (("invite_links", "today_rank_command"), "invite", lambda value: value.strip() or "today_rank"),
    "x:invite:month_rank_command": (("invite_links", "month_rank_command"), "invite", lambda value: value.strip() or "month_rank"),
    "x:invite:total_rank_command": (("invite_links", "total_rank_command"), "invite", lambda value: value.strip() or "total_rank"),
    "x:invite:auto_delete_sec": (("invite_links", "auto_delete_sec"), "invite", lambda value: max(0, int(value))),
    "x:lang:allowed": (("language_whitelist", "allowed"), "lang", lambda value: [part.strip() for part in value.split(",") if part.strip()]),
    "x:nsfw:delay_delete_sec": (("nsfw", "delay_delete_sec"), "nsfw", lambda value: max(0, int(value))),
    "x:fun:dice_cost": (("entertainment", "dice_cost"), "fun", lambda value: max(0, int(value))),
    "x:fun:dice_command": (("entertainment", "dice_command"), "fun", lambda value: value.strip() or "/dice"),
    "x:fun:gomoku_command": (("entertainment", "gomoku_command"), "fun", lambda value: value.strip() or "/gomoku"),
    "x:lottery:query_command": (("lottery", "query_command"), "lottery", lambda value: value.strip() or "lottery"),
}


async def handle_admin_extra_message(update, context, state: dict, msg_text: str) -> bool:
    user_id = update.effective_user.id
    group_id = _group_id(state)
    current_state = str((state or {}).get("state") or "")
    if not group_id:
        await update.effective_message.reply_text("请先选择群组。")
        return True
    if current_state == "x:schedule:add":
        photo_file_id = ""
        photos = getattr(update.effective_message, "photo", None) or []
        if photos:
            photo_file_id = photos[-1].file_id
        try:
            item = parse_schedule_message_input((msg_text or "").strip(), photo_file_id=photo_file_id)
        except Exception as exc:
            await update.effective_message.reply_text(str(exc))
            return True
        items = load_schedule_items(group_id)
        limit = schedule_limit_for_group(group_id)
        if len(items) >= limit:
            await update.effective_message.reply_text(f"当前套餐最多支持 {limit} 条定时消息。")
            return True
        items.append(item)
        save_schedule_items(group_id, items)
        new_state = _state_with(state, state=None, tmp={})
        _save_state(user_id, new_state)
        await show_schedule_list_menu(update, context, new_state)
        return True
    if current_state.startswith("x:rich:text:") or current_state.startswith("x:rich:photo:"):
        target = current_state.split(":", 3)[-1]
        payload = get_rich_message_target(group_id, target)
        if payload is None:
            await update.effective_message.reply_text("未找到消息目标。")
            return True
        if current_state.startswith("x:rich:text:"):
            payload["text"] = (msg_text or "").strip()
        else:
            photos = getattr(update.effective_message, "photo", None) or []
            if not photos:
                await update.effective_message.reply_text("请发送图片。")
                return True
            payload["photo_file_id"] = photos[-1].file_id
        if not save_rich_message_target(group_id, target, payload):
            await update.effective_message.reply_text("保存失败，请重试。")
            return True
        new_state = _state_with(state, state=None, tmp={})
        _save_state(user_id, new_state)
        await show_rich_message_editor(update, context, new_state, target)
        return True
    if current_state.startswith("x:schedule:interval:"):
        target = current_state.split("x:schedule:interval:", 1)[1]
        try:
            minutes = int((msg_text or "").strip())
        except ValueError:
            await update.effective_message.reply_text("请输入整数分钟数。")
            return True
        interval_sec = max(60, minutes * 60)
        draft_user_id = _parse_draft_target(target)
        if draft_user_id is not None:
            draft = _normalize_schedule_draft(SCHEDULE_DRAFTS.get(draft_user_id))
            draft["interval_sec"] = interval_sec
            SCHEDULE_DRAFTS[draft_user_id] = draft
        else:
            try:
                schedule_id = int(str(target).split(":", 1)[1])
            except (IndexError, ValueError):
                schedule_id = 0
            idx, item, items = _get_schedule_item_by_id(group_id, schedule_id)
            if item is None or idx < 0:
                await update.effective_message.reply_text("未找到定时消息。")
                return True
            item["interval_sec"] = interval_sec
            item["next_at"] = int(time.time()) + interval_sec
            items[idx] = item
            save_schedule_items(group_id, items)
        new_state = _state_with(state, state=None, tmp={})
        _save_state(user_id, new_state)
        await show_rich_message_editor(update, context, new_state, target)
        return True
    if current_state == "x:lottery:publish":
        try:
            await publish_lottery(context, group_id, user_id, msg_text)
        except Exception as exc:
            await update.effective_message.reply_text(str(exc))
            return True
        new_state = _state_with(state, state=None, tmp={})
        _save_state(user_id, new_state)
        await show_lottery_menu(update, context, new_state)
        return True
    route = _VALUE_ROUTES.get(current_state)
    if not route:
        return False
    path, module_name, transform = route
    cfg = get_group_config(group_id)
    try:
        value = transform(msg_text)
    except Exception:
        await update.effective_message.reply_text("输入值无效。")
        return True
    _nested_set(cfg, path, value)
    save_group_config(group_id, cfg)
    new_state = _state_with(state, state=None, tmp={})
    _save_state(user_id, new_state)
    await _show_menu(module_name, update, context, new_state)
    return True


async def handle_admin_extra_callback(update, context, state: dict) -> bool:
    query = getattr(update, "callback_query", None)
    data = getattr(query, "data", "") or ""
    if not data.startswith("adminx:"):
        return False
    user_id = update.effective_user.id
    group_id = _group_id(state)
    parts = data.split(":")
    target_draft = _draft_target(user_id)
    if data.startswith("adminx:cancel_input:"):
        current_state = str((state or {}).get("state") or "")
        new_state = _state_with(state, state=None, tmp={})
        _save_state(user_id, new_state)
        if current_state.startswith("x:rich:"):
            await show_rich_message_editor(update, context, new_state, current_state.split(":", 3)[-1])
            return True
        if current_state.startswith("x:schedule:interval:"):
            await show_rich_message_editor(update, context, new_state, current_state.split("x:schedule:interval:", 1)[1])
            return True
        await _show_menu(data.rsplit(":", 1)[-1], update, context, new_state)
        return True
    if group_id and data == "adminx:invite:prompt:notify_text":
        await show_rich_message_editor(update, context, state, "invite_notify")
        return True
    if group_id and data == "adminx:related:prompt:occupy_comment_text":
        await show_rich_message_editor(update, context, state, "related_comment")
        return True
    if data in _PROMPT_ROUTES:
        next_state, module_name, prompt = _PROMPT_ROUTES[data]
        await _begin_input(update, user_id, state, next_state, module_name, prompt)
        return True
    if len(parts) >= 3 and parts[2] == "menu":
        await _show_menu(parts[1], update, context, state)
        return True
    if group_id and len(parts) >= 4 and parts[1] == "admin_access" and parts[2] == "set":
        mode = parts[3]
        if mode not in {"all_admins", "service_owner"}:
            await safe_answer(query, "未知模式。", show_alert=True)
            return True
        cfg = get_group_config(group_id)
        access_cfg = dict(cfg.get("admin_access", {}) or {})
        access_cfg["mode"] = mode
        cfg["admin_access"] = access_cfg
        save_group_config(group_id, cfg)
        await safe_answer(query, f"已切换为：{ADMIN_ACCESS_MODE_LABELS.get(mode, mode)}", show_alert=True)
        await show_admin_access_menu(update, context, state)
        return True
    if group_id and data == "adminx:nsfw:cycle:sensitivity":
        cfg = get_group_config(group_id)
        nsfw_cfg = dict(cfg.get("nsfw", {}) or {})
        options = ("low", "medium", "high")
        current = str(nsfw_cfg.get("sensitivity") or "medium").strip().lower()
        try:
            idx = options.index(current)
        except ValueError:
            idx = 1
        nsfw_cfg["sensitivity"] = options[(idx + 1) % len(options)]
        cfg["nsfw"] = nsfw_cfg
        save_group_config(group_id, cfg)
        await safe_answer(query, f"敏感度：{_sensitivity_label(nsfw_cfg['sensitivity'])}", show_alert=True)
        await show_nsfw_menu(update, context, state)
        return True
    if len(parts) >= 4 and parts[2] == "toggle" and group_id:
        path = {"ad": ("ad_filter", parts[3]), "cmd": ("command_gate", parts[3]), "crypto": ("crypto", parts[3]), "related": ("related_channel", parts[3]), "invite": ("invite_links", parts[3]), "member": ("member_watch", parts[3]), "fun": ("entertainment", parts[3]), "lang": ("language_whitelist", parts[3]), "nsfw": ("nsfw", parts[3]), "verified": ("verified_user", parts[3]), "lottery": ("lottery", parts[3])}.get(parts[1])
        if path:
            enabled = _toggle_config(group_id, path)
            await safe_answer(query, _toggle_notice(enabled), show_alert=True)
            await _show_menu(parts[1], update, context, state)
            return True
    if group_id and data == "adminx:schedule:add":
        items = load_schedule_items(group_id)
        limit = schedule_limit_for_group(group_id)
        if len(items) >= limit:
            await safe_answer(query, f"当前套餐最多支持 {limit} 条定时消息。", show_alert=True)
            await show_schedule_menu(update, context, state)
            return True
        SCHEDULE_DRAFTS[user_id] = _normalize_schedule_draft({"interval_sec": 3600})
        await show_rich_message_editor(update, context, state, target_draft)
        return True
    if group_id and data == "adminx:schedule:list":
        await show_schedule_list_menu(update, context, state)
        return True
    if group_id and data.startswith("adminx:schedule:edit:"):
        items = load_schedule_items(group_id)
        try:
            idx = int(data.split(":")[-1])
        except ValueError:
            idx = -1
        if not (0 <= idx < len(items)):
            await safe_answer(query, "未找到定时消息。", show_alert=True)
            return True
        await show_rich_message_editor(update, context, state, f"schedule:{int(items[idx].get('id') or 0)}")
        return True
    if group_id and len(parts) >= 4 and parts[1] == "schedule" and parts[2] in {"toggle", "delete"}:
        items = load_schedule_items(group_id)
        try:
            idx = int(parts[3])
        except ValueError:
            idx = -1
        if not (0 <= idx < len(items)):
            await safe_answer(query, "未找到定时消息。", show_alert=True)
            return True
        if parts[2] == "toggle":
            items[idx]["enabled"] = not items[idx].get("enabled", True)
            save_schedule_items(group_id, items)
            await safe_answer(query, _toggle_notice(bool(items[idx].get("enabled", True))), show_alert=True)
        else:
            del items[idx]
            save_schedule_items(group_id, items)
            await safe_answer(query, "已删除。", show_alert=True)
        await show_schedule_list_menu(update, context, state)
        return True
    if group_id and data.startswith("adminx:rich:schedule:toggle:"):
        try:
            schedule_id = int(data.split(":")[-1])
        except ValueError:
            schedule_id = 0
        idx, item, items = _get_schedule_item_by_id(group_id, schedule_id)
        if item is None or idx < 0:
            await safe_answer(query, "未找到定时消息。", show_alert=True)
            return True
        item["enabled"] = not item.get("enabled", True)
        items[idx] = item
        save_schedule_items(group_id, items)
        await safe_answer(query, _toggle_notice(bool(item.get("enabled", True))), show_alert=True)
        await show_rich_message_editor(update, context, state, f"schedule:{schedule_id}")
        return True
    if group_id and data.startswith("adminx:rich:schedule:delete:"):
        try:
            schedule_id = int(data.split(":")[-1])
        except ValueError:
            schedule_id = 0
        idx, item, items = _get_schedule_item_by_id(group_id, schedule_id)
        if item is None or idx < 0:
            await safe_answer(query, "未找到定时消息。", show_alert=True)
            return True
        items.pop(idx)
        save_schedule_items(group_id, items)
        await safe_answer(query, "已删除。", show_alert=True)
        await show_schedule_list_menu(update, context, state)
        return True
    if group_id and data.startswith("adminx:rich:"):
        action = parts[2] if len(parts) >= 3 else ""
        target = ":".join(parts[3:]) if len(parts) >= 4 else ""
        meta = _rich_target_meta(target)
        if action == "text" and meta:
            await _begin_input(update, user_id, state, f"x:rich:text:{target}", meta["prompt_module"], "请输入消息文本。")
            return True
        if action == "photo" and meta:
            await _begin_input(update, user_id, state, f"x:rich:photo:{target}", meta["prompt_module"], "请发送这条消息使用的图片。")
            return True
        if action == "clear_photo":
            payload = get_rich_message_target(group_id, target)
            if payload is None:
                await safe_answer(query, "未找到消息目标。", show_alert=True)
                return True
            payload["photo_file_id"] = ""
            save_rich_message_target(group_id, target, payload)
            await show_rich_message_editor(update, context, state, target)
            return True
        if action == "buttons":
            from . import admin as admin_module
            new_state = admin_module._state_with(state, state=admin_module.STATE_BTN_TEXT, tmp={"group_id": group_id, "btn_target": f"rich:{target}"})
            admin_module._save_state(user_id, new_state)
            await _send_or_edit(update, "请输入按钮文字。")
            return True
        if action == "interval" and meta:
            new_state = _state_with(state, state=f"x:schedule:interval:{target}", tmp={})
            _save_state(user_id, new_state)
            await _send_or_edit(update, "请输入间隔分钟数。", _prompt_markup(meta["prompt_module"]))
            return True
        if action == "save":
            draft_user_id = _parse_draft_target(target)
            if draft_user_id is None:
                return True
            items = load_schedule_items(group_id)
            limit = schedule_limit_for_group(group_id)
            if len(items) >= limit:
                await safe_answer(query, f"当前套餐最多支持 {limit} 条定时消息。", show_alert=True)
                await show_schedule_menu(update, context, state)
                return True
            draft = _normalize_schedule_draft(SCHEDULE_DRAFTS.get(draft_user_id))
            if not draft.get("text") and not draft.get("photo_file_id") and not draft.get("buttons"):
                await safe_answer(query, "定时消息至少需要包含文本、图片或按钮。", show_alert=True)
                await show_rich_message_editor(update, context, state, target)
                return True
            interval_sec = int(draft.get("interval_sec") or 3600)
            items.append({"text": draft.get("text") or "", "photo_file_id": draft.get("photo_file_id") or "", "buttons": draft.get("buttons") or [], "interval_sec": interval_sec, "next_at": int(time.time()) + interval_sec, "enabled": True})
            save_schedule_items(group_id, items)
            SCHEDULE_DRAFTS.pop(draft_user_id, None)
            new_state = _state_with(state, state=None, tmp={})
            _save_state(user_id, new_state)
            await safe_answer(query, "已保存。", show_alert=True)
            await show_schedule_list_menu(update, context, new_state)
            return True
    if data == "adminx:lottery:menu":
        await show_lottery_menu(update, context, state)
        return True
    if data == "adminx:lottery:publish":
        await _begin_input(update, user_id, state, "x:lottery:publish", "lottery", "请输入抽奖内容，格式：奖品标题 | 中奖人数")
        return True
    return False

