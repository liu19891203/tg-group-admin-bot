import hmac
import os
import secrets
import time
from urllib.parse import urlparse

from ..storage.kv import kv_del, kv_get_json, kv_set_json

WEB_LOGIN_TTL_SEC = max(60, int(os.environ.get("WEB_LOGIN_TTL_SEC", "600") or 600))
WEB_LOGIN_POLL_INTERVAL_MS = max(1000, int(os.environ.get("WEB_LOGIN_POLL_INTERVAL_MS", "2000") or 2000))
WEB_LOGIN_CODE_TTL_SEC = max(60, int(os.environ.get("WEB_LOGIN_CODE_TTL_SEC", "300") or 300))
WEB_LOGIN_CODE_MAX_ATTEMPTS = max(1, int(os.environ.get("WEB_LOGIN_CODE_MAX_ATTEMPTS", "5") or 5))


def _web_login_key(request_id: str) -> str:
    return f"web_login:{str(request_id or '').strip()}"


def _normalize_requested_group_id(value) -> int | None:
    try:
        group_id = int(value or 0)
    except (TypeError, ValueError):
        return None
    return group_id or None


def _sanitize_origin(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlparse(raw)
    except Exception:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"


def _sanitize_group_title(value: str | None) -> str:
    return str(value or "").strip()[:128]


def _save_web_login_request(request: dict) -> bool:
    return kv_set_json(_web_login_key(str(request.get("request_id") or "")), request)


def web_login_settings() -> dict:
    return {
        "enabled": True,
        "mode": "bot_deep_link",
        "ttl_sec": WEB_LOGIN_TTL_SEC,
        "poll_interval_ms": WEB_LOGIN_POLL_INTERVAL_MS,
        "bot_code_entry": True,
    }


def build_web_login_payload(user) -> dict:
    now_ts = int(time.time())
    return {
        "id": int(getattr(user, "id", 0) or 0),
        "username": str(getattr(user, "username", "") or ""),
        "first_name": str(getattr(user, "first_name", "") or ""),
        "last_name": str(getattr(user, "last_name", "") or ""),
        "auth_date": now_ts,
    }


def create_web_login_request(requested_group_id=None, origin: str | None = None) -> dict:
    now_ts = int(time.time())
    request = {
        "request_id": secrets.token_hex(8),
        "browser_token": secrets.token_urlsafe(24),
        "status": "pending",
        "created_at": now_ts,
        "expires_at": now_ts + WEB_LOGIN_TTL_SEC,
        "requested_group_id": _normalize_requested_group_id(requested_group_id),
        "origin": _sanitize_origin(origin),
        "approved_at": 0,
        "user": None,
    }
    _save_web_login_request(request)
    return request


def create_bot_entry_login_request(user, requested_group_id=None, requested_group_title: str | None = None, origin: str | None = None) -> dict:
    request = create_web_login_request(requested_group_id=requested_group_id, origin=origin)
    request["mode"] = "bot_code"
    request["requested_group_title"] = _sanitize_group_title(requested_group_title)
    request["bound_user"] = build_web_login_payload(user)
    request["code"] = ""
    request["code_expires_at"] = 0
    request["code_sent_at"] = 0
    request["attempts"] = 0
    _save_web_login_request(request)
    return request


def get_web_login_request(request_id: str, *, purge_expired: bool = True) -> dict | None:
    request = kv_get_json(_web_login_key(request_id), None)
    if not isinstance(request, dict):
        return None
    try:
        expires_at = int(request.get("expires_at") or 0)
    except (TypeError, ValueError):
        expires_at = 0
    if expires_at > 0 and expires_at < int(time.time()):
        if purge_expired:
            kv_del(_web_login_key(request_id))
        return None
    return request


def approve_web_login_request(request_id: str, user) -> tuple[bool, str, dict | None]:
    request = get_web_login_request(request_id)
    if not request:
        return False, "not_found", None
    status = str(request.get("status") or "pending")
    if status == "approved":
        return True, "already_approved", request
    if status != "pending":
        return False, "invalid_state", None
    payload = build_web_login_payload(user)
    if int(payload.get("id") or 0) <= 0:
        return False, "invalid_user", None
    request["status"] = "approved"
    request["approved_at"] = int(time.time())
    request["user"] = payload
    _save_web_login_request(request)
    return True, "approved", request


def read_web_login_status(request_id: str, browser_token: str) -> dict:
    request = get_web_login_request(request_id)
    if not request:
        return {"status": "expired"}
    expected = str(request.get("browser_token") or "")
    if not expected or not hmac.compare_digest(expected, str(browser_token or "")):
        return {"status": "forbidden"}
    status = str(request.get("status") or "pending")
    if status == "approved" and isinstance(request.get("user"), dict):
        return {
            "status": "approved",
            "user": dict(request["user"]),
            "requested_group_id": _normalize_requested_group_id(request.get("requested_group_id")),
        }
    return {
        "status": "pending",
        "expires_at": int(request.get("expires_at") or 0),
        "poll_interval_ms": WEB_LOGIN_POLL_INTERVAL_MS,
    }


def consume_web_login_request(request_id: str, browser_token: str) -> dict:
    result = read_web_login_status(request_id, browser_token)
    if result.get("status") == "approved":
        kv_del(_web_login_key(request_id))
    return result


def _issue_login_code() -> str:
    return f"{secrets.randbelow(1000000):06d}"


def begin_bot_code_login(request_id: str, *, force_new_code: bool = False):
    request = get_web_login_request(request_id)
    if not request:
        return {"status": "expired"}, None, None
    if str(request.get("mode") or "") != "bot_code" or not isinstance(request.get("bound_user"), dict):
        return {"status": "forbidden"}, None, None
    now_ts = int(time.time())
    current_code = str(request.get("code") or "")
    code_expires_at = int(request.get("code_expires_at") or 0)
    should_send = force_new_code or not current_code or code_expires_at <= now_ts
    if should_send:
        current_code = _issue_login_code()
        code_expires_at = min(int(request.get("expires_at") or (now_ts + WEB_LOGIN_CODE_TTL_SEC)), now_ts + WEB_LOGIN_CODE_TTL_SEC)
        request["code"] = current_code
        request["code_expires_at"] = code_expires_at
        request["code_sent_at"] = now_ts
    request["browser_token"] = secrets.token_urlsafe(24)
    request["attempts"] = 0
    _save_web_login_request(request)
    return {
        "status": "code_required",
        "request_id": str(request.get("request_id") or ""),
        "browser_token": str(request.get("browser_token") or ""),
        "expires_at": int(request.get("code_expires_at") or code_expires_at or 0),
        "requested_group_id": _normalize_requested_group_id(request.get("requested_group_id")),
        "delivery": "sent" if should_send else "cached",
    }, request, (current_code if should_send else None)


def verify_bot_code_login(request_id: str, browser_token: str, code: str):
    request = get_web_login_request(request_id)
    if not request:
        return {"status": "expired"}
    if str(request.get("mode") or "") != "bot_code" or not isinstance(request.get("bound_user"), dict):
        return {"status": "forbidden"}
    expected_browser_token = str(request.get("browser_token") or "")
    if not expected_browser_token or not hmac.compare_digest(expected_browser_token, str(browser_token or "")):
        return {"status": "forbidden"}
    if int(request.get("code_expires_at") or 0) <= int(time.time()):
        kv_del(_web_login_key(request_id))
        return {"status": "expired"}
    expected_code = str(request.get("code") or "").strip()
    provided_code = str(code or "").strip()
    if not expected_code or not provided_code or not hmac.compare_digest(expected_code, provided_code):
        attempts = int(request.get("attempts") or 0) + 1
        request["attempts"] = attempts
        if attempts >= WEB_LOGIN_CODE_MAX_ATTEMPTS:
            kv_del(_web_login_key(request_id))
            return {"status": "expired"}
        _save_web_login_request(request)
        return {"status": "invalid_code", "remaining_attempts": max(0, WEB_LOGIN_CODE_MAX_ATTEMPTS - attempts)}
    user = dict(request.get("bound_user") or {})
    kv_del(_web_login_key(request_id))
    return {"status": "approved", "user": user, "requested_group_id": _normalize_requested_group_id(request.get("requested_group_id"))}
