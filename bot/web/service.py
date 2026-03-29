from copy import deepcopy
from uuid import uuid4

from ..models.config import (
    DEFAULT_ANTI_SPAM,
    DEFAULT_AUTO_BAN,
    DEFAULT_AUTO_DELETE,
    DEFAULT_AUTO_MUTE,
    DEFAULT_AUTO_WARN,
    DEFAULT_GROUP_CONFIG,
)
from ..services.extra_features import (
    _activity_users_key,
    _invite_users_key,
    _nsfw_threshold,
    _points_users_key,
    get_active_gomoku_game,
    get_active_lottery,
    load_schedule_items,
    save_schedule_items,
)
from ..services.membership import auto_reply_limit_for_group, group_service_owner_id, has_active_membership, schedule_limit_for_group
from ..services.verify import get_verify_message
from ..services.verified_user import build_verified_user_message_payload, normalize_verified_members
from ..storage.config_store import (
    get_group_anti_spam,
    get_group_auto_ban,
    get_group_auto_delete,
    get_group_auto_mute,
    get_group_auto_replies,
    get_group_auto_warn,
    get_group_config,
    get_group_targets,
    save_group_anti_spam,
    save_group_auto_ban,
    save_group_auto_delete,
    save_group_auto_mute,
    save_group_auto_replies,
    save_group_auto_warn,
    save_group_config,
    save_group_targets,
)
from ..storage.kv import kv_get_json
from ..storage.session_store import get_verify_session, get_verify_session_users, get_welcome_queue
from ..utils.template import render_template
from .schemas import get_module, list_modules

VERIFY_MODES = ("join", "calc", "image_calc", "captcha")
ANTI_SPAM_TYPES = ("text", "photo", "video", "document", "voice", "sticker", "link")
ANTI_SPAM_ACTIONS = ("mute", "ban")
AD_FILTER_FIELDS = ("nickname_enabled", "sticker_enabled", "message_enabled", "block_channel_mask")
COMMAND_GATE_FIELDS = ("sign", "profile", "warn", "help", "config", "ban", "kick", "mute")
MEMBER_WATCH_FIELDS = ("nickname_change_detect", "nickname_change_notice")
RULE_MODES = ("contains", "exact", "regex")
AUTOWARN_ACTIONS = ("mute", "kick")
ADMIN_ACCESS_MODES = ("all_admins", "service_owner")
NSFW_SENSITIVITY_LEVELS = ("low", "medium", "high")
JSON_MODULE_DEFAULTS = {
    "autodelete": DEFAULT_AUTO_DELETE,
    "autoban": DEFAULT_AUTO_BAN,
    "automute": DEFAULT_AUTO_MUTE,
    "autowarn": DEFAULT_AUTO_WARN,
    "antispam": DEFAULT_ANTI_SPAM,
    "ad": DEFAULT_GROUP_CONFIG["ad_filter"],
    "cmd": DEFAULT_GROUP_CONFIG["command_gate"],
    "crypto": DEFAULT_GROUP_CONFIG["crypto"],
    "member": DEFAULT_GROUP_CONFIG["member_watch"],
    "schedule": DEFAULT_GROUP_CONFIG["schedule"],
    "points": DEFAULT_GROUP_CONFIG["points"],
    "activity": DEFAULT_GROUP_CONFIG["activity"],
    "fun": DEFAULT_GROUP_CONFIG["entertainment"],
    "usdt": DEFAULT_GROUP_CONFIG["usdt_price"],
    "related": DEFAULT_GROUP_CONFIG["related_channel"],
    "admin_access": DEFAULT_GROUP_CONFIG["admin_access"],
    "nsfw": DEFAULT_GROUP_CONFIG["nsfw"],
    "lang": DEFAULT_GROUP_CONFIG["language_whitelist"],
    "invite": DEFAULT_GROUP_CONFIG["invite_links"],
    "lottery": DEFAULT_GROUP_CONFIG["lottery"],
    "verified": DEFAULT_GROUP_CONFIG["verified_user"],
}
_DICT_LIST_FIELDS = {"buttons", "custom_rules", "items", "rules", "targets"}
_STRING_LIST_FIELDS = {"ad_sticker_ids", "allowed", "exchanges", "types"}


class _PreviewChat:
    def __init__(self, title: str):
        self.title = title or ""


class _PreviewUser:
    def __init__(self, full_name: str):
        self.full_name = full_name or ""

    def mention_html(self) -> str:
        return self.full_name or "成员"


def _yn(value: bool) -> str:
    return "开启" if value else "关闭"


def _verify_mode_label(mode: str) -> str:
    return {
        "join": "入群验证",
        "calc": "算术验证",
        "image_calc": "看图计算",
        "captcha": "验证码验证",
    }.get(str(mode or "join"), str(mode or "join"))


def _anti_spam_action_label(action: str) -> str:
    return {"mute": "禁言", "ban": "封禁"}.get(str(action or "mute"), str(action or "mute"))


def _gomoku_status_label(status: str) -> str:
    return {
        "playing": "进行中",
        "waiting": "等待加入",
        "idle": "空闲",
        "off": "关闭",
    }.get(str(status or "idle"), str(status or "idle"))


def _admin_access_mode_label(mode: str) -> str:
    return {
        "all_admins": "全部管理员",
        "service_owner": "服务主账号",
    }.get(str(mode or "all_admins"), str(mode or "all_admins"))


def _usdt_tier_label(tier: str) -> str:
    return {"best": "最优"}.get(str(tier or "best"), str(tier or "best"))


def _nsfw_sensitivity_label(level: str) -> str:
    return {"low": "低", "medium": "中", "high": "高"}.get(str(level or "medium"), str(level or "medium"))


def _safe_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default


def _require_object(value, label: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _normalize_buttons(buttons):
    result = []
    for item in list(buttons or []):
        if not isinstance(item, dict):
            continue
        result.append(
            {
                "text": str(item.get("text") or ""),
                "type": str(item.get("type") or "url"),
                "value": str(item.get("value") or ""),
                "row": max(0, _safe_int(item.get("row"), 0)),
            }
        )
    return result


def _normalize_rule_mode(value, default="contains"):
    mode = str(value or default).strip().lower()
    return mode if mode in RULE_MODES else default


def _normalize_keyword_rules(rules, *, with_duration: bool = False, default_duration: int = 0):
    result = []
    for rule in list(rules or []):
        if not isinstance(rule, dict):
            continue
        keyword = str(rule.get("keyword") or "").strip()
        if not keyword:
            continue
        item = {"id": str(rule.get("id") or uuid4().hex[:12]), "keyword": keyword, "mode": _normalize_rule_mode(rule.get("mode"))}
        if with_duration:
            item["duration_sec"] = max(0, _safe_int(rule.get("duration_sec"), default_duration))
        result.append(item)
    return result


def _normalize_language_code_ui(value: str) -> str:
    text = str(value or "").strip().lower()
    if text.startswith("zh"):
        return "zh"
    if text.startswith("en"):
        return "en"
    return text


def _normalize_string_list(values, *, normalize=None):
    result = []
    seen = set()
    for value in list(values or []):
        item = str(value or "").strip()
        if normalize is not None:
            item = normalize(item)
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _normalize_access_mode(value) -> str:
    mode = str(value or DEFAULT_GROUP_CONFIG["admin_access"]["mode"]).strip()
    return mode if mode in ADMIN_ACCESS_MODES else DEFAULT_GROUP_CONFIG["admin_access"]["mode"]


def _normalize_nsfw_sensitivity(value) -> str:
    level = str(value or DEFAULT_GROUP_CONFIG["nsfw"]["sensitivity"]).strip().lower()
    return level if level in NSFW_SENSITIVITY_LEVELS else DEFAULT_GROUP_CONFIG["nsfw"]["sensitivity"]


def _normalize_typed_value(defaults, value, path=()):
    if isinstance(defaults, dict):
        if not isinstance(value, dict):
            return deepcopy(defaults)
        result = deepcopy(defaults)
        for key, item in value.items():
            if key in defaults:
                result[key] = _normalize_typed_value(defaults[key], item, path + (key,))
            else:
                result[key] = deepcopy(item)
        return result
    if isinstance(defaults, list):
        if not isinstance(value, list):
            return deepcopy(defaults)
        field = path[-1] if path else ""
        if field in _DICT_LIST_FIELDS:
            return [deepcopy(item) for item in value if isinstance(item, dict)]
        if field in _STRING_LIST_FIELDS:
            return [str(item) for item in value if item is not None and str(item).strip()]
        return deepcopy(value)
    if isinstance(defaults, bool):
        return bool(value)
    if isinstance(defaults, int):
        return _safe_int(value, defaults)
    if isinstance(defaults, str):
        return str(defaults if value is None else value)
    return deepcopy(defaults if value is None else value)


def _normalize_json_module_data(key: str, data) -> dict:
    payload = _require_object(data, f"{key}.data")
    if key == "schedule":
        config_data = payload.get("config")
        if config_data is not None and not isinstance(config_data, dict):
            raise ValueError("schedule.data.config must be a JSON object")
        items_data = payload.get("items")
        if items_data is not None and not isinstance(items_data, list):
            raise ValueError("schedule.data.items must be a JSON array")
        return {
            "config": _normalize_typed_value(JSON_MODULE_DEFAULTS[key], config_data or {}),
            "items": [deepcopy(item) for item in list(items_data or []) if isinstance(item, dict)],
        }
    return _normalize_typed_value(JSON_MODULE_DEFAULTS[key], payload)


def _is_admin_status(member) -> bool:
    status = str(getattr(member, "status", "") or "").lower()
    return status in {"administrator", "creator", "owner"}


async def _bot_can_manage_group(bot, group_id: int) -> bool:
    try:
        me = await bot.get_me()
        member = await bot.get_chat_member(group_id, me.id)
    except Exception:
        return False
    return _is_admin_status(member)


def _active_verify_session_count(group_id: int) -> int:
    count = 0
    for user_id in get_verify_session_users(group_id):
        if get_verify_session(group_id, user_id):
            count += 1
    return count


async def build_module_runtime(bot, group_id: int, key: str) -> dict:
    cfg = get_group_config(group_id)
    runtime = {
        "group_id": int(group_id),
        "group_title": cfg.get("group_title") or str(group_id),
    }
    if key == "verify":
        targets = list(get_group_targets(group_id) or [])
        checkable_target_count = sum(1 for item in targets if item.get("checkable", True) and item.get("chat_id"))
        uncheckable_target_count = max(0, len(targets) - checkable_target_count)
        warnings = []
        if not targets:
            warnings.append("no_targets")
        if targets and checkable_target_count == 0:
            warnings.append("non_checkable_only")
        runtime.update(
            {
                "bot_can_manage_group": await _bot_can_manage_group(bot, group_id),
                "delivery_mode": "private" if cfg.get("verify_private") else "group",
                "pending_verify_users": _active_verify_session_count(group_id),
                "target_count": len(targets),
                "checkable_target_count": checkable_target_count,
                "uncheckable_target_count": uncheckable_target_count,
                "warnings": warnings,
            }
        )
        return runtime
    if key == "welcome":
        queue = list(get_welcome_queue(group_id) or [])
        ttl_queued_messages = sum(1 for item in queue if int(item.get("delete_at", 0) or 0) > 0)
        runtime.update(
            {
                "bot_can_manage_group": await _bot_can_manage_group(bot, group_id),
                "queued_welcome_messages": len(queue),
                "ttl_queued_messages": ttl_queued_messages,
                "delivery_mode": "group",
                "warnings": [],
            }
        )
        return runtime
    if key == "admin_access":
        access_cfg = cfg.get("admin_access", {}) or {}
        owner_id = int(group_service_owner_id(group_id) or 0)
        runtime.update(
            {
                "mode": str(access_cfg.get("mode") or "all_admins"),
                "service_owner_id": owner_id or None,
                "service_owner_bound": bool(owner_id),
                "service_owner_active_membership": has_active_membership(owner_id) if owner_id else False,
            }
        )
        return runtime
    if key == "nsfw":
        nsfw_cfg = cfg.get("nsfw", {}) or {}
        runtime.update(
            {
                "enabled": bool(nsfw_cfg.get("enabled")),
                "sensitivity": str(nsfw_cfg.get("sensitivity") or "medium"),
                "allow_miss": bool(nsfw_cfg.get("allow_miss")),
                "effective_threshold": _nsfw_threshold(nsfw_cfg),
                "heuristic_mode": "keyword_score",
            }
        )
        return runtime
    if key == "schedule":
        items = list(load_schedule_items(group_id) or [])
        enabled_items = [item for item in items if item.get("enabled", True)]
        next_run_at = min((int(item.get("next_at", 0) or 0) for item in enabled_items if int(item.get("next_at", 0) or 0) > 0), default=0)
        runtime.update(
            {
                "module_enabled": bool((cfg.get("schedule", {}) or {}).get("enabled", True)),
                "item_count": len(items),
                "enabled_item_count": len(enabled_items),
                "item_limit": schedule_limit_for_group(group_id),
                "next_run_at": next_run_at or None,
            }
        )
        return runtime
    if key == "related":
        related_cfg = cfg.get("related_channel", {}) or {}
        buttons = related_cfg.get("occupy_comment_buttons") or []
        runtime.update(
            {
                "cancel_top_pin": bool(related_cfg.get("cancel_top_pin")),
                "occupy_comment": bool(related_cfg.get("occupy_comment")),
                "comment_text_set": bool(str(related_cfg.get("occupy_comment_text") or "").strip()),
                "comment_photo_set": bool(str(related_cfg.get("occupy_comment_photo_file_id") or "").strip()),
                "comment_button_count": len(list(buttons or [])),
            }
        )
        return runtime
    if key == "lang":
        lang_cfg = cfg.get("language_whitelist", {}) or {}
        allowed = [str(item).strip() for item in (lang_cfg.get("allowed") or []) if str(item).strip()]
        runtime.update(
            {
                "enabled": bool(lang_cfg.get("enabled")),
                "allowed_count": len(allowed),
                "allowed_languages": allowed,
            }
        )
        return runtime
    if key == "points":
        points_cfg = cfg.get("points", {}) or {}
        tracked_users = list(kv_get_json(_points_users_key(group_id), []) or [])
        runtime.update(
            {
                "enabled": bool(points_cfg.get("enabled")),
                "chat_points_enabled": bool(points_cfg.get("chat_points_enabled")),
                "tracked_user_count": len(tracked_users),
                "sign_command": str(points_cfg.get("sign_command") or ""),
                "query_command": str(points_cfg.get("query_command") or ""),
                "rank_command": str(points_cfg.get("rank_command") or ""),
                "sign_points": _safe_int(points_cfg.get("sign_points"), 0),
                "chat_points_per_message": _safe_int(points_cfg.get("chat_points_per_message"), 0),
            }
        )
        return runtime
    if key == "activity":
        activity_cfg = cfg.get("activity", {}) or {}
        tracked_users = list(kv_get_json(_activity_users_key(group_id), []) or [])
        runtime.update(
            {
                "enabled": bool(activity_cfg.get("enabled", True)),
                "tracked_user_count": len(tracked_users),
                "today_command": str(activity_cfg.get("today_command") or ""),
                "month_command": str(activity_cfg.get("month_command") or ""),
                "total_command": str(activity_cfg.get("total_command") or ""),
            }
        )
        return runtime
    if key == "crypto":
        crypto_cfg = cfg.get("crypto", {}) or {}
        runtime.update(
            {
                "wallet_query_enabled": bool(crypto_cfg.get("wallet_query_enabled", True)),
                "price_query_enabled": bool(crypto_cfg.get("price_query_enabled", True)),
                "push_enabled": bool(crypto_cfg.get("push_enabled")),
                "default_symbol": str(crypto_cfg.get("default_symbol") or "BTC"),
                "query_alias": str(crypto_cfg.get("query_alias") or ""),
            }
        )
        return runtime
    if key == "invite":
        invite_cfg = cfg.get("invite_links", {}) or {}
        tracked_inviters = list(kv_get_json(_invite_users_key(group_id), []) or [])
        runtime.update(
            {
                "enabled": bool(invite_cfg.get("enabled")),
                "notify_enabled": bool(invite_cfg.get("notify_enabled")),
                "join_review": bool(invite_cfg.get("join_review")),
                "reward_points": _safe_int(invite_cfg.get("reward_points"), 0),
                "tracked_inviter_count": len(tracked_inviters),
                "notify_button_count": len(list(invite_cfg.get("notify_buttons") or [])),
            }
        )
        return runtime
    if key == "autodelete":
        delete_cfg = get_group_auto_delete(group_id)
        toggle_keys = [
            name
            for name, value in delete_cfg.items()
            if name.startswith("delete_") and isinstance(value, bool) and value
        ]
        runtime.update(
            {
                "bot_can_manage_group": await _bot_can_manage_group(bot, group_id),
                "active_filter_count": len(toggle_keys),
                "custom_rule_count": len(list(delete_cfg.get("custom_rules") or [])),
                "ad_sticker_count": len(list(delete_cfg.get("ad_sticker_ids") or [])),
                "exclude_admins": bool(delete_cfg.get("exclude_admins", True)),
                "long_length": _safe_int(delete_cfg.get("long_length"), 500),
            }
        )
        return runtime
    if key == "autoban":
        ban_cfg = get_group_auto_ban(group_id)
        rules = list(ban_cfg.get("rules") or [])
        runtime.update(
            {
                "bot_can_manage_group": await _bot_can_manage_group(bot, group_id),
                "enabled": bool(ban_cfg.get("enabled", True)),
                "rule_count": len(rules),
                "regex_rule_count": sum(1 for rule in rules if str(rule.get("mode") or "contains") == "regex"),
                "default_duration_sec": _safe_int(ban_cfg.get("default_duration_sec"), 86400),
            }
        )
        return runtime
    if key == "automute":
        mute_cfg = get_group_auto_mute(group_id)
        rules = list(mute_cfg.get("rules") or [])
        runtime.update(
            {
                "bot_can_manage_group": await _bot_can_manage_group(bot, group_id),
                "rule_count": len(rules),
                "regex_rule_count": sum(1 for rule in rules if str(rule.get("mode") or "contains") == "regex"),
                "default_duration_sec": _safe_int(mute_cfg.get("default_duration_sec"), 60),
            }
        )
        return runtime
    if key == "autowarn":
        warn_cfg = get_group_auto_warn(group_id)
        rules = list(warn_cfg.get("rules") or [])
        runtime.update(
            {
                "bot_can_manage_group": await _bot_can_manage_group(bot, group_id),
                "enabled": bool(warn_cfg.get("enabled", True)),
                "rule_count": len(rules),
                "warn_limit": _safe_int(warn_cfg.get("warn_limit"), 3),
                "action": str(warn_cfg.get("action") or "mute"),
                "cmd_mute_enabled": bool(warn_cfg.get("cmd_mute_enabled")),
                "mute_seconds": _safe_int(warn_cfg.get("mute_seconds"), 86400),
            }
        )
        return runtime
    if key == "antispam":
        spam_cfg = get_group_anti_spam(group_id)
        types = [str(item).strip() for item in (spam_cfg.get("types") or []) if str(item).strip()]
        runtime.update(
            {
                "bot_can_manage_group": await _bot_can_manage_group(bot, group_id),
                "enabled": bool(spam_cfg.get("enabled")),
                "action": str(spam_cfg.get("action") or "mute"),
                "window_sec": _safe_int(spam_cfg.get("window_sec"), 10),
                "threshold": _safe_int(spam_cfg.get("threshold"), 3),
                "type_count": len(types),
                "types": types,
            }
        )
        return runtime
    if key == "autoreply":
        rules = list(get_group_auto_replies(group_id) or [])
        enabled_rules = [rule for rule in rules if rule.get("enabled", True)]
        runtime.update(
            {
                "rule_count": len(rules),
                "enabled_rule_count": len(enabled_rules),
                "photo_rule_count": sum(1 for rule in enabled_rules if str(rule.get("photo_file_id") or "").strip()),
                "button_rule_count": sum(len(list(rule.get("buttons") or [])) for rule in enabled_rules),
                "rule_limit": auto_reply_limit_for_group(group_id),
            }
        )
        return runtime
    if key == "ad":
        ad_cfg = cfg.get("ad_filter", {}) or {}
        enabled_flags = [name for name, value in ad_cfg.items() if value is True]
        runtime.update(
            {
                "active_filter_count": len(enabled_flags),
                "nickname_enabled": bool(ad_cfg.get("nickname_enabled")),
                "sticker_enabled": bool(ad_cfg.get("sticker_enabled")),
                "message_enabled": bool(ad_cfg.get("message_enabled")),
                "block_channel_mask": bool(ad_cfg.get("block_channel_mask")),
            }
        )
        return runtime
    if key == "cmd":
        cmd_cfg = cfg.get("command_gate", {}) or {}
        blocked_commands = [name for name, value in cmd_cfg.items() if value]
        runtime.update(
            {
                "blocked_command_count": len(blocked_commands),
                "blocked_commands": blocked_commands,
            }
        )
        return runtime
    if key == "member":
        member_cfg = cfg.get("member_watch", {}) or {}
        runtime.update(
            {
                "nickname_change_detect": bool(member_cfg.get("nickname_change_detect")),
                "nickname_change_notice": bool(member_cfg.get("nickname_change_notice")),
            }
        )
        return runtime
    if key == "fun":
        fun_cfg = cfg.get("entertainment", {}) or {}
        active_game = get_active_gomoku_game(group_id)
        runtime.update(
            {
                "dice_enabled": bool(fun_cfg.get("dice_enabled", True)),
                "dice_cost": _safe_int(fun_cfg.get("dice_cost"), 0),
                "dice_command": str(fun_cfg.get("dice_command") or "/dice"),
                "gomoku_enabled": bool(fun_cfg.get("gomoku_enabled")),
                "gomoku_command": str(fun_cfg.get("gomoku_command") or "/gomoku"),
                "gomoku_active": bool(active_game),
                "gomoku_status": str((active_game or {}).get("status") or "idle"),
                "gomoku_player_count": sum(1 for user_id in [int((active_game or {}).get("creator_id") or 0), int((active_game or {}).get("challenger_id") or 0)] if user_id > 0),
            }
        )
        return runtime
    if key == "usdt":
        usdt_cfg = cfg.get("usdt_price", {}) or {}
        exchanges = [str(item).strip() for item in (usdt_cfg.get("exchanges") or []) if str(item).strip()]
        runtime.update(
            {
                "enabled": bool(usdt_cfg.get("enabled")),
                "tier": str(usdt_cfg.get("tier") or "best"),
                "show_query_message": bool(usdt_cfg.get("show_query_message", True)),
                "show_calc_message": bool(usdt_cfg.get("show_calc_message", True)),
                "exchange_count": len(exchanges),
                "exchanges": exchanges,
                "alias_z": str(usdt_cfg.get("alias_z") or ""),
                "alias_w": str(usdt_cfg.get("alias_w") or ""),
                "alias_k": str(usdt_cfg.get("alias_k") or ""),
            }
        )
        return runtime
    if key == "lottery":
        lottery_cfg = cfg.get("lottery", {}) or {}
        active_lottery = get_active_lottery(group_id)
        runtime.update(
            {
                "enabled": bool(lottery_cfg.get("enabled")),
                "query_command": str(lottery_cfg.get("query_command") or ""),
                "auto_delete_sec": _safe_int(lottery_cfg.get("auto_delete_sec"), 0),
                "pin_post": bool(lottery_cfg.get("pin_post", True)),
                "pin_result": bool(lottery_cfg.get("pin_result", True)),
                "active_lottery": bool(active_lottery),
                "active_participant_count": len(list((active_lottery or {}).get("participants") or [])),
                "active_winner_count": _safe_int((active_lottery or {}).get("winner_count"), 0),
            }
        )
        return runtime
    if key == "verified":
        verified_cfg = cfg.get("verified_user", {}) or {}
        members = normalize_verified_members(verified_cfg.get("members") or [])
        message = build_verified_user_message_payload(verified_cfg)
        runtime.update(
            {
                "enabled": bool(verified_cfg.get("enabled")),
                "member_count": len(members),
                "members": members,
                "reply_text_set": bool(message.get("text")),
                "reply_photo_set": bool(message.get("photo_file_id")),
                "reply_button_count": len(message.get("buttons") or []),
            }
        )
        return runtime
    return runtime


def _render_module_summary(group_id: int, key: str) -> str:
    cfg = get_group_config(group_id)
    if key == "verify":
        return f"{_yn(bool(cfg.get('verify_enabled')))} / {_verify_mode_label(cfg.get('verify_mode', 'join'))}"
    if key == "welcome":
        return f"{_yn(bool(cfg.get('welcome_enabled')))} / 保留 {cfg.get('welcome_ttl_sec', 0)}秒"
    if key == "autoreply":
        return f"{len(get_group_auto_replies(group_id))} 条规则"
    if key == "autodelete":
        return f"{len(get_group_auto_delete(group_id).get('custom_rules') or [])} 条规则"
    if key == "autoban":
        return f"{len(get_group_auto_ban(group_id).get('rules') or [])} 条规则"
    if key == "autowarn":
        warn_cfg = get_group_auto_warn(group_id)
        return f"{len(warn_cfg.get('rules') or [])} 条规则 / {warn_cfg.get('warn_limit', 3)}次"
    if key == "automute":
        mute_cfg = get_group_auto_mute(group_id)
        return f"{len(mute_cfg.get('rules') or [])} 条规则 / {mute_cfg.get('default_duration_sec', 60)}秒"
    if key == "antispam":
        spam_cfg = get_group_anti_spam(group_id)
        return f"{_yn(bool(spam_cfg.get('enabled')))} / 阈值 {spam_cfg.get('threshold', 3)}"
    if key == "ad":
        ad = cfg.get('ad_filter', {}) or {}
        on_count = sum(1 for value in ad.values() if value is True)
        return f"{on_count} 项开关"
    if key == "cmd":
        cmd = cfg.get('command_gate', {}) or {}
        on_count = sum(1 for value in cmd.values() if value)
        return f"{on_count} 条已禁用"
    if key == "crypto":
        crypto = cfg.get('crypto', {}) or {}
        return f"{crypto.get('default_symbol', 'BTC')} / {_yn(bool(crypto.get('push_enabled')))}"
    if key == "member":
        member = cfg.get('member_watch', {}) or {}
        on_count = sum(1 for value in member.values() if value)
        return f"{on_count} 项开关"
    if key == "schedule":
        return f"{len(load_schedule_items(group_id))} 条任务"
    if key == "points":
        points = cfg.get('points', {}) or {}
        return f"{_yn(bool(points.get('enabled')))} / {points.get('sign_command', '')}"
    if key == "activity":
        activity = cfg.get('activity', {}) or {}
        return activity.get('today_command', 'activity')
    if key == "fun":
        fun = cfg.get('entertainment', {}) or {}
        return f"骰子 {_yn(bool(fun.get('dice_enabled')))} / 五子棋 {_yn(bool(fun.get('gomoku_enabled')))}"
    if key == "usdt":
        usdt = cfg.get('usdt_price', {}) or {}
        return f"{_yn(bool(usdt.get('enabled')))} / {_usdt_tier_label(usdt.get('tier', 'best'))}"
    if key == "related":
        related = cfg.get('related_channel', {}) or {}
        on_count = sum(1 for value in related.values() if value is True)
        return f"{on_count} 项开关"
    if key == "admin_access":
        return _admin_access_mode_label((cfg.get('admin_access', {}) or {}).get('mode', 'all_admins'))
    if key == "nsfw":
        nsfw = cfg.get('nsfw', {}) or {}
        return f"{_yn(bool(nsfw.get('enabled')))} / {_nsfw_sensitivity_label(nsfw.get('sensitivity', 'medium'))}"
    if key == "lang":
        lang = cfg.get('language_whitelist', {}) or {}
        return f"{_yn(bool(lang.get('enabled')))} / {len(lang.get('allowed') or [])} 种语言"
    if key == "invite":
        invite = cfg.get('invite_links', {}) or {}
        return f"{_yn(bool(invite.get('enabled')))} / {invite.get('reward_points', 0)} 积分"
    if key == "lottery":
        lottery = cfg.get('lottery', {}) or {}
        return f"{_yn(bool(lottery.get('enabled')))} / {lottery.get('query_command', '')}"
    if key == "verified":
        verified = cfg.get("verified_user", {}) or {}
        members = normalize_verified_members(verified.get("members") or [])
        return f"{_yn(bool(verified.get('enabled')))} / {len(members)} 个账号"
    return "-"


def _module_runtime_preview(group_id: int, key: str, cfg: dict | None = None) -> list[str]:
    cfg = cfg or get_group_config(group_id)
    if key == "verify":
        return [
            f"{len(get_verify_session_users(group_id) or [])} 人待验证",
            "私聊发送" if cfg.get("verify_private") else "群内发送",
        ]
    if key == "welcome":
        queue = list(get_welcome_queue(group_id) or [])
        ttl_queued = sum(1 for item in queue if _safe_int(item.get("delete_at"), 0) > 0)
        return [f"{len(queue)} 条待发送", f"{ttl_queued} 条待清理"]
    if key == "autoreply":
        rules = list(get_group_auto_replies(group_id) or [])
        enabled_rules = [rule for rule in rules if rule.get("enabled", True)]
        return [f"{len(enabled_rules)}/{len(rules)} 已启用", f"上限 {auto_reply_limit_for_group(group_id)}"]
    if key == "autodelete":
        delete_cfg = get_group_auto_delete(group_id)
        active_filter_count = sum(1 for name, value in delete_cfg.items() if name.startswith("delete_") and value is True)
        return [f"{active_filter_count} 项过滤开启", f"{len(list(delete_cfg.get('custom_rules') or []))} 条自定义规则"]
    if key == "autoban":
        ban_cfg = get_group_auto_ban(group_id)
        return [f"{len(list(ban_cfg.get('rules') or []))} 条封禁规则", f"默认 {_safe_int(ban_cfg.get('default_duration_sec'), 86400)} 秒"]
    if key == "automute":
        mute_cfg = get_group_auto_mute(group_id)
        return [f"{len(list(mute_cfg.get('rules') or []))} 条禁言规则", f"默认 {_safe_int(mute_cfg.get('default_duration_sec'), 60)} 秒"]
    if key == "autowarn":
        warn_cfg = get_group_auto_warn(group_id)
        return [f"上限 {_safe_int(warn_cfg.get('warn_limit'), 3)}", f"{len(list(warn_cfg.get('rules') or []))} 条警告规则"]
    if key == "antispam":
        spam_cfg = get_group_anti_spam(group_id)
        types = [item for item in (spam_cfg.get("types") or []) if str(item).strip()]
        if not spam_cfg.get("enabled"):
            return ["已关闭", f"{len(types)} 类内容"]
        return [f"{_anti_spam_action_label(str(spam_cfg.get('action') or 'mute'))} / 阈值 {_safe_int(spam_cfg.get('threshold'), 3)}", f"{len(types)} 类内容"]
    if key == "ad":
        ad_cfg = cfg.get("ad_filter", {}) or {}
        enabled_filters = [name for name, value in ad_cfg.items() if value is True]
        return [f"{len(enabled_filters)} 项过滤开启"]
    if key == "cmd":
        gate_cfg = cfg.get("command_gate", {}) or {}
        blocked = [name for name, value in gate_cfg.items() if value]
        preview = ", ".join(blocked[:3]) if blocked else "无"
        return [f"{len(blocked)} 条已禁用", preview]
    if key == "crypto":
        crypto_cfg = cfg.get("crypto", {}) or {}
        return [f"默认币种 {str(crypto_cfg.get('default_symbol') or 'BTC')}", f"推送 {_yn(bool(crypto_cfg.get('push_enabled')))}"]
    if key == "member":
        member_cfg = cfg.get("member_watch", {}) or {}
        enabled_watchers = [name for name, value in member_cfg.items() if value is True]
        return [f"{len(enabled_watchers)} 项监听开启"]
    if key == "schedule":
        items = list(load_schedule_items(group_id) or [])
        enabled_items = [item for item in items if item.get("enabled", True)]
        return [f"{len(enabled_items)}/{len(items)} 启用中", f"上限 {schedule_limit_for_group(group_id)}"]
    if key == "points":
        points_cfg = cfg.get("points", {}) or {}
        tracked = len(list(kv_get_json(_points_users_key(group_id), []) or []))
        return [f"{tracked} 人已记录", f"聊天积分 {_yn(bool(points_cfg.get('chat_points_enabled')))}"]
    if key == "activity":
        activity_cfg = cfg.get("activity", {}) or {}
        tracked = len(list(kv_get_json(_activity_users_key(group_id), []) or []))
        return [f"{tracked} 人已记录", str(activity_cfg.get("today_command") or "activity")]
    if key == "fun":
        fun_cfg = cfg.get("entertainment", {}) or {}
        active_game = get_active_gomoku_game(group_id)
        gomoku_status = _gomoku_status_label(str((active_game or {}).get("status") or ("idle" if fun_cfg.get("gomoku_enabled") else "off")))
        return [f"骰子 {_yn(bool(fun_cfg.get('dice_enabled', True)))}", f"五子棋 {gomoku_status}"]
    if key == "usdt":
        usdt_cfg = cfg.get("usdt_price", {}) or {}
        exchanges = [str(item).strip() for item in (usdt_cfg.get("exchanges") or []) if str(item).strip()]
        return [f"档位 {_usdt_tier_label(str(usdt_cfg.get('tier') or 'best'))}", f"{len(exchanges)} 个交易所"]
    if key == "related":
        related_cfg = cfg.get("related_channel", {}) or {}
        return [f"占位评论 {_yn(bool(related_cfg.get('occupy_comment')))}", f"{len(list(related_cfg.get('occupy_comment_buttons') or []))} 个按钮"]
    if key == "admin_access":
        access_cfg = cfg.get("admin_access", {}) or {}
        mode = _admin_access_mode_label(str(access_cfg.get("mode") or "all_admins"))
        owner_id = int(group_service_owner_id(group_id) or 0)
        owner_state = "主账号已绑定" if owner_id and has_active_membership(owner_id) else "主账号未绑定"
        return [f"模式 {mode}", owner_state if str(access_cfg.get("mode") or "all_admins") == "service_owner" else "全部管理员"]
    if key == "nsfw":
        nsfw_cfg = cfg.get("nsfw", {}) or {}
        return [f"阈值 {_nsfw_threshold(nsfw_cfg)}", f"提醒 {_yn(bool(nsfw_cfg.get('notice_enabled', True)))}"]
    if key == "lang":
        lang_cfg = cfg.get("language_whitelist", {}) or {}
        allowed = [str(item).strip() for item in (lang_cfg.get("allowed") or []) if str(item).strip()]
        return [f"{len(allowed)} 种语言", f"白名单 {_yn(bool(lang_cfg.get('enabled')))}"]
    if key == "invite":
        invite_cfg = cfg.get("invite_links", {}) or {}
        tracked = len(list(kv_get_json(_invite_users_key(group_id), []) or []))
        return [f"{tracked} 人邀请记录", f"奖励 {_safe_int(invite_cfg.get('reward_points'), 0)} 积分"]
    if key == "lottery":
        lottery_cfg = cfg.get("lottery", {}) or {}
        active_lottery = get_active_lottery(group_id)
        if active_lottery:
            return ["抽奖进行中", f"{len(list(active_lottery.get('participants') or []))} 人参与"]
        return ["暂无进行中的抽奖", str(lottery_cfg.get("query_command") or "")]
    if key == "verified":
        verified_cfg = cfg.get("verified_user", {}) or {}
        members = normalize_verified_members(verified_cfg.get("members") or [])
        message = build_verified_user_message_payload(verified_cfg)
        if not bool(verified_cfg.get("enabled")):
            return ["已关闭"]
        preview = ["已启用"]
        if members:
            preview.append(f"{len(members)} 个已配置账号")
        elif len(message.get("buttons") or []):
            preview.append(f"{len(message.get('buttons') or [])} 个按钮")
        return preview
    return []


def _module_runtime_alert_details(group_id: int, key: str, cfg: dict | None = None) -> list[dict]:
    cfg = cfg or get_group_config(group_id)
    alerts: list[dict] = []
    if key == "verify" and bool(cfg.get("verify_enabled", True)):
        targets = list(get_group_targets(group_id) or [])
        if not targets:
            alerts.append({"severity": "error", "message": "未配置验证目标"})
        elif not any(item.get("checkable", True) and item.get("chat_id") for item in targets):
            alerts.append({"severity": "error", "message": "验证目标不可校验"})
        return alerts
    if key == "admin_access":
        access_cfg = cfg.get("admin_access", {}) or {}
        if str(access_cfg.get("mode") or "all_admins") != "service_owner":
            return alerts
        owner_id = int(group_service_owner_id(group_id) or 0)
        if owner_id <= 0:
            alerts.append({"severity": "warning", "message": "未绑定服务主账号"})
        elif not has_active_membership(owner_id):
            alerts.append({"severity": "warning", "message": "服务主账号会员已过期"})
        return alerts
    if key == "schedule":
        items = list(load_schedule_items(group_id) or [])
        enabled_items = [item for item in items if item.get("enabled", True)]
        limit = max(0, schedule_limit_for_group(group_id))
        if limit and len(items) >= limit:
            alerts.append({"severity": "warning", "message": "计划任务数量已达上限"})
        elif limit and len(items) >= max(1, limit - 1):
            alerts.append({"severity": "info", "message": "计划任务数量接近上限"})
        if items and not enabled_items:
            alerts.append({"severity": "info", "message": "计划任务均已关闭"})
        return alerts
    if key == "verified":
        verified_cfg = cfg.get("verified_user", {}) or {}
        if not bool(verified_cfg.get("enabled")):
            return alerts
        members = normalize_verified_members(verified_cfg.get("members") or [])
        message = build_verified_user_message_payload(verified_cfg)
        if not members:
            alerts.append({"severity": "warning", "message": "未配置认证账号"})
        if not message.get("text") and not message.get("photo_file_id") and not message.get("buttons"):
            alerts.append({"severity": "warning", "message": "认证回复消息为空"})
        return alerts
    return alerts


def _module_runtime_alerts(group_id: int, key: str, cfg: dict | None = None) -> list[str]:
    return [str(item.get("message") or "").strip() for item in _module_runtime_alert_details(group_id, key, cfg) if str(item.get("message") or "").strip()]


def _lightweight_module_summary(cfg: dict, item: dict) -> str:
    key = str(item.get("key") or "")
    if key == "verify":
        return f"{_yn(bool(cfg.get('verify_enabled')))} / {_verify_mode_label(cfg.get('verify_mode', 'join'))}"
    if key == "welcome":
        return f"{_yn(bool(cfg.get('welcome_enabled')))} / 保留 {cfg.get('welcome_ttl_sec', 0)}秒"
    if key == "crypto":
        crypto = cfg.get("crypto", {}) or {}
        return f"{crypto.get('default_symbol', 'BTC')} / {_yn(bool(crypto.get('push_enabled')))}"
    if key == "ad":
        ad = cfg.get("ad_filter", {}) or {}
        return f"{sum(1 for value in ad.values() if value is True)} 项开关"
    if key == "cmd":
        gate = cfg.get("command_gate", {}) or {}
        return f"{sum(1 for value in gate.values() if value)} 条已禁用"
    if key == "member":
        member = cfg.get("member_watch", {}) or {}
        return f"{sum(1 for value in member.values() if value)} 项开关"
    if key == "points":
        points = cfg.get("points", {}) or {}
        return f"{_yn(bool(points.get('enabled')))} / {points.get('sign_command', '')}"
    if key == "activity":
        activity = cfg.get("activity", {}) or {}
        return str(activity.get("today_command") or "activity")
    if key == "fun":
        fun = cfg.get("entertainment", {}) or {}
        return f"骰子 {_yn(bool(fun.get('dice_enabled', True)))} / 五子棋 {_yn(bool(fun.get('gomoku_enabled')))}"
    if key == "usdt":
        usdt = cfg.get("usdt_price", {}) or {}
        return f"{_yn(bool(usdt.get('enabled')))} / {_usdt_tier_label(usdt.get('tier', 'best'))}"
    if key == "related":
        related = cfg.get("related_channel", {}) or {}
        return f"{sum(1 for value in related.values() if value is True)} 项开关"
    if key == "admin_access":
        return _admin_access_mode_label((cfg.get("admin_access", {}) or {}).get("mode", "all_admins"))
    if key == "nsfw":
        nsfw = cfg.get("nsfw", {}) or {}
        return f"{_yn(bool(nsfw.get('enabled')))} / {_nsfw_sensitivity_label(nsfw.get('sensitivity', 'medium'))}"
    if key == "lang":
        lang = cfg.get("language_whitelist", {}) or {}
        return f"{_yn(bool(lang.get('enabled')))} / {len(lang.get('allowed') or [])} 种语言"
    if key == "invite":
        invite = cfg.get("invite_links", {}) or {}
        return f"{_yn(bool(invite.get('enabled')))} / {invite.get('reward_points', 0)} 积分"
    if key == "lottery":
        lottery = cfg.get("lottery", {}) or {}
        return f"{_yn(bool(lottery.get('enabled')))} / {lottery.get('query_command', '')}"
    if key == "verified":
        verified = cfg.get("verified_user", {}) or {}
        return _yn(bool(verified.get("enabled")))
    return "-"


def build_group_summary(group_id: int, *, include_runtime: bool = True) -> dict:
    cfg = get_group_config(group_id)
    modules = []
    for item in list_modules():
        runtime_preview = _module_runtime_preview(group_id, item["key"], cfg) if include_runtime else []
        runtime_alert_details = _module_runtime_alert_details(group_id, item["key"], cfg) if include_runtime else []
        modules.append(
            {
                **item,
                "summary": _render_module_summary(group_id, item["key"]) if include_runtime else _lightweight_module_summary(cfg, item),
                "runtime_preview": runtime_preview,
                "runtime_alerts": [str(entry.get("message") or "").strip() for entry in runtime_alert_details if str(entry.get("message") or "").strip()],
                "runtime_alert_details": runtime_alert_details,
            }
        )
    return {
        "group_id": int(group_id),
        "group_title": cfg.get("group_title") or str(group_id),
        "modules": modules,
    }


def _raw_module_data(group_id: int, key: str):
    cfg = get_group_config(group_id)
    if key == "autodelete":
        return get_group_auto_delete(group_id)
    if key == "autoban":
        return get_group_auto_ban(group_id)
    if key == "automute":
        return get_group_auto_mute(group_id)
    if key == "autowarn":
        return get_group_auto_warn(group_id)
    if key == "antispam":
        return get_group_anti_spam(group_id)
    if key == "ad":
        return deepcopy(cfg.get("ad_filter", {}) or {})
    if key == "cmd":
        return deepcopy(cfg.get("command_gate", {}) or {})
    if key == "crypto":
        return deepcopy(cfg.get("crypto", {}) or {})
    if key == "member":
        return deepcopy(cfg.get("member_watch", {}) or {})
    if key == "schedule":
        return {"config": deepcopy(cfg.get("schedule", {}) or {}), "items": load_schedule_items(group_id)}
    if key == "points":
        return deepcopy(cfg.get("points", {}) or {})
    if key == "activity":
        return deepcopy(cfg.get("activity", {}) or {})
    if key == "fun":
        return deepcopy(cfg.get("entertainment", {}) or {})
    if key == "usdt":
        return deepcopy(cfg.get("usdt_price", {}) or {})
    if key == "related":
        return deepcopy(cfg.get("related_channel", {}) or {})
    if key == "admin_access":
        return deepcopy(cfg.get("admin_access", {}) or {})
    if key == "nsfw":
        return deepcopy(cfg.get("nsfw", {}) or {})
    if key == "lang":
        return deepcopy(cfg.get("language_whitelist", {}) or {})
    if key == "invite":
        return deepcopy(cfg.get("invite_links", {}) or {})
    if key == "lottery":
        return deepcopy(cfg.get("lottery", {}) or {})
    if key == "verified":
        return deepcopy(cfg.get("verified_user", {}) or {})
    return {}


def load_module_payload(group_id: int, key: str) -> dict:
    meta = get_module(key)
    if not meta:
        raise KeyError(key)
    cfg = get_group_config(group_id)
    if key == "welcome":
        data = {
            "enabled": bool(cfg.get("welcome_enabled")),
            "text": str(cfg.get("welcome_text") or ""),
            "photo_file_id": str(cfg.get("welcome_photo_file_id") or ""),
            "buttons": _normalize_buttons(cfg.get("welcome_buttons") or []),
            "ttl_sec": _safe_int(cfg.get("welcome_ttl_sec"), 0),
            "delete_prev": bool(cfg.get("welcome_delete_prev")),
        }
        return {"module": meta, "supported": True, "editor": "welcome", "data": data}
    if key == "verify":
        messages = {}
        for mode in VERIFY_MODES:
            item = get_verify_message(cfg, mode)
            messages[mode] = {
                "text": str(item.get("text") or ""),
                "photo_file_id": str(item.get("photo_file_id") or ""),
                "buttons": _normalize_buttons(item.get("buttons") or []),
            }
        data = {
            "enabled": bool(cfg.get("verify_enabled")),
            "mode": str(cfg.get("verify_mode") or "join"),
            "timeout_sec": _safe_int(cfg.get("verify_timeout_sec"), 60),
            "max_attempts": _safe_int(cfg.get("verify_max_attempts"), 3),
            "fail_action": str(cfg.get("verify_fail_action") or "mute"),
            "private_enabled": bool(cfg.get("verify_private")),
            "fail_text": str(cfg.get("verify_fail_text") or ""),
            "targets": deepcopy(get_group_targets(group_id)),
            "messages": messages,
        }
        return {"module": meta, "supported": True, "editor": "verify", "data": data}
    if key == "autoreply":
        rules = []
        for rule in get_group_auto_replies(group_id):
            rules.append(
                {
                    "keyword": str(rule.get("keyword") or ""),
                    "mode": str(rule.get("mode") or "contains"),
                    "enabled": bool(rule.get("enabled", True)),
                    "reply_text": str(rule.get("reply_text") or ""),
                    "photo_file_id": str(rule.get("photo_file_id") or ""),
                    "buttons": _normalize_buttons(rule.get("buttons") or []),
                }
            )
        return {"module": meta, "supported": True, "editor": "autoreply", "data": {"rules": rules}}
    if key == "autodelete":
        data = get_group_auto_delete(group_id)
        data = {
            "delete_system": bool(data.get("delete_system", True)),
            "delete_channel_mask": bool(data.get("delete_channel_mask", False)),
            "delete_links": bool(data.get("delete_links", True)),
            "delete_long": bool(data.get("delete_long", True)),
            "long_length": max(1, _safe_int(data.get("long_length"), DEFAULT_AUTO_DELETE["long_length"])),
            "delete_videos": bool(data.get("delete_videos", True)),
            "delete_stickers": bool(data.get("delete_stickers", False)),
            "delete_forwarded": bool(data.get("delete_forwarded", False)),
            "delete_ad_stickers": bool(data.get("delete_ad_stickers", False)),
            "delete_archives": bool(data.get("delete_archives", False)),
            "delete_executables": bool(data.get("delete_executables", False)),
            "delete_notice_text": bool(data.get("delete_notice_text", True)),
            "delete_documents": bool(data.get("delete_documents", True)),
            "delete_mentions": bool(data.get("delete_mentions", True)),
            "delete_other_commands": bool(data.get("delete_other_commands", False)),
            "delete_qr": bool(data.get("delete_qr", True)),
            "delete_edited": bool(data.get("delete_edited", False)),
            "delete_member_emoji": bool(data.get("delete_member_emoji", False)),
            "delete_member_emoji_only": bool(data.get("delete_member_emoji_only", False)),
            "delete_external_reply": bool(data.get("delete_external_reply", True)),
            "delete_shared_contact": bool(data.get("delete_shared_contact", True)),
            "exclude_admins": bool(data.get("exclude_admins", True)),
            "custom_rules": [
                {
                    "keyword": str(rule.get("keyword") or ""),
                    "mode": str(rule.get("mode") or "contains"),
                }
                for rule in list(data.get("custom_rules") or [])
                if isinstance(rule, dict)
            ],
            "ad_sticker_ids": [str(item) for item in list(data.get("ad_sticker_ids") or []) if str(item).strip()],
        }
        return {"module": meta, "supported": True, "editor": "autodelete", "data": data}
    if key == "autoban":
        ban_cfg = _normalize_typed_value(DEFAULT_AUTO_BAN, get_group_auto_ban(group_id) or {})
        default_duration = max(0, _safe_int(ban_cfg.get("default_duration_sec"), DEFAULT_AUTO_BAN["default_duration_sec"]))
        data = {"enabled": bool(ban_cfg.get("enabled", True)), "default_duration_sec": default_duration, "rules": _normalize_keyword_rules(ban_cfg.get("rules") or [], with_duration=True, default_duration=default_duration)}
        return {"module": meta, "supported": True, "editor": "autoban", "data": data}
    if key == "automute":
        mute_cfg = _normalize_typed_value(DEFAULT_AUTO_MUTE, get_group_auto_mute(group_id) or {})
        default_duration = max(1, _safe_int(mute_cfg.get("default_duration_sec"), DEFAULT_AUTO_MUTE["default_duration_sec"]))
        data = {"default_duration_sec": default_duration, "rules": _normalize_keyword_rules(mute_cfg.get("rules") or [], with_duration=True, default_duration=default_duration)}
        return {"module": meta, "supported": True, "editor": "automute", "data": data}
    if key == "autowarn":
        warn_cfg = _normalize_typed_value(DEFAULT_AUTO_WARN, get_group_auto_warn(group_id) or {})
        action = str(warn_cfg.get("action") or DEFAULT_AUTO_WARN["action"]).strip().lower()
        if action not in AUTOWARN_ACTIONS:
            action = DEFAULT_AUTO_WARN["action"]
        data = {"enabled": bool(warn_cfg.get("enabled", True)), "warn_limit": max(1, _safe_int(warn_cfg.get("warn_limit"), DEFAULT_AUTO_WARN["warn_limit"])), "mute_seconds": max(1, _safe_int(warn_cfg.get("mute_seconds"), DEFAULT_AUTO_WARN["mute_seconds"])), "action": action, "cmd_mute_enabled": bool(warn_cfg.get("cmd_mute_enabled", False)), "warn_text": str(warn_cfg.get("warn_text") or ""), "rules": _normalize_keyword_rules(warn_cfg.get("rules") or [])}
        return {"module": meta, "supported": True, "editor": "autowarn", "data": data}
    if key == "schedule":
        schedule_cfg = _normalize_typed_value(DEFAULT_GROUP_CONFIG["schedule"], cfg.get("schedule", {}) or {})
        items = []
        for item in load_schedule_items(group_id):
            items.append(
                {
                    "id": _safe_int(item.get("id"), 0),
                    "text": str(item.get("text") or ""),
                    "photo_file_id": str(item.get("photo_file_id") or ""),
                    "buttons": _normalize_buttons(item.get("buttons") or []),
                    "interval_sec": max(60, _safe_int(item.get("interval_sec"), 3600)),
                    "next_at": max(0, _safe_int(item.get("next_at"), 0)),
                    "enabled": bool(item.get("enabled", True)),
                }
            )
        return {
            "module": meta,
            "supported": True,
            "editor": "schedule",
            "data": {
                "enabled": bool(schedule_cfg.get("enabled", True)),
                "items": items,
            },
        }
    if key == "ad":
        ad_cfg = _normalize_typed_value(DEFAULT_GROUP_CONFIG["ad_filter"], cfg.get("ad_filter", {}) or {})
        data = {field: bool(ad_cfg.get(field, False)) for field in AD_FILTER_FIELDS}
        return {"module": meta, "supported": True, "editor": "ad", "data": data}
    if key == "cmd":
        cmd_cfg = _normalize_typed_value(DEFAULT_GROUP_CONFIG["command_gate"], cfg.get("command_gate", {}) or {})
        data = {field: bool(cmd_cfg.get(field, False)) for field in COMMAND_GATE_FIELDS}
        return {"module": meta, "supported": True, "editor": "cmd", "data": data}
    if key == "member":
        member_cfg = _normalize_typed_value(DEFAULT_GROUP_CONFIG["member_watch"], cfg.get("member_watch", {}) or {})
        data = {field: bool(member_cfg.get(field, False)) for field in MEMBER_WATCH_FIELDS}
        return {"module": meta, "supported": True, "editor": "member", "data": data}
    if key == "antispam":
        spam_cfg = _normalize_typed_value(DEFAULT_ANTI_SPAM, get_group_anti_spam(group_id) or {})
        action = str(spam_cfg.get("action") or DEFAULT_ANTI_SPAM["action"]).strip().lower()
        if action not in ANTI_SPAM_ACTIONS:
            action = DEFAULT_ANTI_SPAM["action"]
        data = {
            "enabled": bool(spam_cfg.get("enabled", False)),
            "action": action,
            "mute_seconds": max(0, _safe_int(spam_cfg.get("mute_seconds"), DEFAULT_ANTI_SPAM["mute_seconds"])),
            "window_sec": max(1, _safe_int(spam_cfg.get("window_sec"), DEFAULT_ANTI_SPAM["window_sec"])),
            "threshold": max(1, _safe_int(spam_cfg.get("threshold"), DEFAULT_ANTI_SPAM["threshold"])),
            "types": [item for item in list(spam_cfg.get("types") or []) if item in ANTI_SPAM_TYPES],
        }
        return {"module": meta, "supported": True, "editor": "antispam", "data": data}
    if key == "crypto":
        crypto_cfg = _normalize_typed_value(DEFAULT_GROUP_CONFIG["crypto"], cfg.get("crypto", {}) or {})
        data = {
            "wallet_query_enabled": bool(crypto_cfg.get("wallet_query_enabled", True)),
            "price_query_enabled": bool(crypto_cfg.get("price_query_enabled", True)),
            "push_enabled": bool(crypto_cfg.get("push_enabled", False)),
            "default_symbol": str(crypto_cfg.get("default_symbol") or DEFAULT_GROUP_CONFIG["crypto"]["default_symbol"]),
            "query_alias": str(crypto_cfg.get("query_alias") or DEFAULT_GROUP_CONFIG["crypto"]["query_alias"]),
        }
        return {"module": meta, "supported": True, "editor": "crypto", "data": data}
    if key == "fun":
        fun_cfg = _normalize_typed_value(DEFAULT_GROUP_CONFIG["entertainment"], cfg.get("entertainment", {}) or {})
        data = {
            "dice_enabled": bool(fun_cfg.get("dice_enabled", True)),
            "dice_cost": max(0, _safe_int(fun_cfg.get("dice_cost"), DEFAULT_GROUP_CONFIG["entertainment"]["dice_cost"])),
            "dice_command": str(fun_cfg.get("dice_command") or DEFAULT_GROUP_CONFIG["entertainment"]["dice_command"]),
            "gomoku_enabled": bool(fun_cfg.get("gomoku_enabled", False)),
            "gomoku_command": str(fun_cfg.get("gomoku_command") or DEFAULT_GROUP_CONFIG["entertainment"]["gomoku_command"]),
        }
        return {"module": meta, "supported": True, "editor": "fun", "data": data}
    if key == "lottery":
        lottery_cfg = _normalize_typed_value(DEFAULT_GROUP_CONFIG["lottery"], cfg.get("lottery", {}) or {})
        data = {
            "enabled": bool(lottery_cfg.get("enabled", False)),
            "query_command": str(lottery_cfg.get("query_command") or DEFAULT_GROUP_CONFIG["lottery"]["query_command"]),
            "auto_delete_sec": max(0, _safe_int(lottery_cfg.get("auto_delete_sec"), DEFAULT_GROUP_CONFIG["lottery"]["auto_delete_sec"])),
            "pin_post": bool(lottery_cfg.get("pin_post", True)),
            "pin_result": bool(lottery_cfg.get("pin_result", True)),
        }
        return {"module": meta, "supported": True, "editor": "lottery", "data": data}
    if key == "related":
        related_cfg = _normalize_typed_value(DEFAULT_GROUP_CONFIG["related_channel"], cfg.get("related_channel", {}) or {})
        data = {
            "cancel_top_pin": bool(related_cfg.get("cancel_top_pin", False)),
            "occupy_comment": bool(related_cfg.get("occupy_comment", False)),
            "comment_message": {
                "text": str(related_cfg.get("occupy_comment_text") or ""),
                "photo_file_id": str(related_cfg.get("occupy_comment_photo_file_id") or ""),
                "buttons": _normalize_buttons(related_cfg.get("occupy_comment_buttons") or []),
            },
        }
        return {"module": meta, "supported": True, "editor": "related", "data": data}
    if key == "admin_access":
        access_cfg = _normalize_typed_value(DEFAULT_GROUP_CONFIG["admin_access"], cfg.get("admin_access", {}) or {})
        data = {"mode": _normalize_access_mode(access_cfg.get("mode"))}
        return {"module": meta, "supported": True, "editor": "admin_access", "data": data}
    if key == "nsfw":
        nsfw_cfg = _normalize_typed_value(DEFAULT_GROUP_CONFIG["nsfw"], cfg.get("nsfw", {}) or {})
        data = {
            "enabled": bool(nsfw_cfg.get("enabled", False)),
            "sensitivity": _normalize_nsfw_sensitivity(nsfw_cfg.get("sensitivity")),
            "allow_miss": bool(nsfw_cfg.get("allow_miss", False)),
            "notice_enabled": bool(nsfw_cfg.get("notice_enabled", True)),
            "delay_delete_sec": max(0, _safe_int(nsfw_cfg.get("delay_delete_sec"), DEFAULT_GROUP_CONFIG["nsfw"]["delay_delete_sec"])),
        }
        return {"module": meta, "supported": True, "editor": "nsfw", "data": data}
    if key == "lang":
        lang_cfg = _normalize_typed_value(DEFAULT_GROUP_CONFIG["language_whitelist"], cfg.get("language_whitelist", {}) or {})
        data = {
            "enabled": bool(lang_cfg.get("enabled", False)),
            "allowed": _normalize_string_list(lang_cfg.get("allowed") or [], normalize=_normalize_language_code_ui),
        }
        return {"module": meta, "supported": True, "editor": "lang", "data": data}
    if key == "invite":
        invite_cfg = _normalize_typed_value(DEFAULT_GROUP_CONFIG["invite_links"], cfg.get("invite_links", {}) or {})
        data = {
            "enabled": bool(invite_cfg.get("enabled", False)),
            "notify_enabled": bool(invite_cfg.get("notify_enabled", False)),
            "join_review": bool(invite_cfg.get("join_review", False)),
            "reward_points": max(0, _safe_int(invite_cfg.get("reward_points"), 0)),
            "query_command": str(invite_cfg.get("query_command") or DEFAULT_GROUP_CONFIG["invite_links"]["query_command"]),
            "today_rank_command": str(invite_cfg.get("today_rank_command") or DEFAULT_GROUP_CONFIG["invite_links"]["today_rank_command"]),
            "month_rank_command": str(invite_cfg.get("month_rank_command") or DEFAULT_GROUP_CONFIG["invite_links"]["month_rank_command"]),
            "total_rank_command": str(invite_cfg.get("total_rank_command") or DEFAULT_GROUP_CONFIG["invite_links"]["total_rank_command"]),
            "result_format": str(invite_cfg.get("result_format") or DEFAULT_GROUP_CONFIG["invite_links"]["result_format"]),
            "only_admin_can_query_rank": bool(invite_cfg.get("only_admin_can_query_rank", False)),
            "auto_delete_sec": max(0, _safe_int(invite_cfg.get("auto_delete_sec"), 0)),
            "notify_message": {
                "text": str(invite_cfg.get("notify_text") or ""),
                "photo_file_id": str(invite_cfg.get("notify_photo_file_id") or ""),
                "buttons": _normalize_buttons(invite_cfg.get("notify_buttons") or []),
            },
        }
        return {"module": meta, "supported": True, "editor": "invite", "data": data}
    if key == "points":
        points_cfg = _normalize_typed_value(DEFAULT_GROUP_CONFIG["points"], cfg.get("points", {}) or {})
        data = {
            "enabled": bool(points_cfg.get("enabled", False)),
            "chat_points_enabled": bool(points_cfg.get("chat_points_enabled", False)),
            "sign_command": str(points_cfg.get("sign_command") or DEFAULT_GROUP_CONFIG["points"]["sign_command"]),
            "query_command": str(points_cfg.get("query_command") or DEFAULT_GROUP_CONFIG["points"]["query_command"]),
            "rank_command": str(points_cfg.get("rank_command") or DEFAULT_GROUP_CONFIG["points"]["rank_command"]),
            "sign_points": max(0, _safe_int(points_cfg.get("sign_points"), DEFAULT_GROUP_CONFIG["points"]["sign_points"])),
            "chat_points_per_message": max(0, _safe_int(points_cfg.get("chat_points_per_message"), DEFAULT_GROUP_CONFIG["points"]["chat_points_per_message"])),
            "min_text_length": max(0, _safe_int(points_cfg.get("min_text_length"), DEFAULT_GROUP_CONFIG["points"]["min_text_length"])),
            "admin_adjust_enabled": bool(points_cfg.get("admin_adjust_enabled", False)),
        }
        return {"module": meta, "supported": True, "editor": "points", "data": data}
    if key == "activity":
        activity_cfg = _normalize_typed_value(DEFAULT_GROUP_CONFIG["activity"], cfg.get("activity", {}) or {})
        data = {
            "enabled": bool(activity_cfg.get("enabled", True)),
            "today_command": str(activity_cfg.get("today_command") or DEFAULT_GROUP_CONFIG["activity"]["today_command"]),
            "month_command": str(activity_cfg.get("month_command") or DEFAULT_GROUP_CONFIG["activity"]["month_command"]),
            "total_command": str(activity_cfg.get("total_command") or DEFAULT_GROUP_CONFIG["activity"]["total_command"]),
        }
        return {"module": meta, "supported": True, "editor": "activity", "data": data}
    if key == "usdt":
        usdt_cfg = _normalize_typed_value(DEFAULT_GROUP_CONFIG["usdt_price"], cfg.get("usdt_price", {}) or {})
        data = {
            "enabled": bool(usdt_cfg.get("enabled", False)),
            "tier": str(usdt_cfg.get("tier") or DEFAULT_GROUP_CONFIG["usdt_price"]["tier"]),
            "show_query_message": bool(usdt_cfg.get("show_query_message", True)),
            "show_calc_message": bool(usdt_cfg.get("show_calc_message", True)),
            "alias_z": str(usdt_cfg.get("alias_z") or DEFAULT_GROUP_CONFIG["usdt_price"]["alias_z"]),
            "alias_w": str(usdt_cfg.get("alias_w") or DEFAULT_GROUP_CONFIG["usdt_price"]["alias_w"]),
            "alias_k": str(usdt_cfg.get("alias_k") or DEFAULT_GROUP_CONFIG["usdt_price"]["alias_k"]),
            "exchanges": [str(item) for item in (usdt_cfg.get("exchanges") or DEFAULT_GROUP_CONFIG["usdt_price"]["exchanges"]) if str(item).strip()],
        }
        return {"module": meta, "supported": True, "editor": "usdt", "data": data}
    if key == "verified":
        verified_cfg = _normalize_typed_value(DEFAULT_GROUP_CONFIG["verified_user"], cfg.get("verified_user", {}) or {})
        message = build_verified_user_message_payload(verified_cfg)
        data = {
            "enabled": bool(verified_cfg.get("enabled", False)),
            "members": normalize_verified_members(verified_cfg.get("members") or []),
            "message": {
                "text": str(message.get("text") or ""),
                "photo_file_id": str(message.get("photo_file_id") or ""),
                "buttons": _normalize_buttons(message.get("buttons") or []),
            },
        }
        return {"module": meta, "supported": True, "editor": "verified", "data": data}
    return {"module": meta, "supported": False, "editor": "json", "data": _raw_module_data(group_id, key)}



def save_module_payload(group_id: int, key: str, payload: dict):
    cfg = get_group_config(group_id)
    if key == "welcome":
        data = _require_object(payload.get("data"), "welcome.data")
        cfg["welcome_enabled"] = bool(data.get("enabled"))
        cfg["welcome_text"] = str(data.get("text") or "")
        cfg["welcome_photo_file_id"] = str(data.get("photo_file_id") or "")
        cfg["welcome_buttons"] = _normalize_buttons(data.get("buttons") or [])
        cfg["welcome_ttl_sec"] = max(0, _safe_int(data.get("ttl_sec"), 0))
        cfg["welcome_delete_prev"] = bool(data.get("delete_prev"))
        save_group_config(group_id, cfg)
        return load_module_payload(group_id, key)
    if key == "verify":
        data = _require_object(payload.get("data"), "verify.data")
        message_map = data.get("messages")
        if message_map is not None and not isinstance(message_map, dict):
            raise ValueError("verify.data.messages must be a JSON object")
        targets = data.get("targets")
        if targets is not None and not isinstance(targets, list):
            raise ValueError("verify.data.targets must be a JSON array")
        cfg["verify_enabled"] = bool(data.get("enabled"))
        cfg["verify_mode"] = str(data.get("mode") or "join")
        cfg["verify_timeout_sec"] = max(10, _safe_int(data.get("timeout_sec"), 60))
        cfg["verify_max_attempts"] = max(0, _safe_int(data.get("max_attempts"), 3))
        cfg["verify_fail_action"] = str(data.get("fail_action") or "mute")
        cfg["verify_private"] = bool(data.get("private_enabled"))
        cfg["verify_fail_text"] = str(data.get("fail_text") or "")
        messages = {}
        safe_message_map = message_map or {}
        for mode in VERIFY_MODES:
            item = safe_message_map.get(mode) or {}
            if not isinstance(item, dict):
                item = {}
            messages[mode] = {
                "text": str(item.get("text") or ""),
                "photo_file_id": str(item.get("photo_file_id") or ""),
                "buttons": _normalize_buttons(item.get("buttons") or []),
            }
        cfg["verify_messages"] = messages
        save_group_config(group_id, cfg)
        if isinstance(targets, list):
            save_group_targets(group_id, [deepcopy(item) for item in targets if isinstance(item, dict)])
        return load_module_payload(group_id, key)
    if key == "autoreply":
        data = _require_object(payload.get("data"), "autoreply.data")
        rules_value = data.get("rules")
        if rules_value is not None and not isinstance(rules_value, list):
            raise ValueError("autoreply.data.rules must be a JSON array")
        rules = []
        for rule in list(rules_value or []):
            if not isinstance(rule, dict):
                continue
            rules.append(
                {
                    "keyword": str(rule.get("keyword") or ""),
                    "mode": str(rule.get("mode") or "contains"),
                    "enabled": bool(rule.get("enabled", True)),
                    "reply_text": str(rule.get("reply_text") or ""),
                    "photo_file_id": str(rule.get("photo_file_id") or ""),
                    "buttons": _normalize_buttons(rule.get("buttons") or []),
                }
            )
        save_group_auto_replies(group_id, rules)
        return load_module_payload(group_id, key)
    if key == "autodelete":
        data = _require_object(payload.get("data"), "autodelete.data")
        rules_value = data.get("custom_rules")
        if rules_value is not None and not isinstance(rules_value, list):
            raise ValueError("autodelete.data.custom_rules must be a JSON array")
        sticker_ids = data.get("ad_sticker_ids")
        if sticker_ids is not None and not isinstance(sticker_ids, list):
            raise ValueError("autodelete.data.ad_sticker_ids must be a JSON array")
        delete_cfg = _normalize_typed_value(DEFAULT_AUTO_DELETE, get_group_auto_delete(group_id) or {})
        for field in [
            "delete_system",
            "delete_channel_mask",
            "delete_links",
            "delete_long",
            "delete_videos",
            "delete_stickers",
            "delete_forwarded",
            "delete_ad_stickers",
            "delete_archives",
            "delete_executables",
            "delete_notice_text",
            "delete_documents",
            "delete_mentions",
            "delete_other_commands",
            "delete_qr",
            "delete_edited",
            "delete_member_emoji",
            "delete_member_emoji_only",
            "delete_external_reply",
            "delete_shared_contact",
            "exclude_admins",
        ]:
            delete_cfg[field] = bool(data.get(field))
        delete_cfg["long_length"] = max(1, _safe_int(data.get("long_length"), DEFAULT_AUTO_DELETE["long_length"]))
        delete_cfg["custom_rules"] = [
            {
                "keyword": str(rule.get("keyword") or "").strip(),
                "mode": str(rule.get("mode") or "contains"),
            }
            for rule in list(rules_value or [])
            if isinstance(rule, dict) and str(rule.get("keyword") or "").strip()
        ]
        delete_cfg["ad_sticker_ids"] = [str(item).strip() for item in list(sticker_ids or []) if str(item).strip()]
        save_group_auto_delete(group_id, delete_cfg)
        return load_module_payload(group_id, key)
    if key == "autoban":
        data = _require_object(payload.get("data"), "autoban.data")
        rules_value = data.get("rules")
        if rules_value is not None and not isinstance(rules_value, list):
            raise ValueError("autoban.data.rules must be a JSON array")
        ban_cfg = _normalize_typed_value(DEFAULT_AUTO_BAN, get_group_auto_ban(group_id) or {})
        ban_cfg["enabled"] = bool(data.get("enabled", True))
        ban_cfg["default_duration_sec"] = max(0, _safe_int(data.get("default_duration_sec"), DEFAULT_AUTO_BAN["default_duration_sec"]))
        ban_cfg["rules"] = _normalize_keyword_rules(rules_value or [], with_duration=True, default_duration=ban_cfg["default_duration_sec"])
        save_group_auto_ban(group_id, ban_cfg)
        return load_module_payload(group_id, key)
    if key == "automute":
        data = _require_object(payload.get("data"), "automute.data")
        rules_value = data.get("rules")
        if rules_value is not None and not isinstance(rules_value, list):
            raise ValueError("automute.data.rules must be a JSON array")
        mute_cfg = _normalize_typed_value(DEFAULT_AUTO_MUTE, get_group_auto_mute(group_id) or {})
        mute_cfg["default_duration_sec"] = max(1, _safe_int(data.get("default_duration_sec"), DEFAULT_AUTO_MUTE["default_duration_sec"]))
        mute_cfg["rules"] = _normalize_keyword_rules(rules_value or [], with_duration=True, default_duration=mute_cfg["default_duration_sec"])
        save_group_auto_mute(group_id, mute_cfg)
        return load_module_payload(group_id, key)
    if key == "autowarn":
        data = _require_object(payload.get("data"), "autowarn.data")
        rules_value = data.get("rules")
        if rules_value is not None and not isinstance(rules_value, list):
            raise ValueError("autowarn.data.rules must be a JSON array")
        warn_cfg = _normalize_typed_value(DEFAULT_AUTO_WARN, get_group_auto_warn(group_id) or {})
        action = str(data.get("action") or warn_cfg.get("action") or DEFAULT_AUTO_WARN["action"]).strip().lower()
        if action not in AUTOWARN_ACTIONS:
            action = DEFAULT_AUTO_WARN["action"]
        warn_cfg["enabled"] = bool(data.get("enabled", True))
        warn_cfg["warn_limit"] = max(1, _safe_int(data.get("warn_limit"), DEFAULT_AUTO_WARN["warn_limit"]))
        warn_cfg["mute_seconds"] = max(1, _safe_int(data.get("mute_seconds"), DEFAULT_AUTO_WARN["mute_seconds"]))
        warn_cfg["action"] = action
        warn_cfg["cmd_mute_enabled"] = bool(data.get("cmd_mute_enabled", False))
        warn_cfg["warn_text"] = str(data.get("warn_text") or "")
        warn_cfg["rules"] = _normalize_keyword_rules(rules_value or [])
        save_group_auto_warn(group_id, warn_cfg)
        return load_module_payload(group_id, key)
    if key == "schedule":
        data = _require_object(payload.get("data"), "schedule.data")
        items_value = data.get("items")
        if items_value is not None and not isinstance(items_value, list):
            raise ValueError("schedule.data.items must be a JSON array")
        schedule_cfg = _normalize_typed_value(DEFAULT_GROUP_CONFIG["schedule"], cfg.get("schedule", {}) or {})
        schedule_cfg["enabled"] = bool(data.get("enabled", True))
        normalized_items = []
        for item in list(items_value or []):
            if not isinstance(item, dict):
                continue
            normalized = {
                "id": _safe_int(item.get("id"), 0),
                "text": str(item.get("text") or ""),
                "photo_file_id": str(item.get("photo_file_id") or ""),
                "buttons": _normalize_buttons(item.get("buttons") or []),
                "interval_sec": max(60, _safe_int(item.get("interval_sec"), 3600)),
                "next_at": max(0, _safe_int(item.get("next_at"), 0)),
                "enabled": bool(item.get("enabled", True)),
            }
            if normalized["text"] or normalized["photo_file_id"] or normalized["buttons"]:
                normalized_items.append(normalized)
        cfg["schedule"] = schedule_cfg
        save_group_config(group_id, cfg)
        save_schedule_items(group_id, normalized_items)
        return load_module_payload(group_id, key)
    if key == "ad":
        data = _require_object(payload.get("data"), "ad.data")
        ad_cfg = _normalize_typed_value(DEFAULT_GROUP_CONFIG["ad_filter"], cfg.get("ad_filter", {}) or {})
        for field in AD_FILTER_FIELDS:
            ad_cfg[field] = bool(data.get(field))
        cfg["ad_filter"] = ad_cfg
        save_group_config(group_id, cfg)
        return load_module_payload(group_id, key)
    if key == "cmd":
        data = _require_object(payload.get("data"), "cmd.data")
        cmd_cfg = _normalize_typed_value(DEFAULT_GROUP_CONFIG["command_gate"], cfg.get("command_gate", {}) or {})
        for field in COMMAND_GATE_FIELDS:
            cmd_cfg[field] = bool(data.get(field))
        cfg["command_gate"] = cmd_cfg
        save_group_config(group_id, cfg)
        return load_module_payload(group_id, key)
    if key == "member":
        data = _require_object(payload.get("data"), "member.data")
        member_cfg = _normalize_typed_value(DEFAULT_GROUP_CONFIG["member_watch"], cfg.get("member_watch", {}) or {})
        for field in MEMBER_WATCH_FIELDS:
            member_cfg[field] = bool(data.get(field))
        cfg["member_watch"] = member_cfg
        save_group_config(group_id, cfg)
        return load_module_payload(group_id, key)
    if key == "antispam":
        data = _require_object(payload.get("data"), "antispam.data")
        types_value = data.get("types")
        if types_value is not None and not isinstance(types_value, list):
            raise ValueError("antispam.data.types must be a JSON array")
        spam_cfg = _normalize_typed_value(DEFAULT_ANTI_SPAM, get_group_anti_spam(group_id) or {})
        action = str(data.get("action") or spam_cfg.get("action") or DEFAULT_ANTI_SPAM["action"]).strip().lower()
        if action not in ANTI_SPAM_ACTIONS:
            action = DEFAULT_ANTI_SPAM["action"]
        spam_cfg["enabled"] = bool(data.get("enabled"))
        spam_cfg["action"] = action
        spam_cfg["mute_seconds"] = max(0, _safe_int(data.get("mute_seconds"), DEFAULT_ANTI_SPAM["mute_seconds"]))
        spam_cfg["window_sec"] = max(1, _safe_int(data.get("window_sec"), DEFAULT_ANTI_SPAM["window_sec"]))
        spam_cfg["threshold"] = max(1, _safe_int(data.get("threshold"), DEFAULT_ANTI_SPAM["threshold"]))
        spam_cfg["types"] = [
            item
            for item in [str(value).strip().lower() for value in list(types_value or [])]
            if item in ANTI_SPAM_TYPES
        ]
        save_group_anti_spam(group_id, spam_cfg)
        return load_module_payload(group_id, key)
    if key == "crypto":
        data = _require_object(payload.get("data"), "crypto.data")
        crypto_cfg = _normalize_typed_value(DEFAULT_GROUP_CONFIG["crypto"], cfg.get("crypto", {}) or {})
        crypto_cfg["wallet_query_enabled"] = bool(data.get("wallet_query_enabled"))
        crypto_cfg["price_query_enabled"] = bool(data.get("price_query_enabled"))
        crypto_cfg["push_enabled"] = bool(data.get("push_enabled"))
        crypto_cfg["default_symbol"] = str(data.get("default_symbol") or DEFAULT_GROUP_CONFIG["crypto"]["default_symbol"]).strip().upper() or DEFAULT_GROUP_CONFIG["crypto"]["default_symbol"]
        crypto_cfg["query_alias"] = str(data.get("query_alias") or DEFAULT_GROUP_CONFIG["crypto"]["query_alias"]).strip() or DEFAULT_GROUP_CONFIG["crypto"]["query_alias"]
        cfg["crypto"] = crypto_cfg
        save_group_config(group_id, cfg)
        return load_module_payload(group_id, key)
    if key == "fun":
        data = _require_object(payload.get("data"), "fun.data")
        fun_cfg = _normalize_typed_value(DEFAULT_GROUP_CONFIG["entertainment"], cfg.get("entertainment", {}) or {})
        fun_cfg["dice_enabled"] = bool(data.get("dice_enabled"))
        fun_cfg["dice_cost"] = max(0, _safe_int(data.get("dice_cost"), DEFAULT_GROUP_CONFIG["entertainment"]["dice_cost"]))
        fun_cfg["dice_command"] = str(data.get("dice_command") or DEFAULT_GROUP_CONFIG["entertainment"]["dice_command"]).strip() or DEFAULT_GROUP_CONFIG["entertainment"]["dice_command"]
        fun_cfg["gomoku_enabled"] = bool(data.get("gomoku_enabled"))
        fun_cfg["gomoku_command"] = str(data.get("gomoku_command") or DEFAULT_GROUP_CONFIG["entertainment"]["gomoku_command"]).strip() or DEFAULT_GROUP_CONFIG["entertainment"]["gomoku_command"]
        cfg["entertainment"] = fun_cfg
        save_group_config(group_id, cfg)
        return load_module_payload(group_id, key)
    if key == "lottery":
        data = _require_object(payload.get("data"), "lottery.data")
        lottery_cfg = _normalize_typed_value(DEFAULT_GROUP_CONFIG["lottery"], cfg.get("lottery", {}) or {})
        lottery_cfg["enabled"] = bool(data.get("enabled"))
        lottery_cfg["query_command"] = str(data.get("query_command") or DEFAULT_GROUP_CONFIG["lottery"]["query_command"]).strip() or DEFAULT_GROUP_CONFIG["lottery"]["query_command"]
        lottery_cfg["auto_delete_sec"] = max(0, _safe_int(data.get("auto_delete_sec"), DEFAULT_GROUP_CONFIG["lottery"]["auto_delete_sec"]))
        lottery_cfg["pin_post"] = bool(data.get("pin_post"))
        lottery_cfg["pin_result"] = bool(data.get("pin_result"))
        cfg["lottery"] = lottery_cfg
        save_group_config(group_id, cfg)
        return load_module_payload(group_id, key)
    if key == "related":
        data = _require_object(payload.get("data"), "related.data")
        comment_message = data.get("comment_message") or {}
        if not isinstance(comment_message, dict):
            raise ValueError("related.data.comment_message must be a JSON object")
        related_cfg = _normalize_typed_value(DEFAULT_GROUP_CONFIG["related_channel"], cfg.get("related_channel", {}) or {})
        related_cfg["cancel_top_pin"] = bool(data.get("cancel_top_pin"))
        related_cfg["occupy_comment"] = bool(data.get("occupy_comment"))
        related_cfg["occupy_comment_text"] = str(comment_message.get("text") or "")
        related_cfg["occupy_comment_photo_file_id"] = str(comment_message.get("photo_file_id") or "")
        related_cfg["occupy_comment_buttons"] = _normalize_buttons(comment_message.get("buttons") or [])
        cfg["related_channel"] = related_cfg
        save_group_config(group_id, cfg)
        return load_module_payload(group_id, key)
    if key == "admin_access":
        data = _require_object(payload.get("data"), "admin_access.data")
        access_cfg = _normalize_typed_value(DEFAULT_GROUP_CONFIG["admin_access"], cfg.get("admin_access", {}) or {})
        access_cfg["mode"] = _normalize_access_mode(data.get("mode"))
        cfg["admin_access"] = access_cfg
        save_group_config(group_id, cfg)
        return load_module_payload(group_id, key)
    if key == "nsfw":
        data = _require_object(payload.get("data"), "nsfw.data")
        nsfw_cfg = _normalize_typed_value(DEFAULT_GROUP_CONFIG["nsfw"], cfg.get("nsfw", {}) or {})
        nsfw_cfg["enabled"] = bool(data.get("enabled"))
        nsfw_cfg["sensitivity"] = _normalize_nsfw_sensitivity(data.get("sensitivity"))
        nsfw_cfg["allow_miss"] = bool(data.get("allow_miss"))
        nsfw_cfg["notice_enabled"] = bool(data.get("notice_enabled"))
        nsfw_cfg["delay_delete_sec"] = max(0, _safe_int(data.get("delay_delete_sec"), DEFAULT_GROUP_CONFIG["nsfw"]["delay_delete_sec"]))
        cfg["nsfw"] = nsfw_cfg
        save_group_config(group_id, cfg)
        return load_module_payload(group_id, key)
    if key == "lang":
        data = _require_object(payload.get("data"), "lang.data")
        allowed_value = data.get("allowed")
        if allowed_value is not None and not isinstance(allowed_value, list):
            raise ValueError("lang.data.allowed must be a JSON array")
        lang_cfg = _normalize_typed_value(DEFAULT_GROUP_CONFIG["language_whitelist"], cfg.get("language_whitelist", {}) or {})
        lang_cfg["enabled"] = bool(data.get("enabled"))
        lang_cfg["allowed"] = _normalize_string_list(allowed_value or [], normalize=_normalize_language_code_ui)
        cfg["language_whitelist"] = lang_cfg
        save_group_config(group_id, cfg)
        return load_module_payload(group_id, key)
    if key == "invite":
        data = _require_object(payload.get("data"), "invite.data")
        notify_message = data.get("notify_message") or {}
        if not isinstance(notify_message, dict):
            raise ValueError("invite.data.notify_message must be a JSON object")
        invite_cfg = _normalize_typed_value(DEFAULT_GROUP_CONFIG["invite_links"], cfg.get("invite_links", {}) or {})
        invite_cfg["enabled"] = bool(data.get("enabled"))
        invite_cfg["notify_enabled"] = bool(data.get("notify_enabled"))
        invite_cfg["join_review"] = bool(data.get("join_review"))
        invite_cfg["reward_points"] = max(0, _safe_int(data.get("reward_points"), 0))
        invite_cfg["query_command"] = str(data.get("query_command") or DEFAULT_GROUP_CONFIG["invite_links"]["query_command"]).strip() or DEFAULT_GROUP_CONFIG["invite_links"]["query_command"]
        invite_cfg["today_rank_command"] = str(data.get("today_rank_command") or DEFAULT_GROUP_CONFIG["invite_links"]["today_rank_command"]).strip() or DEFAULT_GROUP_CONFIG["invite_links"]["today_rank_command"]
        invite_cfg["month_rank_command"] = str(data.get("month_rank_command") or DEFAULT_GROUP_CONFIG["invite_links"]["month_rank_command"]).strip() or DEFAULT_GROUP_CONFIG["invite_links"]["month_rank_command"]
        invite_cfg["total_rank_command"] = str(data.get("total_rank_command") or DEFAULT_GROUP_CONFIG["invite_links"]["total_rank_command"]).strip() or DEFAULT_GROUP_CONFIG["invite_links"]["total_rank_command"]
        invite_cfg["result_format"] = str(data.get("result_format") or DEFAULT_GROUP_CONFIG["invite_links"]["result_format"]).strip() or DEFAULT_GROUP_CONFIG["invite_links"]["result_format"]
        invite_cfg["only_admin_can_query_rank"] = bool(data.get("only_admin_can_query_rank"))
        invite_cfg["auto_delete_sec"] = max(0, _safe_int(data.get("auto_delete_sec"), 0))
        invite_cfg["notify_text"] = str(notify_message.get("text") or "")
        invite_cfg["notify_photo_file_id"] = str(notify_message.get("photo_file_id") or "")
        invite_cfg["notify_buttons"] = _normalize_buttons(notify_message.get("buttons") or [])
        cfg["invite_links"] = invite_cfg
        save_group_config(group_id, cfg)
        return load_module_payload(group_id, key)
    if key == "points":
        data = _require_object(payload.get("data"), "points.data")
        points_cfg = _normalize_typed_value(DEFAULT_GROUP_CONFIG["points"], cfg.get("points", {}) or {})
        points_cfg["enabled"] = bool(data.get("enabled"))
        points_cfg["chat_points_enabled"] = bool(data.get("chat_points_enabled"))
        points_cfg["sign_command"] = str(data.get("sign_command") or DEFAULT_GROUP_CONFIG["points"]["sign_command"]).strip() or DEFAULT_GROUP_CONFIG["points"]["sign_command"]
        points_cfg["query_command"] = str(data.get("query_command") or DEFAULT_GROUP_CONFIG["points"]["query_command"]).strip() or DEFAULT_GROUP_CONFIG["points"]["query_command"]
        points_cfg["rank_command"] = str(data.get("rank_command") or DEFAULT_GROUP_CONFIG["points"]["rank_command"]).strip() or DEFAULT_GROUP_CONFIG["points"]["rank_command"]
        points_cfg["sign_points"] = max(0, _safe_int(data.get("sign_points"), DEFAULT_GROUP_CONFIG["points"]["sign_points"]))
        points_cfg["chat_points_per_message"] = max(0, _safe_int(data.get("chat_points_per_message"), DEFAULT_GROUP_CONFIG["points"]["chat_points_per_message"]))
        points_cfg["min_text_length"] = max(0, _safe_int(data.get("min_text_length"), DEFAULT_GROUP_CONFIG["points"]["min_text_length"]))
        points_cfg["admin_adjust_enabled"] = bool(data.get("admin_adjust_enabled"))
        cfg["points"] = points_cfg
        save_group_config(group_id, cfg)
        return load_module_payload(group_id, key)
    if key == "activity":
        data = _require_object(payload.get("data"), "activity.data")
        activity_cfg = _normalize_typed_value(DEFAULT_GROUP_CONFIG["activity"], cfg.get("activity", {}) or {})
        activity_cfg["enabled"] = bool(data.get("enabled"))
        activity_cfg["today_command"] = str(data.get("today_command") or DEFAULT_GROUP_CONFIG["activity"]["today_command"]).strip() or DEFAULT_GROUP_CONFIG["activity"]["today_command"]
        activity_cfg["month_command"] = str(data.get("month_command") or DEFAULT_GROUP_CONFIG["activity"]["month_command"]).strip() or DEFAULT_GROUP_CONFIG["activity"]["month_command"]
        activity_cfg["total_command"] = str(data.get("total_command") or DEFAULT_GROUP_CONFIG["activity"]["total_command"]).strip() or DEFAULT_GROUP_CONFIG["activity"]["total_command"]
        cfg["activity"] = activity_cfg
        save_group_config(group_id, cfg)
        return load_module_payload(group_id, key)
    if key == "usdt":
        data = _require_object(payload.get("data"), "usdt.data")
        usdt_cfg = _normalize_typed_value(DEFAULT_GROUP_CONFIG["usdt_price"], cfg.get("usdt_price", {}) or {})
        selected_exchanges = [str(item).strip().lower() for item in list(data.get("exchanges") or []) if str(item).strip()]
        selected_exchanges = [item for item in selected_exchanges if item in {"binance", "okx", "htx"}]
        if not selected_exchanges:
            selected_exchanges = list(DEFAULT_GROUP_CONFIG["usdt_price"]["exchanges"])
        usdt_cfg["enabled"] = bool(data.get("enabled"))
        usdt_cfg["tier"] = str(data.get("tier") or DEFAULT_GROUP_CONFIG["usdt_price"]["tier"]).strip() or DEFAULT_GROUP_CONFIG["usdt_price"]["tier"]
        usdt_cfg["show_query_message"] = bool(data.get("show_query_message"))
        usdt_cfg["show_calc_message"] = bool(data.get("show_calc_message"))
        usdt_cfg["alias_z"] = str(data.get("alias_z") or DEFAULT_GROUP_CONFIG["usdt_price"]["alias_z"]).strip() or DEFAULT_GROUP_CONFIG["usdt_price"]["alias_z"]
        usdt_cfg["alias_w"] = str(data.get("alias_w") or DEFAULT_GROUP_CONFIG["usdt_price"]["alias_w"]).strip() or DEFAULT_GROUP_CONFIG["usdt_price"]["alias_w"]
        usdt_cfg["alias_k"] = str(data.get("alias_k") or DEFAULT_GROUP_CONFIG["usdt_price"]["alias_k"]).strip() or DEFAULT_GROUP_CONFIG["usdt_price"]["alias_k"]
        usdt_cfg["exchanges"] = selected_exchanges
        cfg["usdt_price"] = usdt_cfg
        save_group_config(group_id, cfg)
        return load_module_payload(group_id, key)
    if key == "verified":
        data = _require_object(payload.get("data"), "verified.data")
        message = _require_object(data.get("message") or {}, "verified.data.message")
        members_value = data.get("members")
        if members_value is not None and not isinstance(members_value, list):
            raise ValueError("verified.data.members must be a JSON array")
        verified_cfg = _normalize_typed_value(DEFAULT_GROUP_CONFIG["verified_user"], cfg.get("verified_user", {}) or {})
        verified_cfg["enabled"] = bool(data.get("enabled"))
        verified_cfg["members"] = normalize_verified_members(members_value or [])
        verified_cfg["reply_text"] = str(message.get("text") or "")
        verified_cfg["reply_photo_file_id"] = str(message.get("photo_file_id") or "")
        verified_cfg["reply_buttons"] = _normalize_buttons(message.get("buttons") or [])
        cfg["verified_user"] = verified_cfg
        save_group_config(group_id, cfg)
        return load_module_payload(group_id, key)

    if key not in JSON_MODULE_DEFAULTS:
        raise KeyError(key)
    data = _normalize_json_module_data(key, payload.get("data"))
    if key == "autodelete":
        save_group_auto_delete(group_id, data)
    elif key == "autoban":
        save_group_auto_ban(group_id, data)
    elif key == "automute":
        save_group_auto_mute(group_id, data)
    elif key == "autowarn":
        save_group_auto_warn(group_id, data)
    elif key == "antispam":
        save_group_anti_spam(group_id, data)
    elif key == "ad":
        cfg["ad_filter"] = data
        save_group_config(group_id, cfg)
    elif key == "cmd":
        cfg["command_gate"] = data
        save_group_config(group_id, cfg)
    elif key == "member":
        cfg["member_watch"] = data
        save_group_config(group_id, cfg)
    elif key == "schedule":
        cfg["schedule"] = data["config"]
        save_group_config(group_id, cfg)
        save_schedule_items(group_id, data["items"])
    elif key == "points":
        cfg["points"] = data
        save_group_config(group_id, cfg)
    elif key == "activity":
        cfg["activity"] = data
        save_group_config(group_id, cfg)
    elif key == "usdt":
        cfg["usdt_price"] = data
        save_group_config(group_id, cfg)
    elif key == "related":
        cfg["related_channel"] = data
        save_group_config(group_id, cfg)
    elif key == "admin_access":
        cfg["admin_access"] = data
        save_group_config(group_id, cfg)
    elif key == "nsfw":
        cfg["nsfw"] = data
        save_group_config(group_id, cfg)
    elif key == "lang":
        cfg["language_whitelist"] = data
        save_group_config(group_id, cfg)
    elif key == "verified":
        cfg["verified_user"] = data
        save_group_config(group_id, cfg)
    else:
        raise KeyError(key)
    return load_module_payload(group_id, key)



def render_preview(message: dict, preview_context: dict | None = None) -> dict:
    message = _require_object(message, "message")
    if preview_context is not None and not isinstance(preview_context, dict):
        raise ValueError("preview_context must be a JSON object")
    preview_context = preview_context or {}
    user_name = str(preview_context.get("userName") or preview_context.get("user") or "Member")
    group_name = str(preview_context.get("group") or "")
    extra = {key: value for key, value in preview_context.items() if key not in {"user", "userName", "group"}}
    text = render_template(str(message.get("text") or ""), _PreviewUser(user_name), _PreviewChat(group_name), extra).replace(
        "\n", "<br>"
    )
    rows = {}
    for button in _normalize_buttons(message.get("buttons") or []):
        row = _safe_int(button.get("row"), 0)
        rows.setdefault(row, [])
        if len(rows[row]) < 2:
            rows[row].append(button)
    return {
        "text_html": text,
        "photo_file_id": str(message.get("photo_file_id") or ""),
        "rows": [rows[key] for key in sorted(rows.keys())],
        "delete_after_sec": _safe_int(message.get("delete_after_sec"), 0),
    }

