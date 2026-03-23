from .kv import kv_get_json, kv_set_json, kv_del
from ..utils.time import warn_date_str


def _verify_index_key(group_id: int) -> str:
    return f"verify:{group_id}:users"


def get_verify_session_users(group_id: int) -> list[int]:
    raw_users = kv_get_json(_verify_index_key(group_id), []) or []
    users: list[int] = []
    for value in raw_users:
        try:
            user_id = int(value)
        except (TypeError, ValueError):
            continue
        if user_id not in users:
            users.append(user_id)
    if users != raw_users:
        kv_set_json(_verify_index_key(group_id), users)
    return users


def _remember_verify_session_user(group_id: int, user_id: int):
    users = get_verify_session_users(group_id)
    if int(user_id) in users:
        return
    users.append(int(user_id))
    kv_set_json(_verify_index_key(group_id), users)


def _forget_verify_session_user(group_id: int, user_id: int):
    users = [uid for uid in get_verify_session_users(group_id) if int(uid) != int(user_id)]
    kv_set_json(_verify_index_key(group_id), users)


def get_verify_session(group_id: int, user_id: int):
    session = kv_get_json(f"verify:{group_id}:{user_id}", None)
    if session:
        _remember_verify_session_user(group_id, user_id)
    return session


def save_verify_session(group_id: int, user_id: int, session: dict):
    kv_set_json(f"verify:{group_id}:{user_id}", session)
    _remember_verify_session_user(group_id, user_id)


def clear_verify_session(group_id: int, user_id: int):
    kv_del(f"verify:{group_id}:{user_id}")
    _forget_verify_session_user(group_id, user_id)


def get_warn_counter(group_id: int, user_id: int):
    return kv_get_json(f"warn:{group_id}:{user_id}", {"date": warn_date_str(), "count": 0})


def save_warn_counter(group_id: int, user_id: int, data: dict):
    kv_set_json(f"warn:{group_id}:{user_id}", data)


def get_welcome_queue(group_id: int):
    return kv_get_json(f"welcome:{group_id}:queue", [])


def save_welcome_queue(group_id: int, queue: list):
    kv_set_json(f"welcome:{group_id}:queue", queue)