import base64
import hashlib
import hmac
import ipaddress
import json
import os
import time
from http.cookies import SimpleCookie

from ..models.config import BOT_TOKEN

SESSION_COOKIE_NAME = "tg_admin_session"
SESSION_MAX_AGE_SEC = int(os.environ.get("WEB_SESSION_MAX_AGE_SEC", str(7 * 24 * 60 * 60)))
SESSION_SECRET = (os.environ.get("WEB_SESSION_SECRET", "").strip() or BOT_TOKEN or "local-web-secret").encode("utf-8")
COOKIE_SECURE = os.environ.get("WEB_COOKIE_SECURE", "0").strip() == "1"


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def _b64decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode((data + padding).encode("utf-8"))


def _sign(value: str) -> str:
    return hmac.new(SESSION_SECRET, value.encode("utf-8"), hashlib.sha256).hexdigest()


def _telegram_check_string(payload: dict) -> str:
    parts = []
    for key in sorted(payload.keys()):
        if key == "hash":
            continue
        value = payload.get(key)
        if value is None:
            continue
        parts.append(f"{key}={value}")
    return "\n".join(parts)


def verify_telegram_login(payload: dict, max_age_sec: int = 86400) -> bool:
    if not BOT_TOKEN:
        return False
    provided_hash = str(payload.get("hash") or "").strip().lower()
    if not provided_hash:
        return False
    try:
        auth_date = int(payload.get("auth_date") or 0)
    except Exception:
        return False
    now_ts = int(time.time())
    if auth_date <= 0 or auth_date < now_ts - max_age_sec:
        return False
    check_string = _telegram_check_string(payload)
    secret = hashlib.sha256(BOT_TOKEN.encode("utf-8")).digest()
    expected = hmac.new(secret, check_string.encode("utf-8"), hashlib.sha256).hexdigest().lower()
    return hmac.compare_digest(expected, provided_hash)


def is_loopback_client(host: str | None) -> bool:
    value = str(host or "").strip()
    if not value:
        return False
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return value in {"localhost", "::1"}


def build_local_debug_login(user_id: int) -> dict:
    now_ts = int(time.time())
    return {
        "id": int(user_id),
        "username": "local_debug",
        "first_name": "Local Debug",
        "last_name": "",
        "auth_date": now_ts,
    }

def issue_session(payload: dict) -> str:
    now_ts = int(time.time())
    data = {
        "id": int(payload.get("id") or 0),
        "username": str(payload.get("username") or ""),
        "first_name": str(payload.get("first_name") or ""),
        "last_name": str(payload.get("last_name") or ""),
        "auth_date": int(payload.get("auth_date") or now_ts),
        "iat": now_ts,
        "exp": now_ts + SESSION_MAX_AGE_SEC,
    }
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    token = _b64encode(raw.encode("utf-8"))
    return f"{token}.{_sign(token)}"


def read_session(token: str | None) -> dict | None:
    if not token or "." not in token:
        return None
    raw_token, signature = token.rsplit(".", 1)
    if not hmac.compare_digest(_sign(raw_token), signature):
        return None
    try:
        payload = json.loads(_b64decode(raw_token).decode("utf-8"))
    except Exception:
        return None
    try:
        if int(payload.get("exp") or 0) < int(time.time()):
            return None
    except Exception:
        return None
    if int(payload.get("id") or 0) <= 0:
        return None
    return payload


def cookie_header_for_session(token: str) -> str:
    parts = [
        f"{SESSION_COOKIE_NAME}={token}",
        "Path=/",
        f"Max-Age={SESSION_MAX_AGE_SEC}",
        "HttpOnly",
        "SameSite=Lax",
    ]
    if COOKIE_SECURE:
        parts.append("Secure")
    return "; ".join(parts)


def cookie_header_for_logout() -> str:
    parts = [
        f"{SESSION_COOKIE_NAME}=",
        "Path=/",
        "Max-Age=0",
        "HttpOnly",
        "SameSite=Lax",
    ]
    if COOKIE_SECURE:
        parts.append("Secure")
    return "; ".join(parts)


def read_session_from_cookie_header(cookie_header: str | None) -> dict | None:
    if not cookie_header:
        return None
    cookie = SimpleCookie()
    try:
        cookie.load(cookie_header)
    except Exception:
        return None
    morsel = cookie.get(SESSION_COOKIE_NAME)
    if not morsel:
        return None
    return read_session(morsel.value)
