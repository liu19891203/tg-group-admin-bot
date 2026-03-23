import time

from ..storage.config_store import get_group_config, get_user_profile, save_group_config

FREE_SCHEDULE_LIMIT = 5
MEMBER_SCHEDULE_LIMIT = 50
FREE_AUTOREPLY_LIMIT = 10
MEMBER_AUTOREPLY_LIMIT = 200
FREE_SCHEDULE_SWEEP_EVERY = 3


def _as_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def membership_expires_at(user_id: int) -> int:
    profile = get_user_profile(user_id)
    return _as_int((profile.get("membership") or {}).get("expires_at"))


def has_active_membership(user_id: int | None, at_ts: int | None = None) -> bool:
    uid = _as_int(user_id)
    if uid <= 0:
        return False
    now_ts = _as_int(at_ts) or int(time.time())
    return membership_expires_at(uid) > now_ts


def group_service_owner_id(group_id: int) -> int | None:
    owner_id = _as_int(get_group_config(group_id).get("service_owner_user_id"))
    return owner_id or None


def maybe_bind_group_service_owner(group_id: int, user_id: int) -> int | None:
    gid = _as_int(group_id)
    uid = _as_int(user_id)
    if gid == 0 or uid <= 0:
        return group_service_owner_id(group_id)

    cfg = get_group_config(gid)
    current_owner = _as_int(cfg.get("service_owner_user_id"))
    if current_owner == uid:
        return uid
    if not current_owner or not has_active_membership(current_owner):
        cfg["service_owner_user_id"] = uid
        save_group_config(gid, cfg)
        return uid
    return current_owner or None


def group_plan(group_id: int, at_ts: int | None = None) -> str:
    owner_id = group_service_owner_id(group_id)
    if owner_id and has_active_membership(owner_id, at_ts=at_ts):
        return "member"
    return "free"


def group_plan_label(group_id: int, at_ts: int | None = None) -> str:
    return "会员" if group_plan(group_id, at_ts=at_ts) == "member" else "免费"


def schedule_limit_for_group(group_id: int, at_ts: int | None = None) -> int:
    return MEMBER_SCHEDULE_LIMIT if group_plan(group_id, at_ts=at_ts) == "member" else FREE_SCHEDULE_LIMIT


def auto_reply_limit_for_group(group_id: int, at_ts: int | None = None) -> int:
    return MEMBER_AUTOREPLY_LIMIT if group_plan(group_id, at_ts=at_ts) == "member" else FREE_AUTOREPLY_LIMIT


def is_priority_group(group_id: int, at_ts: int | None = None) -> bool:
    return group_plan(group_id, at_ts=at_ts) == "member"


def should_process_scheduled_group(group_id: int, sweep_tick: int, at_ts: int | None = None) -> bool:
    if is_priority_group(group_id, at_ts=at_ts):
        return True
    return _as_int(sweep_tick) % FREE_SCHEDULE_SWEEP_EVERY == 0


def clone_launch_state(clone: dict | None, at_ts: int | None = None) -> str:
    if not clone:
        return "missing"
    now_ts = _as_int(at_ts) or int(time.time())
    status = str(clone.get("status") or "saved")
    expires_at = _as_int(clone.get("expires_at"))
    if status == "approved_pending_launch":
        if expires_at > now_ts:
            return "launch_ready"
        return "expired"
    return status


def launch_ready_clone_count(user_id: int, at_ts: int | None = None) -> int:
    profile = get_user_profile(user_id)
    return sum(1 for clone in profile.get("clone_bots") or [] if clone_launch_state(clone, at_ts=at_ts) == "launch_ready")
