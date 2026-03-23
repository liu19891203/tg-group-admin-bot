
from copy import deepcopy

from .kv import kv_get_json, kv_set_json, kv_del
from ..models.config import DEFAULT_GROUP_CONFIG, DEFAULT_AUTO_DELETE, DEFAULT_AUTO_BAN, DEFAULT_AUTO_MUTE, DEFAULT_AUTO_WARN, DEFAULT_ANTI_SPAM


DEFAULT_USER_PROFILE = {
    "timezone_offset": 8,
    "language": "zh_cn",
    "membership": {
        "expires_at": 0,
    },
    "clone_bots": [],
    "orders": [],
}


def _deep_merge(defaults, overrides):
    if not isinstance(defaults, dict):
        return deepcopy(overrides if overrides is not None else defaults)
    merged = deepcopy(defaults)
    if not isinstance(overrides, dict):
        return merged
    for key, value in overrides.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def get_group_config(group_id: int):
    cfg = kv_get_json(f"group:{group_id}", {})
    merged = _deep_merge(DEFAULT_GROUP_CONFIG, cfg or {})
    merged.setdefault("welcome_buttons", [])
    merged.setdefault("verify_messages", {})
    return merged


def save_group_config(group_id: int, cfg: dict):
    kv_set_json(f"group:{group_id}", cfg)


def get_group_targets(group_id: int):
    return kv_get_json(f"group:{group_id}:targets", [])


def save_group_targets(group_id: int, targets: list):
    kv_set_json(f"group:{group_id}:targets", targets)


def get_group_auto_replies(group_id: int):
    return kv_get_json(f"group:{group_id}:auto_replies", [])


def save_group_auto_replies(group_id: int, rules: list):
    kv_set_json(f"group:{group_id}:auto_replies", rules)


def get_group_auto_delete(group_id: int):
    cfg = kv_get_json(f"group:{group_id}:auto_delete", {})
    merged = {**DEFAULT_AUTO_DELETE, **(cfg or {})}
    merged["custom_rules"] = merged.get("custom_rules") or []
    merged["ad_sticker_ids"] = merged.get("ad_sticker_ids") or []
    return merged


def save_group_auto_delete(group_id: int, cfg: dict):
    kv_set_json(f"group:{group_id}:auto_delete", cfg)


def get_group_auto_ban(group_id: int):
    cfg = kv_get_json(f"group:{group_id}:auto_ban", {})
    merged = {**DEFAULT_AUTO_BAN, **(cfg or {})}
    merged["rules"] = merged.get("rules") or []
    return merged


def save_group_auto_ban(group_id: int, cfg: dict):
    kv_set_json(f"group:{group_id}:auto_ban", cfg)


def get_group_auto_mute(group_id: int):
    cfg = kv_get_json(f"group:{group_id}:auto_mute", {})
    merged = {**DEFAULT_AUTO_MUTE, **(cfg or {})}
    merged["rules"] = merged.get("rules") or []
    return merged


def save_group_auto_mute(group_id: int, cfg: dict):
    kv_set_json(f"group:{group_id}:auto_mute", cfg)


def get_group_auto_warn(group_id: int):
    cfg = kv_get_json(f"group:{group_id}:auto_warn", {})
    merged = {**DEFAULT_AUTO_WARN, **(cfg or {})}
    merged["rules"] = merged.get("rules") or []
    return merged


def save_group_auto_warn(group_id: int, cfg: dict):
    kv_set_json(f"group:{group_id}:auto_warn", cfg)


def get_group_anti_spam(group_id: int):
    cfg = kv_get_json(f"group:{group_id}:anti_spam", {})
    merged = {**DEFAULT_ANTI_SPAM, **(cfg or {})}
    merged["types"] = merged.get("types") or []
    return merged


def save_group_anti_spam(group_id: int, cfg: dict):
    kv_set_json(f"group:{group_id}:anti_spam", cfg)


def get_known_groups():
    return kv_get_json("known_groups", [])


def upsert_known_group(chat):
    if chat is None or chat.type not in ("group", "supergroup"):
        return
    groups = get_known_groups()
    existing = next((g for g in groups if g.get("id") == chat.id), None)
    if existing:
        if existing.get("title") != chat.title:
            existing["title"] = chat.title
    else:
        groups.append({"id": chat.id, "title": chat.title})
    kv_set_json("known_groups", groups)
    try:
        cfg = get_group_config(chat.id)
        cfg["group_title"] = chat.title
        save_group_config(chat.id, cfg)
    except Exception:
        pass


def get_admin_state(user_id: int):
    return kv_get_json(
        f"admin:{user_id}",
        {"active_group_id": None, "state": None, "tmp": {}},
    )


def save_admin_state(user_id: int, state: dict):
    kv_set_json(f"admin:{user_id}", state)


def clear_admin_state(user_id: int):
    kv_del(f"admin:{user_id}")

def get_user_profile(user_id: int):
    profile = kv_get_json(f"user:{user_id}", {})
    merged = _deep_merge(DEFAULT_USER_PROFILE, profile or {})
    merged["clone_bots"] = merged.get("clone_bots") or []
    merged["orders"] = merged.get("orders") or []
    merged["membership"] = _deep_merge(DEFAULT_USER_PROFILE["membership"], merged.get("membership") or {})
    return merged


def save_user_profile(user_id: int, profile: dict):
    kv_set_json(f"user:{user_id}", profile)


def get_manual_order(order_id: str):
    return kv_get_json(f"manual_order:{order_id}", None)


def save_manual_order(order_id: str, order: dict):
    kv_set_json(f"manual_order:{order_id}", order)


def get_clone_launch_request(clone_request_id: str):
    return kv_get_json(f"clone_launch:{clone_request_id}", None)


def save_clone_launch_request(clone_request_id: str, payload: dict):
    kv_set_json(f"clone_launch:{clone_request_id}", payload)


def clear_clone_launch_request(clone_request_id: str):
    kv_del(f"clone_launch:{clone_request_id}")

