import json
import logging
import os
from pathlib import Path
from urllib.parse import quote

import httpx

from ..utils.env import load_local_env

load_local_env()

logger = logging.getLogger(__name__)

KV_REST_API_URL = os.environ.get("KV_REST_API_URL", "").strip()
KV_REST_API_TOKEN = os.environ.get("KV_REST_API_TOKEN", "").strip()
KV_ENABLED = bool(KV_REST_API_URL and KV_REST_API_TOKEN)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
BOT_STORAGE_NAMESPACE = os.environ.get("BOT_STORAGE_NAMESPACE", "").strip() or (BOT_TOKEN.split(":", 1)[0].strip() if BOT_TOKEN else "default")
BOT_STORAGE_PREFIX = f"bot:{BOT_STORAGE_NAMESPACE}:"

LOCAL_KV_PATH = Path(os.environ.get("LOCAL_KV_PATH", ".local_kv.json")).resolve()
_memory_store = None


def _scoped_key(key: str) -> str:
    return f"{BOT_STORAGE_PREFIX}{key}"


def _load_local_store():
    global _memory_store
    if _memory_store is not None:
        return _memory_store
    if not LOCAL_KV_PATH.exists():
        _memory_store = {}
        return _memory_store
    try:
        _memory_store = json.loads(LOCAL_KV_PATH.read_text(encoding="utf-8"))
        if not isinstance(_memory_store, dict):
            _memory_store = {}
    except Exception as exc:
        logger.warning("local_kv_load_failed: %s", exc)
        _memory_store = {}
    return _memory_store


def _save_local_store():
    store = _load_local_store()
    try:
        LOCAL_KV_PATH.write_text(json.dumps(store, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        return True
    except Exception as exc:
        logger.warning("local_kv_save_failed: %s", exc)
        return False


def kv_request(command: str, *parts: str):
    if not KV_ENABLED:
        raise RuntimeError("KV is not configured")
    base = KV_REST_API_URL.rstrip("/")
    url = f"{base}/{command}"
    for part in parts:
        url += f"/{quote(str(part), safe='')}"
    headers = {"Authorization": f"Bearer {KV_REST_API_TOKEN}"}
    return url, headers


def kv_get_json(key: str, default=None):
    scoped_key = _scoped_key(key)
    if not KV_ENABLED:
        store = _load_local_store()
        return store.get(scoped_key, default)
    try:
        url, headers = kv_request("get", scoped_key)
        r = httpx.post(url, headers=headers, timeout=10)
        r.raise_for_status()
        data = r.json()
        if not data or data.get("result") is None:
            return default
        return json.loads(data["result"])
    except Exception as exc:
        logger.warning("kv_get failed: %s", exc)
        return default


def kv_set_json(key: str, value):
    scoped_key = _scoped_key(key)
    if not KV_ENABLED:
        store = _load_local_store()
        store[scoped_key] = value
        return _save_local_store()
    try:
        url, headers = kv_request("set", scoped_key, json.dumps(value, ensure_ascii=False))
        r = httpx.post(url, headers=headers, timeout=10)
        r.raise_for_status()
        return True
    except Exception as exc:
        logger.warning("kv_set failed: %s", exc)
        return False


def kv_del(key: str):
    scoped_key = _scoped_key(key)
    if not KV_ENABLED:
        store = _load_local_store()
        store.pop(scoped_key, None)
        return _save_local_store()
    try:
        url, headers = kv_request("del", scoped_key)
        r = httpx.post(url, headers=headers, timeout=10)
        r.raise_for_status()
        return True
    except Exception as exc:
        logger.warning("kv_del failed: %s", exc)
        return False
