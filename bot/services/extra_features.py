from __future__ import annotations

import asyncio
import datetime
import hashlib
import html
import json
import logging
import random
import re
import time
from decimal import Decimal, InvalidOperation

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import TelegramError

from ..storage.config_store import get_group_auto_warn, get_group_config, get_known_groups, save_group_config
from ..storage.kv import kv_get_json, kv_set_json
from ..utils.message import get_message_text, message_has_link
from ..utils.telegram import build_buttons, safe_answer, send_rich_message
from ..utils.template import render_template
from .membership import should_process_scheduled_group
from .verify import sweep_expired_verify_sessions
from .welcome import process_welcome_queue

logger = logging.getLogger(__name__)

TRON_SUN_PER_TRX = Decimal("1000000")
BINANCE_PAYMENT_MAP = {
    "alipay": "ALIPAY",
    "bank": "BANK",
    "wechat": "WECHAT",
}
OKX_PAYMENT_MAP = {
    "alipay": "aliPay",
    "bank": "bank",
    "wechat": "wxPay",
}


def _schedule_key(group_id: int) -> str:
    return f"schedule:{int(group_id)}"


def _invite_stats_key(group_id: int, user_id: int) -> str:
    return f"invite:{int(group_id)}:{int(user_id)}"


def _invite_pending_key(group_id: int, user_id: int) -> str:
    return f"invite_pending:{int(group_id)}:{int(user_id)}"


def _profile_key(group_id: int, user_id: int) -> str:
    return f"member_profile:{int(group_id)}:{int(user_id)}"


def _profile_changes_key(group_id: int) -> str:
    return f"profile_changes:{int(group_id)}"


def _lottery_key(group_id: int, lottery_id: str) -> str:
    return f"lottery:{int(group_id)}:{str(lottery_id).strip()}"


def _lottery_active_key(group_id: int) -> str:
    return f"lottery_active:{int(group_id)}"

def _pow10(decimals: int | str | None) -> Decimal:
    try:
        return Decimal(10) ** int(decimals or 0)
    except Exception:
        return Decimal(1)


def _to_decimal(value) -> Decimal:
    if value is None:
        return Decimal("0")
    try:
        return Decimal(str(value).strip() or "0")
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")


def _format_amount(value, digits: int = 2) -> str:
    amount = _to_decimal(value)
    quant = Decimal(1) / (Decimal(10) ** max(0, int(digits)))
    try:
        normalized = amount.quantize(quant)
    except Exception:
        normalized = amount
    text = format(normalized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _short_addr(address: str, head: int = 6, tail: int = 4) -> str:
    text = str(address or "").strip()
    if len(text) <= head + tail + 3:
        return text
    return f"{text[:head]}...{text[-tail:]}"


def _wallet_asset_line(symbol: str, amount, usd=None, suffix: str = "") -> str:
    line = f"{symbol}: {_format_amount(amount, 6)}"
    if usd not in (None, ""):
        line += f" (${_format_amount(usd, 2)})"
    if suffix:
        line += f" {suffix}"
    return line


def _looks_like_wallet_address(value: str) -> bool:
    text = str(value or "").strip()
    return bool(re.fullmatch(r"T[1-9A-HJ-NP-Za-km-z]{33}", text) or re.fullmatch(r"0x[a-fA-F0-9]{40}", text))


def _classify_wallet_address(value: str) -> str | None:
    text = str(value or "").strip()
    if re.fullmatch(r"T[1-9A-HJ-NP-Za-km-z]{33}", text):
        return "tron"
    if re.fullmatch(r"0x[a-fA-F0-9]{40}", text):
        return "evm"
    return None


RICH_BUTTON_CALLBACK_PREFIXES = {
    "invite_notify": "ivb",
    "related_comment": "rcb",
}


def _normalize_schedule_buttons(buttons) -> list[dict]:
    rows = []
    for item in list(buttons or []):
        if not isinstance(item, dict):
            continue
        try:
            row = max(0, int(item.get("row", 0) or 0))
        except (TypeError, ValueError):
            row = 0
        rows.append(
            {
                "text": str(item.get("text") or "Button"),
                "type": str(item.get("type") or "url"),
                "value": str(item.get("value") or ""),
                "row": row,
            }
        )
    return rows


def _normalize_message_payload(data: dict | None) -> dict:
    payload = dict(data or {})
    return {
        "text": str(payload.get("text") or ""),
        "photo_file_id": str(payload.get("photo_file_id") or ""),
        "buttons": _normalize_schedule_buttons(payload.get("buttons") or []),
    }


async def _send_group_rich_message(context, chat_id: int, payload: dict, callback_prefix: str, text: str):
    markup = build_buttons(payload.get("buttons") or [], chat_id, callback_prefix) if payload.get("buttons") else None
    return await send_rich_message(
        bot=context.bot,
        chat_id=chat_id,
        text=text or "",
        photo=str(payload.get("photo_file_id") or ""),
        reply_markup=markup,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


async def _reply_rich_message(message, payload: dict, callback_prefix: str, text: str):
    chat_id = getattr(message, "chat_id", None) or getattr(getattr(message, "chat", None), "id", 0)
    reply_markup = build_buttons(payload.get("buttons") or [], chat_id, callback_prefix) if payload.get("buttons") else None
    if payload.get("photo_file_id"):
        return await message.reply_photo(
            payload.get("photo_file_id"),
            caption=text or " ",
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup,
        )
    return await message.reply_text(
        text or " ",
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup,
        disable_web_page_preview=True,
    )


def _normalize_schedule_item(item: dict, default_next_at: int | None = None) -> dict:
    data = dict(item or {})
    try:
        interval_sec = int(data.get("interval_sec", 0) or 0)
    except (TypeError, ValueError):
        interval_sec = 0
    if interval_sec <= 0:
        try:
            interval_minutes = int(data.get("interval_minutes", data.get("minutes", 0)) or 0)
        except (TypeError, ValueError):
            interval_minutes = 0
        interval_sec = interval_minutes * 60
    interval_sec = max(60, interval_sec)
    try:
        next_at = int(data.get("next_at", 0) or 0)
    except (TypeError, ValueError):
        next_at = 0
    if next_at <= 0:
        next_at = int(default_next_at or time.time()) + interval_sec
    try:
        item_id = int(data.get("id") or int(time.time() * 1000))
    except (TypeError, ValueError):
        item_id = int(time.time() * 1000)
    return {
        "id": item_id,
        "text": str(data.get("text") or ""),
        "photo_file_id": str(data.get("photo_file_id") or ""),
        "buttons": _normalize_schedule_buttons(data.get("buttons") or []),
        "interval_sec": interval_sec,
        "next_at": next_at,
        "enabled": bool(data.get("enabled", True)),
    }


def parse_schedule_message_input(raw_text: str, photo_file_id: str = "") -> dict:
    text_value = (raw_text or "").strip()
    if text_value.startswith("{") and text_value.endswith("}"):
        try:
            data = json.loads(text_value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON format error: {exc.msg}") from exc
        if not isinstance(data, dict):
            raise ValueError("Schedule message JSON must be an object")
    else:
        if "|" not in text_value:
            raise ValueError("Expected 'message text | interval minutes' or a JSON object")
        raw_body, raw_minutes = [part.strip() for part in text_value.split("|", 1)]
        if not raw_body and not photo_file_id:
            raise ValueError("Schedule message content cannot be empty")
        try:
            minutes = int(raw_minutes)
        except ValueError as exc:
            raise ValueError("Interval minutes must be an integer") from exc
        data = {"text": raw_body, "interval_sec": max(60, minutes * 60)}
    if photo_file_id and not data.get("photo_file_id"):
        data["photo_file_id"] = photo_file_id
    item = _normalize_schedule_item(data, default_next_at=int(time.time()))
    if not item.get("text") and not item.get("photo_file_id") and not item.get("buttons"):
        raise ValueError("Schedule message requires text, photo, or buttons")
    return item


def load_schedule_items(group_id: int) -> list[dict]:
    items = kv_get_json(_schedule_key(group_id), []) or []
    return [_normalize_schedule_item(item) for item in items if isinstance(item, dict)]


def save_schedule_items(group_id: int, items: list[dict]):
    normalized = [_normalize_schedule_item(item) for item in list(items or []) if isinstance(item, dict)]
    kv_set_json(_schedule_key(group_id), normalized)


async def process_scheduled_messages(context, chat_id: int):
    items = load_schedule_items(chat_id)
    if not items:
        return
    now_ts = int(time.time())
    changed = False
    for item in items:
        if not item.get("enabled", True):
            continue
        next_at = int(item.get("next_at", 0) or 0)
        interval_sec = int(item.get("interval_sec", 0) or 0)
        if interval_sec <= 0 or next_at <= 0 or next_at > now_ts:
            continue
        rows: dict[int, list[InlineKeyboardButton]] = {}
        for idx, button in enumerate(item.get("buttons") or []):
            row = int(button.get("row", 0) or 0)
            rows.setdefault(row, [])
            if str(button.get("type") or "url") == "url":
                rows[row].append(InlineKeyboardButton(str(button.get("text") or "按钮"), url=str(button.get("value") or "")))
            else:
                rows[row].append(InlineKeyboardButton(str(button.get("text") or "按钮"), callback_data=f"smb:{item.get('id')}:{chat_id}:{idx}"))
        markup = InlineKeyboardMarkup([rows[key] for key in sorted(rows)]) if rows else None
        sent = False
        try:
            if item.get("photo_file_id"):
                await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=item.get("photo_file_id"),
                    caption=item.get("text") or " ",
                    parse_mode=ParseMode.HTML,
                    reply_markup=markup,
                )
            else:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=item.get("text") or " ",
                    parse_mode=ParseMode.HTML,
                    reply_markup=markup,
                    disable_web_page_preview=True,
                )
            sent = True
        except TelegramError as exc:
            logger.warning("scheduled message failed: %s", exc)
        if not sent:
            continue
        item["next_at"] = now_ts + interval_sec
        changed = True
    if changed:
        save_schedule_items(chat_id, items)


async def sweep_group_maintenance(context, group_id: int, sweep_tick: int = 0):
    try:
        await process_welcome_queue(context, group_id)
    except Exception as exc:
        logger.warning("welcome_queue_sweep_failed group=%s: %s", group_id, exc)
    try:
        await sweep_expired_verify_sessions(context, group_id)
    except Exception as exc:
        logger.warning("verify_sweep_failed group=%s: %s", group_id, exc)
    if not should_process_scheduled_group(group_id, sweep_tick):
        return
    try:
        await process_scheduled_messages(context, group_id)
    except Exception as exc:
        logger.warning("scheduled_message_sweep_failed group=%s: %s", group_id, exc)


async def sweep_known_group_maintenance(context, sweep_tick: int = 0):
    for group in get_known_groups() or []:
        try:
            group_id = int(group.get("id") or 0)
        except (AttributeError, TypeError, ValueError):
            continue
        if not group_id:
            continue
        await sweep_group_maintenance(context, group_id, sweep_tick=sweep_tick)


async def scheduled_message_worker(context, interval_sec: int = 5):
    tick = 0
    while True:
        try:
            await sweep_known_group_maintenance(context, sweep_tick=tick)
            tick += 1
        except Exception as exc:
            logger.warning("scheduled message sweep failed: %s", exc)
        await asyncio.sleep(max(1, int(interval_sec)))

async def _fetch_json(method: str, url: str, **kwargs) -> dict:
    timeout = kwargs.pop("timeout", 15)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        response = await client.request(method.upper(), url, **kwargs)
        response.raise_for_status()
        data = response.json()
    return data if isinstance(data, dict) else {}


async def _fetch_tron_trc20_assets(address: str):
    data = await _fetch_json("GET", f"https://api.trongrid.io/v1/accounts/{address}/transactions/trc20", params={"limit": 20})
    return data.get("data") or []


def _iter_tron_assets(rows) -> list[tuple[str, Decimal]]:
    items = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        key = str(row.get("key") or row.get("name") or row.get("id") or "").strip()
        amount = _to_decimal(row.get("value") or row.get("amount") or 0)
        if key:
            items.append((key, amount))
    return items


async def fetch_wallet_summary(address: str) -> str:
    chain = _classify_wallet_address(address)
    if chain == "tron":
        return f"Chain: TRON\nAddress: {_short_addr(address, 8, 6)}"
    if chain == "evm":
        return f"Chain: EVM\nAddress: {_short_addr(address, 8, 6)}"
    return "Unsupported wallet address"


SPOT_QUOTE_ASSETS = ("USDT", "FDUSD", "USDC", "BTC", "ETH", "BNB", "TRY", "EUR", "USD")


def _normalize_spot_symbol(symbol: str, default_quote: str = "USDT") -> str:
    text = re.sub(r"[^A-Z0-9]", "", str(symbol or "").upper())
    if not text:
        return ""
    for quote in SPOT_QUOTE_ASSETS:
        if text.endswith(quote) and len(text) > len(quote):
            return text
    return f"{text}{default_quote}"


def _split_spot_symbol(symbol: str) -> tuple[str, str]:
    text = _normalize_spot_symbol(symbol)
    for quote in SPOT_QUOTE_ASSETS:
        if text.endswith(quote) and len(text) > len(quote):
            return text[: -len(quote)], quote
    return text, ""


def _format_percent(value) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "0.00%"
    sign = "+" if number > 0 else ""
    return f"{sign}{number:.2f}%"


async def fetch_spot_summary(symbol: str) -> str:
    pair = _normalize_spot_symbol(symbol)
    if not pair:
        return "Invalid symbol."
    try:
        data = await _fetch_json("GET", "https://api.binance.com/api/v3/ticker/24hr", params={"symbol": pair})
    except Exception as exc:
        logger.warning("spot_summary_failed symbol=%s: %s", pair, exc)
        return f"Price query failed for {pair}."
    if not data or "lastPrice" not in data:
        return f"No spot data for {pair}."
    base, quote = _split_spot_symbol(pair)
    lines = [
        f"{base}/{quote}",
        f"Last: {_format_amount(data.get('lastPrice'), 6)} {quote}",
        f"24h: {_format_percent(data.get('priceChangePercent'))}",
        f"High/Low: {_format_amount(data.get('highPrice'), 6)} / {_format_amount(data.get('lowPrice'), 6)} {quote}",
        f"Volume: {_format_amount(data.get('volume'), 2)} {base}",
    ]
    return "\n".join(lines)


def _average_decimals(values) -> Decimal:
    rows = [value for value in list(values or []) if _to_decimal(value) > 0]
    if not rows:
        return Decimal("0")
    total = sum((_to_decimal(item) for item in rows), Decimal("0"))
    return total / Decimal(len(rows))


def _parse_decimal_amount(value: str):
    text = str(value or "").strip().replace(",", "")
    if not text:
        return None
    amount = _to_decimal(text)
    if amount <= 0:
        return None
    return amount


def _is_exact_alias(text: str, alias: str) -> bool:
    alias_text = str(alias or "").strip()
    return bool(alias_text and str(text or "").strip().casefold() == alias_text.casefold())


def _extract_alias_amount(text: str, alias: str):
    alias_text = str(alias or "").strip()
    if not alias_text:
        return None
    match = re.fullmatch(rf"{re.escape(alias_text)}\s*([0-9][0-9,]*(?:\.[0-9]+)?)", str(text or "").strip(), re.I)
    if not match:
        return None
    return _parse_decimal_amount(match.group(1))


async def _fetch_binance_usdt_prices(limit: int = 5) -> list[Decimal]:
    payload = {
        "asset": "USDT",
        "fiat": "CNY",
        "merchantCheck": False,
        "page": 1,
        "payTypes": [],
        "publisherType": None,
        "rows": max(1, int(limit or 5)),
        "tradeType": "SELL",
    }
    data = await _fetch_json(
        "POST",
        "https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/adv/search",
        json=payload,
    )
    prices = []
    for row in data.get("data") or []:
        adv = row.get("adv") if isinstance(row, dict) else {}
        price = _to_decimal((adv or {}).get("price"))
        if price > 0:
            prices.append(price)
    return prices


async def _fetch_okx_usdt_prices(limit: int = 5) -> list[Decimal]:
    data = await _fetch_json(
        "GET",
        "https://www.okx.com/v3/c2c/tradingOrders/books",
        params={
            "quoteCurrency": "cny",
            "baseCurrency": "usdt",
            "side": "sell",
            "paymentMethod": "all",
            "userType": "all",
            "showTrade": "false",
            "showFollow": "false",
            "showAlreadyTraded": "false",
            "isAbleFilter": "false",
            "receivingAds": "false",
            "t": int(time.time() * 1000),
        },
    )
    rows = []
    if isinstance(data.get("data"), dict):
        rows = data.get("data", {}).get("sell") or data.get("data", {}).get("list") or []
    elif isinstance(data.get("data"), list):
        rows = data.get("data") or []
    elif isinstance(data.get("sell"), list):
        rows = data.get("sell") or []
    prices = []
    for row in list(rows)[: max(1, int(limit or 5))]:
        price = _to_decimal((row or {}).get("price") or (row or {}).get("priceValue"))
        if price > 0:
            prices.append(price)
    return prices


async def _fetch_htx_usdt_prices(limit: int = 5) -> list[Decimal]:
    data = await _fetch_json(
        "GET",
        "https://www.htx.com/-/x/otc/v1/data/trade-market",
        params={
            "coinId": 2,
            "currency": 1,
            "tradeType": "sell",
            "currPage": 1,
            "payMethod": 0,
            "acceptOrder": 0,
            "blockType": "general",
            "online": 1,
            "range": 0,
            "amount": "",
        },
    )
    rows = data.get("data") or []
    prices = []
    for row in list(rows)[: max(1, int(limit or 5))]:
        price = _to_decimal((row or {}).get("price"))
        if price > 0:
            prices.append(price)
    return prices


async def fetch_usdt_price_snapshot(usdt_cfg: dict | None = None) -> dict:
    cfg = dict(usdt_cfg or {})
    exchange_order = [str(item or "").strip().lower() for item in (cfg.get("exchanges") or ["binance", "okx", "htx"])]
    fetchers = {
        "binance": _fetch_binance_usdt_prices,
        "okx": _fetch_okx_usdt_prices,
        "htx": _fetch_htx_usdt_prices,
    }
    rows = []
    for exchange in exchange_order:
        fetcher = fetchers.get(exchange)
        if not fetcher:
            continue
        try:
            prices = await fetcher(limit=5)
        except Exception as exc:
            logger.warning("usdt_price_fetch_failed exchange=%s: %s", exchange, exc)
            continue
        if not prices:
            continue
        rows.append(
            {
                "exchange": exchange,
                "best_price": min(prices),
                "avg_price": _average_decimals(prices),
                "samples": len(prices),
            }
        )
    if not rows:
        return {"reference_price": Decimal("0"), "rows": []}
    tier = str(cfg.get("tier") or "best").strip().lower()
    if tier == "best":
        reference_price = min((row["best_price"] for row in rows), default=Decimal("0"))
    else:
        reference_price = _average_decimals([row["avg_price"] for row in rows])
    return {"reference_price": reference_price, "rows": rows}


async def fetch_usdt_price_summary(usdt_cfg: dict | None = None) -> str:
    snapshot = await fetch_usdt_price_snapshot(usdt_cfg)
    price = _to_decimal(snapshot.get("reference_price"))
    if price <= 0:
        return "USDT price is currently unavailable."
    lines = ["USDT CNY", f"Reference: {_format_amount(price, 4)} CNY/USDT"]
    for row in snapshot.get("rows") or []:
        label = str(row.get("exchange") or "").upper()
        lines.append(f"{label}: {_format_amount(row.get('best_price'), 4)}")
    return "\n".join(lines)


async def _handle_usdt_commands(message, cfg: dict) -> bool:
    usdt_cfg = cfg.get("usdt_price", {}) or {}
    if not usdt_cfg.get("enabled"):
        return False
    text = (get_message_text(message) or "").strip()
    if not text:
        return False

    query_alias = str(usdt_cfg.get("alias_z") or "").strip()
    if usdt_cfg.get("show_query_message", True) and _is_exact_alias(text, query_alias):
        await message.reply_text(await fetch_usdt_price_summary(usdt_cfg))
        return True

    if not usdt_cfg.get("show_calc_message", True):
        return False

    cny_alias = str(usdt_cfg.get("alias_w") or "").strip()
    usdt_alias = str(usdt_cfg.get("alias_k") or "").strip()
    cny_amount = _extract_alias_amount(text, cny_alias)
    usdt_amount = _extract_alias_amount(text, usdt_alias)
    if cny_amount is None and usdt_amount is None:
        return False

    snapshot = await fetch_usdt_price_snapshot(usdt_cfg)
    price = _to_decimal(snapshot.get("reference_price"))
    if price <= 0:
        await message.reply_text("USDT price is currently unavailable.")
        return True

    if cny_amount is not None:
        approx_usdt = cny_amount / price
        await message.reply_text(
            f"{_format_amount(cny_amount, 2)} CNY ~= {_format_amount(approx_usdt, 4)} USDT\n"
            f"Ref: {_format_amount(price, 4)} CNY/USDT"
        )
        return True

    approx_cny = usdt_amount * price
    await message.reply_text(
        f"{_format_amount(usdt_amount, 4)} USDT ~= {_format_amount(approx_cny, 2)} CNY\n"
        f"Ref: {_format_amount(price, 4)} CNY/USDT"
    )
    return True

async def _apply_invite_success(context, chat, member, invite_cfg: dict, inviter_id: int):
    if inviter_id == member.id:
        return
    increment_invite_stats(chat.id, inviter_id)
    reward_points = int(invite_cfg.get("reward_points", 0) or 0)
    if reward_points > 0:
        add_points(chat.id, inviter_id, reward_points)
    if invite_cfg.get("notify_enabled"):
        payload = _normalize_message_payload(
            {
                "text": invite_cfg.get("notify_text") or "{userName} joined via invite",
                "photo_file_id": invite_cfg.get("notify_photo_file_id") or "",
                "buttons": invite_cfg.get("notify_buttons") or [],
            }
        )
        try:
            rendered = render_template(payload.get("text", ""), member, chat)
            await _send_group_rich_message(
                context,
                chat.id,
                payload,
                RICH_BUTTON_CALLBACK_PREFIXES["invite_notify"],
                rendered,
            )
        except TelegramError as exc:
            logger.warning("invite notify failed: %s", exc)


async def handle_invite_join_request(context, chat, join_request):
    invite_cfg = get_group_config(chat.id).get("invite_links", {}) or {}
    if not invite_cfg.get("enabled") or not invite_cfg.get("join_review"):
        return
    invite_link = getattr(join_request, "invite_link", None)
    name = str(getattr(invite_link, "name", "") or "")
    if not name.startswith("inv:"):
        return
    try:
        inviter_id = int(name.split(":", 1)[1])
    except Exception:
        return
    user = getattr(join_request, "from_user", None)
    if not user:
        return
    kv_set_json(_invite_pending_key(chat.id, user.id), {"inviter_id": inviter_id, "requested_at": int(time.time())})


async def handle_new_member_features(context, chat, member, message):
    cfg = get_group_config(chat.id)
    invite_cfg = cfg.get("invite_links", {}) or {}
    if not invite_cfg.get("enabled"):
        return
    inviter_id = 0
    invite_link = getattr(message, "invite_link", None)
    name = str(getattr(invite_link, "name", "") or "")
    if name.startswith("inv:"):
        try:
            inviter_id = int(name.split(":", 1)[1])
        except Exception:
            inviter_id = 0
    if not inviter_id:
        pending = kv_get_json(_invite_pending_key(chat.id, member.id), None) or {}
        inviter_id = int(pending.get("inviter_id", 0) or 0)
    if inviter_id > 0 and inviter_id != member.id:
        await _apply_invite_success(context, chat, member, invite_cfg, inviter_id)


async def track_member_profile(context, chat, user):
    del context
    cfg = get_group_config(chat.id).get("member_watch", {}) or {}
    if not cfg.get("nickname_change_detect"):
        return
    old = kv_get_json(_profile_key(chat.id, user.id), None) or {}
    current = {"full_name": user.full_name or "", "username": getattr(user, "username", None) or ""}
    kv_set_json(_profile_key(chat.id, user.id), current)
    if not old:
        return
    changes = []
    if str(old.get("full_name") or "") != current["full_name"]:
        changes.append(f"Nickname: {old.get('full_name') or '-'} -> {current['full_name'] or '-'}")
    old_username = str(old.get("username") or "")
    new_username = current["username"]
    if old_username != new_username:
        left = f"@{old_username}" if old_username else "-"
        right = f"@{new_username}" if new_username else "-"
        changes.append(f"@username: {left} -> {right}")
    if not changes:
        return
    rows = kv_get_json(_profile_changes_key(chat.id), []) or []
    rows.append({"user_id": user.id, "changes": changes, "at": int(time.time())})
    kv_set_json(_profile_changes_key(chat.id), rows[-50:])
    if cfg.get("nickname_change_notice"):
        mention = f'<a href="tg://user?id={user.id}">{(user.full_name or str(user.id)).replace("<", "&lt;").replace(">", "&gt;")}</a>'
        text = "\n".join([f"馃摑 {mention} member profile changed", *changes])
        try:
            await getattr(chat, "send_message", None)(text, parse_mode=ParseMode.HTML)
        except Exception:
            pass


def _is_related_channel_forward(message) -> bool:
    return bool(getattr(message, "is_automatic_forward", False))


async def handle_related_channel_message(context, message, chat) -> bool:
    cfg = get_group_config(chat.id).get("related_channel", {}) or {}
    pinned_message = getattr(message, "pinned_message", None)
    if cfg.get("cancel_top_pin") and pinned_message and _is_related_channel_forward(pinned_message):
        handled = False
        try:
            await context.bot.unpin_chat_message(chat_id=chat.id, message_id=pinned_message.message_id)
            handled = True
        except TelegramError:
            try:
                await context.bot.unpin_chat_message(chat_id=chat.id)
                handled = True
            except TelegramError:
                pass
        try:
            await context.bot.delete_message(chat_id=chat.id, message_id=message.message_id)
            handled = True
        except TelegramError:
            pass
        return handled
    if cfg.get("occupy_comment") and _is_related_channel_forward(message):
        payload = _normalize_message_payload(
            {
                "text": cfg.get("occupy_comment_text") or "Occupy comment",
                "photo_file_id": cfg.get("occupy_comment_photo_file_id") or "",
                "buttons": cfg.get("occupy_comment_buttons") or [],
            }
        )
        try:
            rendered = render_template(payload.get("text", ""), None, chat)
            await _reply_rich_message(
                message,
                payload,
                RICH_BUTTON_CALLBACK_PREFIXES["related_comment"],
                rendered,
            )
            return True
        except TelegramError as exc:
            logger.warning("related comment reply failed: %s", exc)
            return False
    return False

def _points_key(group_id: int, user_id: int) -> str:
    return f"points:{int(group_id)}:{int(user_id)}"


def _points_users_key(group_id: int) -> str:
    return f"points_users:{int(group_id)}"


def _activity_key(group_id: int, user_id: int) -> str:
    return f"activity:{int(group_id)}:{int(user_id)}"


def _activity_users_key(group_id: int) -> str:
    return f"activity_users:{int(group_id)}"


def _invite_users_key(group_id: int) -> str:
    return f"invite_users:{int(group_id)}"


def _counter_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _day_stamp(now_ts: int | None = None) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(int(now_ts or time.time())))


def _month_stamp(now_ts: int | None = None) -> str:
    return time.strftime("%Y-%m", time.localtime(int(now_ts or time.time())))


def _normalize_user_index(rows) -> list[int]:
    result = []
    seen = set()
    for item in list(rows or []):
        try:
            user_id = int(item)
        except (TypeError, ValueError):
            continue
        if user_id <= 0 or user_id in seen:
            continue
        seen.add(user_id)
        result.append(user_id)
    return result


def _touch_user_index(key: str, user_id: int):
    rows = _normalize_user_index(kv_get_json(key, []) or [])
    uid = _counter_int(user_id)
    if uid <= 0 or uid in rows:
        return rows
    rows.append(uid)
    kv_set_json(key, rows)
    return rows


def _normalize_points_profile(data: dict | None) -> dict:
    payload = dict(data or {})
    return {
        "balance": _counter_int(payload.get("balance"), 0),
        "last_sign_day": str(payload.get("last_sign_day") or ""),
        "sign_count": _counter_int(payload.get("sign_count"), 0),
    }


def _load_points_profile(group_id: int, user_id: int) -> dict:
    return _normalize_points_profile(kv_get_json(_points_key(group_id, user_id), {}) or {})


def _save_points_profile(group_id: int, user_id: int, data: dict):
    kv_set_json(_points_key(group_id, user_id), _normalize_points_profile(data))


def _normalize_activity_stats(data: dict | None, now_ts: int | None = None) -> dict:
    payload = dict(data or {})
    current_day = _day_stamp(now_ts)
    current_month = _month_stamp(now_ts)
    today_count = _counter_int(payload.get("today"), 0)
    month_count = _counter_int(payload.get("month"), 0)
    if str(payload.get("day_stamp") or "") != current_day:
        today_count = 0
    if str(payload.get("month_stamp") or "") != current_month:
        month_count = 0
    return {
        "today": today_count,
        "month": month_count,
        "total": _counter_int(payload.get("total"), 0),
        "day_stamp": current_day,
        "month_stamp": current_month,
    }


def _load_activity_stats(group_id: int, user_id: int, persist: bool = False) -> dict:
    key = _activity_key(group_id, user_id)
    raw = kv_get_json(key, {}) or {}
    normalized = _normalize_activity_stats(raw)
    if persist and normalized != raw:
        kv_set_json(key, normalized)
    return normalized


def _save_activity_stats(group_id: int, user_id: int, data: dict):
    kv_set_json(_activity_key(group_id, user_id), _normalize_activity_stats(data))


def _normalize_invite_stats(data: dict | None, now_ts: int | None = None) -> dict:
    payload = dict(data or {})
    current_day = _day_stamp(now_ts)
    current_month = _month_stamp(now_ts)
    today_count = _counter_int(payload.get("today"), 0)
    month_count = _counter_int(payload.get("month"), 0)
    if str(payload.get("day_stamp") or "") != current_day:
        today_count = 0
    if str(payload.get("month_stamp") or "") != current_month:
        month_count = 0
    return {
        "today": today_count,
        "month": month_count,
        "total": _counter_int(payload.get("total"), 0),
        "day_stamp": current_day,
        "month_stamp": current_month,
    }


def _load_invite_stats(group_id: int, user_id: int, persist: bool = False) -> dict:
    key = _invite_stats_key(group_id, user_id)
    raw = kv_get_json(key, {}) or {}
    normalized = _normalize_invite_stats(raw)
    if persist and normalized != raw:
        kv_set_json(key, normalized)
    return normalized


def _save_invite_stats(group_id: int, user_id: int, data: dict):
    kv_set_json(_invite_stats_key(group_id, user_id), _normalize_invite_stats(data))


def _user_link(user_id: int, label: str | None = None) -> str:
    safe_label = html.escape(str(label or user_id))
    return f'<a href="tg://user?id={int(user_id)}">{safe_label}</a>'


async def _resolve_user_label(context, chat_id: int, user_id: int) -> str:
    try:
        member = await context.bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        resolved_user = getattr(member, "user", None)
        if resolved_user is not None:
            label = getattr(resolved_user, "full_name", None) or getattr(resolved_user, "username", None) or user_id
            return _user_link(user_id, label)
    except Exception:
        pass
    return _user_link(user_id, f"User {user_id}")


def _normalize_command_value(value: str) -> str:
    token = str(value or "").strip()
    if token.startswith("/"):
        token = token[1:]
    token = token.split(None, 1)[0] if token else ""
    if "@" in token:
        token = token.split("@", 1)[0]
    return token.casefold()


def _extract_command_token(text: str) -> str:
    value = str(text or "").strip()
    if not value:
        return ""
    token = value.split(None, 1)[0]
    if token.startswith("/"):
        token = token[1:]
    if "@" in token:
        token = token.split("@", 1)[0]
    return token.casefold()


def _matches_command(text: str, configured: str) -> bool:
    expected = _normalize_command_value(configured)
    actual = _extract_command_token(text)
    return bool(expected and actual and actual == expected)


def add_points(group_id: int, user_id: int, delta: int):
    profile = _load_points_profile(group_id, user_id)
    profile["balance"] = _counter_int(profile.get("balance"), 0) + _counter_int(delta, 0)
    _save_points_profile(group_id, user_id, profile)
    _touch_user_index(_points_users_key(group_id), user_id)
    return profile["balance"]


def _increment_activity_stats(group_id: int, user_id: int):
    stats = _load_activity_stats(group_id, user_id, persist=True)
    stats["today"] = _counter_int(stats.get("today"), 0) + 1
    stats["month"] = _counter_int(stats.get("month"), 0) + 1
    stats["total"] = _counter_int(stats.get("total"), 0) + 1
    _save_activity_stats(group_id, user_id, stats)
    _touch_user_index(_activity_users_key(group_id), user_id)
    return stats


def increment_invite_stats(group_id: int, user_id: int):
    stats = _load_invite_stats(group_id, user_id, persist=True)
    stats["today"] = _counter_int(stats.get("today"), 0) + 1
    stats["month"] = _counter_int(stats.get("month"), 0) + 1
    stats["total"] = _counter_int(stats.get("total"), 0) + 1
    _save_invite_stats(group_id, user_id, stats)
    _touch_user_index(_invite_users_key(group_id), user_id)
    return stats


async def _delete_message(context, message) -> bool:
    bot = getattr(context, "bot", None)
    chat_id = getattr(getattr(message, "chat", None), "id", None) or getattr(message, "chat_id", None)
    message_id = getattr(message, "message_id", None)
    if bot is None or not chat_id or not message_id:
        return False
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
        return True
    except TelegramError as exc:
        logger.warning("delete_message_failed chat=%s message=%s: %s", chat_id, message_id, exc)
        return False


async def _delete_message_later(bot, chat_id: int, message_id: int, delay_sec: int):
    await asyncio.sleep(max(1, _counter_int(delay_sec, 0)))
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except TelegramError:
        return


async def _send_html_message(context, chat_id: int, text: str, delete_after_sec: int = 0, reply_to_message_id: int | None = None):
    kwargs = {
        "chat_id": chat_id,
        "text": text or " ",
        "parse_mode": ParseMode.HTML,
        "disable_web_page_preview": True,
    }
    if reply_to_message_id:
        kwargs["reply_to_message_id"] = reply_to_message_id
    sent = await context.bot.send_message(**kwargs)
    if delete_after_sec > 0:
        asyncio.create_task(_delete_message_later(context.bot, chat_id, sent.message_id, delete_after_sec))
    return sent


def parse_lottery_input(raw_text: str) -> dict:
    text = str(raw_text or "").strip()
    if not text:
        raise ValueError("Lottery content cannot be empty")
    winner_count = 1
    title = text
    if "|" in text:
        raw_title, raw_count = [part.strip() for part in text.split("|", 1)]
        if not raw_title:
            raise ValueError("Lottery title cannot be empty")
        title = raw_title
        try:
            winner_count = max(1, int(raw_count or 1))
        except ValueError as exc:
            raise ValueError("Winner count must be an integer") from exc
    return {"title": title, "winner_count": winner_count}


def _normalize_lottery(lottery: dict | None) -> dict | None:
    if not isinstance(lottery, dict) or not lottery:
        return None
    return {
        "id": str(lottery.get("id") or ""),
        "title": str(lottery.get("title") or "Lottery"),
        "winner_count": max(1, _counter_int(lottery.get("winner_count"), 1)),
        "creator_id": _counter_int(lottery.get("creator_id"), 0),
        "participants": _normalize_user_index(lottery.get("participants") or []),
        "winners": _normalize_user_index(lottery.get("winners") or []),
        "message_id": _counter_int(lottery.get("message_id"), 0),
        "created_at": _counter_int(lottery.get("created_at"), int(time.time())),
        "closed": bool(lottery.get("closed")),
        "drawn_at": _counter_int(lottery.get("drawn_at"), 0),
    }


def _load_lottery(group_id: int, lottery_id: str):
    return _normalize_lottery(kv_get_json(_lottery_key(group_id, lottery_id), None))


def _save_lottery(group_id: int, lottery: dict):
    normalized = _normalize_lottery(lottery)
    if normalized is None or not normalized.get("id"):
        return False
    return kv_set_json(_lottery_key(group_id, normalized["id"]), normalized)


def get_active_lottery(group_id: int):
    lottery_id = str(kv_get_json(_lottery_active_key(group_id), "") or "").strip()
    if not lottery_id:
        return None
    lottery = _load_lottery(group_id, lottery_id)
    if lottery is None or lottery.get("closed"):
        kv_set_json(_lottery_active_key(group_id), "")
        return None
    return lottery


def _lottery_markup(chat_id: int, lottery: dict):
    if lottery.get("closed"):
        return None
    lottery_id = str(lottery.get("id") or "")
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("🎟️ 参与抽奖", callback_data=f"lottery:join:{chat_id}:{lottery_id}"),
            InlineKeyboardButton("🎁 立即开奖", callback_data=f"lottery:draw:{chat_id}:{lottery_id}"),
        ]]
    )


def _lottery_text(lottery: dict, winner_labels: list[str] | None = None) -> str:
    normalized = _normalize_lottery(lottery) or {}
    lines = [
        "Lottery",
        f"Prize: {html.escape(str(normalized.get('title') or 'Lottery'))}",
        f"Winners: {int(normalized.get('winner_count') or 1)}",
        f"Participants: {len(normalized.get('participants') or [])}",
        f"Status: {'Closed' if normalized.get('closed') else 'Open'}",
    ]
    labels = list(winner_labels or [])
    if normalized.get("closed") and labels:
        lines.append("Winner list:")
        for index, label in enumerate(labels, start=1):
            lines.append(f"{index}. {label}")
    return "\n".join(lines)


async def publish_lottery(context, chat_id: int, creator_id: int, raw_text: str) -> dict:
    active = get_active_lottery(chat_id)
    if active is not None:
        raise ValueError("An active lottery already exists")
    payload = parse_lottery_input(raw_text)
    lottery = {
        "id": str(int(time.time() * 1000)),
        "title": payload["title"],
        "winner_count": payload["winner_count"],
        "creator_id": int(creator_id),
        "participants": [],
        "winners": [],
        "message_id": 0,
        "created_at": int(time.time()),
        "closed": False,
        "drawn_at": 0,
    }
    sent = await context.bot.send_message(
        chat_id=chat_id,
        text=_lottery_text(lottery),
        parse_mode=ParseMode.HTML,
        reply_markup=_lottery_markup(chat_id, lottery),
        disable_web_page_preview=True,
    )
    lottery["message_id"] = getattr(sent, "message_id", 0) or 0
    _save_lottery(chat_id, lottery)
    kv_set_json(_lottery_active_key(chat_id), lottery["id"])
    cfg = get_group_config(chat_id).get("lottery", {}) or {}
    if cfg.get("pin_post") and lottery["message_id"]:
        try:
            await context.bot.pin_chat_message(chat_id=chat_id, message_id=lottery["message_id"], disable_notification=True)
        except TelegramError:
            pass
    return lottery


async def _handle_lottery_commands(context, message, chat, cfg: dict) -> bool:
    lottery_cfg = cfg.get("lottery", {}) or {}
    if not lottery_cfg.get("enabled"):
        return False
    text = (get_message_text(message) or "").strip()
    if not text or not _matches_command(text, lottery_cfg.get("query_command", "")):
        return False
    lottery = get_active_lottery(chat.id)
    rendered = _lottery_text(lottery) if lottery else "No active lottery."
    await _send_html_message(
        context,
        chat.id,
        rendered,
        delete_after_sec=max(0, _counter_int(lottery_cfg.get("auto_delete_sec"), 0)),
        reply_to_message_id=getattr(message, "message_id", None),
    )
    return True

def _gomoku_key(group_id: int, game_id: str) -> str:
    return f"gomoku:{int(group_id)}:{str(game_id).strip()}"


def _gomoku_active_key(group_id: int) -> str:
    return f"gomoku_active:{int(group_id)}"


def _empty_gomoku_board(size: int = 8) -> list[list[int]]:
    return [[0 for _ in range(size)] for _ in range(size)]


def _normalize_gomoku_board(board, size: int) -> list[list[int]]:
    rows = []
    for raw_row in list(board or [])[:size]:
        values = []
        for cell in list(raw_row or [])[:size]:
            token = _counter_int(cell, 0)
            values.append(token if token in (1, 2) else 0)
        values.extend([0] * max(0, size - len(values)))
        rows.append(values[:size])
    while len(rows) < size:
        rows.append([0] * size)
    return rows[:size]


def _normalize_gomoku_game(game: dict | None) -> dict | None:
    if not isinstance(game, dict) or not game:
        return None
    size = max(5, min(8, _counter_int(game.get("size"), 8)))
    status = str(game.get("status") or "waiting")
    if status not in {"waiting", "playing", "finished", "draw", "stopped"}:
        status = "waiting"
    turn = _counter_int(game.get("turn"), 1)
    if turn not in (1, 2):
        turn = 1
    return {
        "id": str(game.get("id") or ""),
        "creator_id": _counter_int(game.get("creator_id"), 0),
        "challenger_id": _counter_int(game.get("challenger_id"), 0),
        "size": size,
        "board": _normalize_gomoku_board(game.get("board") or [], size),
        "turn": turn,
        "status": status,
        "winner_id": _counter_int(game.get("winner_id"), 0),
        "message_id": _counter_int(game.get("message_id"), 0),
        "created_at": _counter_int(game.get("created_at"), int(time.time())),
        "started_at": _counter_int(game.get("started_at"), 0),
        "finished_at": _counter_int(game.get("finished_at"), 0),
        "last_move": list(game.get("last_move") or []),
    }


def _load_gomoku_game(group_id: int, game_id: str):
    return _normalize_gomoku_game(kv_get_json(_gomoku_key(group_id, game_id), None))


def _save_gomoku_game(group_id: int, game: dict):
    normalized = _normalize_gomoku_game(game)
    if normalized is None or not normalized.get("id"):
        return False
    return kv_set_json(_gomoku_key(group_id, normalized["id"]), normalized)


def get_active_gomoku_game(group_id: int):
    game_id = str(kv_get_json(_gomoku_active_key(group_id), "") or "").strip()
    if not game_id:
        return None
    game = _load_gomoku_game(group_id, game_id)
    if game is None or game.get("status") not in {"waiting", "playing"}:
        kv_set_json(_gomoku_active_key(group_id), "")
        return None
    return game


def _gomoku_piece(value: int) -> str:
    return {1: "X", 2: "O"}.get(int(value or 0), ".")


def _gomoku_turn_user_id(game: dict) -> int:
    return int(game.get("creator_id") or 0) if int(game.get("turn") or 1) == 1 else int(game.get("challenger_id") or 0)


def _gomoku_text(
    game: dict,
    creator_label: str | None = None,
    challenger_label: str | None = None,
    turn_label: str | None = None,
    winner_label: str | None = None,
) -> str:
    normalized = _normalize_gomoku_game(game) or {}
    creator_id = int(normalized.get("creator_id") or 0)
    challenger_id = int(normalized.get("challenger_id") or 0)
    board = normalized.get("board") or []
    size = int(normalized.get("size") or 8)
    header = "  " + " ".join(str(idx + 1) for idx in range(size))
    board_lines = [f"{row_idx + 1} " + " ".join(_gomoku_piece(cell) for cell in row) for row_idx, row in enumerate(board)]
    black_label = creator_label or _user_link(creator_id, f"User {creator_id}")
    white_label = challenger_label or (_user_link(challenger_id, f"User {challenger_id}") if challenger_id else "Waiting for player")
    lines = [
        "Gomoku",
        f"Black: {black_label}",
        f"White: {white_label}",
    ]
    status = str(normalized.get("status") or "waiting")
    if status == "waiting":
        lines.append("Status: waiting for another player")
    elif status == "playing":
        current_turn = turn_label or _user_link(_gomoku_turn_user_id(normalized), f"User {_gomoku_turn_user_id(normalized)}")
        lines.append(f"Turn: {current_turn}")
    elif status == "finished":
        label = winner_label or _user_link(int(normalized.get("winner_id") or 0), f"User {int(normalized.get('winner_id') or 0)}")
        lines.append(f"Winner: {label}")
    elif status == "draw":
        lines.append("Result: draw")
    else:
        lines.append("Status: stopped")
    lines.append("<code>" + "\n".join([header, *board_lines]) + "</code>")
    return "\n".join(lines)


def _gomoku_markup(chat_id: int, game: dict):
    normalized = _normalize_gomoku_game(game) or {}
    status = str(normalized.get("status") or "waiting")
    game_id = str(normalized.get("id") or "")
    if not game_id:
        return None
    if status == "waiting":
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🎮 加入对局", callback_data=f"gomoku:join:{chat_id}:{game_id}"),
                InlineKeyboardButton("🛑 结束对局", callback_data=f"gomoku:stop:{chat_id}:{game_id}"),
            ]
        ])
    if status != "playing":
        return None
    rows = []
    board = normalized.get("board") or []
    for y, row in enumerate(board):
        row_buttons = []
        for x, cell in enumerate(row):
            if int(cell or 0) == 0:
                data = f"gomoku:move:{chat_id}:{game_id}:{x}:{y}"
            else:
                data = f"gomoku:noop:{chat_id}:{game_id}:{x}:{y}"
            row_buttons.append(InlineKeyboardButton(_gomoku_piece(cell), callback_data=data))
        rows.append(row_buttons)
    rows.append([InlineKeyboardButton("🛑 结束对局", callback_data=f"gomoku:stop:{chat_id}:{game_id}")])
    return InlineKeyboardMarkup(rows)


def _gomoku_has_five(board: list[list[int]], x: int, y: int, piece: int) -> bool:
    size = len(board)
    for dx, dy in ((1, 0), (0, 1), (1, 1), (1, -1)):
        count = 1
        for direction in (1, -1):
            nx = x + dx * direction
            ny = y + dy * direction
            while 0 <= nx < size and 0 <= ny < size and int(board[ny][nx] or 0) == piece:
                count += 1
                nx += dx * direction
                ny += dy * direction
        if count >= 5:
            return True
    return False


def _gomoku_board_full(board: list[list[int]]) -> bool:
    return all(int(cell or 0) in (1, 2) for row in list(board or []) for cell in list(row or []))


async def _gomoku_label(context, chat_id: int, user_id: int) -> str:
    if user_id <= 0:
        return "Waiting for player"
    return await _resolve_user_label(context, chat_id, user_id)


async def publish_gomoku_game(context, chat_id: int, creator_id: int) -> dict:
    active = get_active_gomoku_game(chat_id)
    if active is not None:
        raise ValueError("A Gomoku game is already active")
    game = {
        "id": str(int(time.time() * 1000)),
        "creator_id": int(creator_id),
        "challenger_id": 0,
        "size": 8,
        "board": _empty_gomoku_board(8),
        "turn": 1,
        "status": "waiting",
        "winner_id": 0,
        "message_id": 0,
        "created_at": int(time.time()),
        "started_at": 0,
        "finished_at": 0,
        "last_move": [],
    }
    sent = await context.bot.send_message(
        chat_id=chat_id,
        text=_gomoku_text(game),
        parse_mode=ParseMode.HTML,
        reply_markup=_gomoku_markup(chat_id, game),
        disable_web_page_preview=True,
    )
    game["message_id"] = getattr(sent, "message_id", 0) or 0
    _save_gomoku_game(chat_id, game)
    kv_set_json(_gomoku_active_key(chat_id), game["id"])
    return game


async def _handle_dice_commands(context, message, user, chat, cfg: dict) -> bool:
    fun_cfg = cfg.get("entertainment", {}) or {}
    if not fun_cfg.get("dice_enabled"):
        return False
    text = (get_message_text(message) or "").strip()
    if not text:
        return False
    configured = str(fun_cfg.get("dice_command") or "/dice")
    normalized = text.casefold()
    if not (_matches_command(text, configured) or normalized in {"dice", "??", "???"}):
        return False
    cost = max(0, _counter_int(fun_cfg.get("dice_cost"), 0))
    points_cfg = cfg.get("points", {}) or {}
    if cost > 0 and points_cfg.get("enabled"):
        profile = _load_points_profile(chat.id, user.id)
        balance = _counter_int(profile.get("balance"), 0)
        if balance < cost:
            await _send_html_message(
                context,
                chat.id,
                f"{getattr(user, 'mention_html', lambda: html.escape(str(getattr(user, 'id', 'user'))))()} needs <b>{cost}</b> points to roll the dice.",
                reply_to_message_id=getattr(message, "message_id", None),
            )
            return True
        add_points(chat.id, user.id, -cost)
    await context.bot.send_dice(
        chat_id=chat.id,
        emoji="??",
        reply_to_message_id=getattr(message, "message_id", None),
    )
    return True


async def _handle_gomoku_commands(context, message, user, chat, cfg: dict) -> bool:
    fun_cfg = cfg.get("entertainment", {}) or {}
    if not fun_cfg.get("gomoku_enabled"):
        return False
    text = (get_message_text(message) or "").strip()
    if not text:
        return False
    configured = str(fun_cfg.get("gomoku_command") or "/gomoku")
    normalized = text.casefold()
    if not (_matches_command(text, configured) or normalized in {"gomoku", "???"}):
        return False
    active = get_active_gomoku_game(chat.id)
    if active is not None:
        await _send_html_message(
            context,
            chat.id,
            "A Gomoku game is already active in this group.",
            reply_to_message_id=getattr(message, "message_id", None),
        )
        return True
    await publish_gomoku_game(context, chat.id, user.id)
    return True


def _top_user_scores(user_ids, score_loader, limit: int = 10):
    rows = []
    for user_id in _normalize_user_index(user_ids):
        score = _counter_int(score_loader(user_id), 0)
        if score <= 0:
            continue
        rows.append((user_id, score))
    rows.sort(key=lambda item: (-item[1], item[0]))
    return rows[: max(1, int(limit or 10))]


async def _render_ranking_text(context, chat_id: int, title: str, rows, unit: str = "") -> str:
    lines = [title]
    if not rows:
        lines.append("暂无数据")
        return "\n".join(lines)
    suffix = f" {unit}" if unit else ""
    for index, (user_id, score) in enumerate(rows, start=1):
        label = await _resolve_user_label(context, chat_id, user_id)
        lines.append(f"{index}. {label} - <b>{score}</b>{suffix}")
    return "\n".join(lines)


def _should_award_chat_points(message, min_text_length: int) -> bool:
    text = (get_message_text(message) or "").strip()
    if text and not text.startswith("/"):
        return len(text) >= max(0, _counter_int(min_text_length, 0))
    return bool(
        getattr(message, "photo", None)
        or getattr(message, "video", None)
        or getattr(message, "document", None)
        or getattr(message, "voice", None)
        or getattr(message, "audio", None)
    )


async def _handle_command_gate(context, message, chat, is_admin_user: bool, cfg: dict) -> bool:
    if is_admin_user:
        return False
    gate_cfg = cfg.get("command_gate", {}) or {}
    text = (get_message_text(message) or "").strip()
    if not text.startswith("/"):
        return False
    command = _extract_command_token(text)
    if not command or not gate_cfg.get(command, False):
        return False
    if await _delete_message(context, message):
        return True
    await _send_html_message(
        context,
        chat.id,
        f"/{command} is disabled in this group.",
        delete_after_sec=10,
        reply_to_message_id=getattr(message, "message_id", None),
    )
    return True

async def _handle_points_commands(context, message, user, chat, cfg: dict) -> bool:
    points_cfg = cfg.get("points", {}) or {}
    if not points_cfg.get("enabled"):
        return False
    text = (get_message_text(message) or "").strip()
    if not text:
        return False
    reply_to_message_id = getattr(message, "message_id", None)

    if _matches_command(text, points_cfg.get("sign_command", "")):
        profile = _load_points_profile(chat.id, user.id)
        today = _day_stamp()
        if profile.get("last_sign_day") == today:
            rendered = f"{user.mention_html()} 今天已经签到过了。\n当前积分：<b>{profile.get('balance', 0)}</b>"
        else:
            gain = max(0, _counter_int(points_cfg.get("sign_points"), 5))
            profile["balance"] = _counter_int(profile.get("balance"), 0) + gain
            profile["last_sign_day"] = today
            profile["sign_count"] = _counter_int(profile.get("sign_count"), 0) + 1
            _save_points_profile(chat.id, user.id, profile)
            _touch_user_index(_points_users_key(chat.id), user.id)
            rendered = f"{user.mention_html()} 签到成功，获得 <b>{gain}</b> 积分。\n当前积分：<b>{profile.get('balance', 0)}</b>"
        await _send_html_message(context, chat.id, rendered, reply_to_message_id=reply_to_message_id)
        return True

    if _matches_command(text, points_cfg.get("query_command", "")):
        profile = _load_points_profile(chat.id, user.id)
        sign_status = "已签到" if profile.get("last_sign_day") == _day_stamp() else "未签到"
        rendered = f"{user.mention_html()} 的积分\n当前积分：<b>{profile.get('balance', 0)}</b>\n今日签到：{sign_status}"
        await _send_html_message(context, chat.id, rendered, reply_to_message_id=reply_to_message_id)
        return True

    if _matches_command(text, points_cfg.get("rank_command", "")):
        rows = _top_user_scores(
            kv_get_json(_points_users_key(chat.id), []) or [],
            lambda member_id: _load_points_profile(chat.id, member_id).get("balance", 0),
        )
        rendered = await _render_ranking_text(context, chat.id, "积分排行", rows, unit="分")
        await _send_html_message(context, chat.id, rendered, reply_to_message_id=reply_to_message_id)
        return True

    return False


async def _handle_activity_commands(context, message, user, chat, cfg: dict) -> bool:
    del user
    activity_cfg = cfg.get("activity", {}) or {}
    if not activity_cfg.get("enabled", True):
        return False
    text = (get_message_text(message) or "").strip()
    if not text:
        return False
    reply_to_message_id = getattr(message, "message_id", None)
    mappings = [
        (activity_cfg.get("today_command", ""), "today", "今日活跃排行"),
        (activity_cfg.get("month_command", ""), "month", "本月活跃排行"),
        (activity_cfg.get("total_command", ""), "total", "总活跃排行"),
    ]
    for configured, field, title in mappings:
        if not _matches_command(text, configured):
            continue
        rows = _top_user_scores(
            kv_get_json(_activity_users_key(chat.id), []) or [],
            lambda member_id: _load_activity_stats(chat.id, member_id, persist=True).get(field, 0),
        )
        rendered = await _render_ranking_text(context, chat.id, title, rows, unit="条")
        await _send_html_message(context, chat.id, rendered, reply_to_message_id=reply_to_message_id)
        return True
    return False


async def _handle_invite_commands(context, message, user, chat, is_admin_user: bool, cfg: dict) -> bool:
    invite_cfg = cfg.get("invite_links", {}) or {}
    if not invite_cfg.get("enabled"):
        return False
    text = (get_message_text(message) or "").strip()
    if not text:
        return False
    reply_to_message_id = getattr(message, "message_id", None)
    auto_delete_sec = max(0, _counter_int(invite_cfg.get("auto_delete_sec"), 0))

    if _matches_command(text, invite_cfg.get("query_command", "")):
        stats = _load_invite_stats(chat.id, user.id, persist=True)
        rendered = "\n".join(
            [
                f"{user.mention_html()} 的邀请数据",
                f"今日：<b>{stats.get('today', 0)}</b>",
                f"本月：<b>{stats.get('month', 0)}</b>",
                f"总计：<b>{stats.get('total', 0)}</b>",
            ]
        )
        await _send_html_message(
            context,
            chat.id,
            rendered,
            delete_after_sec=auto_delete_sec,
            reply_to_message_id=reply_to_message_id,
        )
        return True

    mappings = [
        (invite_cfg.get("today_rank_command", ""), "today", "今日邀请排行"),
        (invite_cfg.get("month_rank_command", ""), "month", "本月邀请排行"),
        (invite_cfg.get("total_rank_command", ""), "total", "总邀请排行"),
    ]
    for configured, field, title in mappings:
        if not _matches_command(text, configured):
            continue
        if invite_cfg.get("only_admin_can_query_rank") and not is_admin_user:
            await _send_html_message(
                context,
                chat.id,
                "仅管理员可查看邀请排行。",
                delete_after_sec=auto_delete_sec,
                reply_to_message_id=reply_to_message_id,
            )
            return True
        rows = _top_user_scores(
            kv_get_json(_invite_users_key(chat.id), []) or [],
            lambda member_id: _load_invite_stats(chat.id, member_id, persist=True).get(field, 0),
        )
        rendered = await _render_ranking_text(context, chat.id, title, rows, unit="人")
        await _send_html_message(
            context,
            chat.id,
            rendered,
            delete_after_sec=auto_delete_sec,
            reply_to_message_id=reply_to_message_id,
        )
        return True
    return False


AD_KEYWORDS = (
    "推广",
    "兼职",
    "刷单",
    "博彩",
    "赌博",
    "代发",
    "返佣",
    "收群",
    "买群",
    "加我",
    "联系我",
    "客服",
    "赚钱",
    "vx",
    "wechat",
    "telegram",
    "飞机",
)


def _contains_ad_keywords(value: str) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return False
    if any(keyword in text for keyword in AD_KEYWORDS):
        return True
    if re.search(r"(https?://|t\.me/|tg://|www\.)", text, re.I):
        return True
    if re.search(r"(q[q号]?\s*[:：]?\s*\d{5,}|v[x信]?\s*[:：]?\s*[a-z0-9_-]{5,})", text, re.I):
        return True
    return False


def _looks_like_ad_identity(user) -> bool:
    if user is None:
        return False
    return _contains_ad_keywords(getattr(user, "full_name", "")) or _contains_ad_keywords(getattr(user, "username", ""))


def _looks_like_ad_message(message) -> bool:
    if _contains_ad_keywords(get_message_text(message)):
        return True
    if message_has_link(message):
        return True
    document = getattr(message, "document", None)
    if document and _contains_ad_keywords(getattr(document, "file_name", "")):
        return True
    return bool(getattr(message, "contact", None))


async def handle_ad_filter(context, message, user, chat, is_admin_user: bool) -> bool:
    cfg = get_group_config(chat.id).get("ad_filter", {}) or {}
    if cfg.get("block_channel_mask") and getattr(message, "sender_chat", None) and not getattr(message, "is_automatic_forward", False):
        return await _delete_message(context, message)
    if is_admin_user:
        return False
    if cfg.get("sticker_enabled") and getattr(message, "sticker", None):
        return await _delete_message(context, message)
    if cfg.get("nickname_enabled") and _looks_like_ad_identity(user):
        return await _delete_message(context, message)
    if cfg.get("message_enabled") and _looks_like_ad_message(message):
        return await _delete_message(context, message)
    return False


LANGUAGE_PATTERNS = {
    "zh": re.compile(r"[\u4e00-\u9fff]"),
    "en": re.compile(r"[A-Za-z]"),
    "ja": re.compile(r"[\u3040-\u30ff]"),
    "ko": re.compile(r"[\uac00-\ud7af]"),
    "ru": re.compile(r"[\u0400-\u04FF]"),
    "ar": re.compile(r"[\u0600-\u06FF]"),
    "hi": re.compile(r"[\u0900-\u097F]"),
    "th": re.compile(r"[\u0E00-\u0E7F]"),
}


def _normalize_language_code(value: str) -> str:
    text = str(value or "").strip().lower()
    if text.startswith("zh"):
        return "zh"
    if text.startswith("en"):
        return "en"
    return text


def _detect_languages(text: str) -> set[str]:
    value = str(text or "")
    return {code for code, pattern in LANGUAGE_PATTERNS.items() if pattern.search(value)}


async def handle_language_whitelist(context, message, chat, is_admin_user: bool) -> bool:
    cfg = get_group_config(chat.id).get("language_whitelist", {}) or {}
    if is_admin_user or not cfg.get("enabled"):
        return False
    text = (get_message_text(message) or "").strip()
    if not text or text.startswith("/"):
        return False
    allowed = {_normalize_language_code(item) for item in (cfg.get("allowed") or []) if str(item).strip()}
    if not allowed:
        return False
    detected = _detect_languages(text)
    if not detected or detected.issubset(allowed):
        return False
    return await _delete_message(context, message)


NSFW_KEYWORDS = (
    "nsfw",
    "porn",
    "sex",
    "nude",
    "onlyfans",
    "\u6210\u4eba\u89c6\u9891",
    "\u88f8\u804a",
    "\u7ea6\u70ae",
    "\u9ec4\u8272",
    "\u60c5\u8272",
    "\u798f\u5229\u59ec",
    "\u5077\u62cd",
    "\u65e0\u7801",
)

NSFW_SOFT_KEYWORDS = (
    "adult",
    "18+",
    "sexy",
    "escort",
    "fetish",
    "\u6210\u4eba",
)



def _nsfw_samples(message) -> list[str]:
    samples = [get_message_text(message)]
    document = getattr(message, "document", None)
    if document is not None:
        samples.append(getattr(document, "file_name", ""))
    sticker = getattr(message, "sticker", None)
    if sticker is not None:
        samples.append(getattr(sticker, "set_name", ""))
        samples.append(getattr(sticker, "emoji", ""))
    return [str(sample or "").strip().lower() for sample in samples if str(sample or "").strip()]



def _contains_nsfw_keyword(value: str, keywords=NSFW_KEYWORDS) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return False
    return any(keyword in text for keyword in keywords)



def _nsfw_match_score(message) -> int:
    score = 0
    for sample in _nsfw_samples(message):
        score += sum(2 for keyword in NSFW_KEYWORDS if keyword in sample)
        score += sum(1 for keyword in NSFW_SOFT_KEYWORDS if keyword in sample)
    return score



def _nsfw_threshold(cfg) -> int:
    sensitivity = str((cfg or {}).get("sensitivity") or "medium").strip().lower()
    threshold = {"high": 1, "medium": 2, "low": 3}.get(sensitivity, 2)
    if bool((cfg or {}).get("allow_miss")):
        threshold += 1
    return threshold



def _looks_like_nsfw_message(message, cfg=None) -> bool:
    return _nsfw_match_score(message) >= _nsfw_threshold(cfg or {})


async def handle_nsfw_filter(context, message, chat, is_admin_user: bool) -> bool:
    cfg = get_group_config(chat.id).get("nsfw", {}) or {}
    if is_admin_user or not cfg.get("enabled"):
        return False
    if not _looks_like_nsfw_message(message, cfg):
        return False
    handled = await _delete_message(context, message)
    if cfg.get("notice_enabled"):
        await _send_html_message(
            context,
            chat.id,
            "Suspected NSFW content was removed.",
            delete_after_sec=max(0, _counter_int(cfg.get("delay_delete_sec"), 0)),
        )
        return True
    return handled


async def record_message_metrics(message, user, chat):
    cfg = get_group_config(chat.id)
    activity_cfg = cfg.get("activity", {}) or {}
    if activity_cfg.get("enabled", True):
        _increment_activity_stats(chat.id, user.id)

    points_cfg = cfg.get("points", {}) or {}
    if points_cfg.get("enabled") and points_cfg.get("chat_points_enabled"):
        per_message = max(0, _counter_int(points_cfg.get("chat_points_per_message"), 1))
        min_text_length = max(0, _counter_int(points_cfg.get("min_text_length"), 5))
        if per_message > 0 and _should_award_chat_points(message, min_text_length):
            add_points(chat.id, user.id, per_message)
    return None


async def _handle_crypto_commands(context, message, user, chat, is_admin_user: bool, cfg: dict) -> bool:
    del context, user, chat, is_admin_user
    text = (get_message_text(message) or "").strip()
    crypto_cfg = cfg.get("crypto", {}) or {}
    if crypto_cfg.get("wallet_query_enabled", True) and text and " " not in text and _looks_like_wallet_address(text):
        await message.reply_text(await fetch_wallet_summary(text))
        return True
    if text.startswith("/wallet "):
        await message.reply_text(await fetch_wallet_summary(text.split(None, 1)[1].strip()))
        return True

    symbol = ""
    if crypto_cfg.get("price_query_enabled", True):
        if text.startswith("/price "):
            symbol = text.split(None, 1)[1].strip()
        elif _matches_command(text, "/price"):
            symbol = str(crypto_cfg.get("default_symbol") or "BTC")
        else:
            alias = str(crypto_cfg.get("query_alias") or "").strip()
            if alias:
                if text == alias:
                    symbol = str(crypto_cfg.get("default_symbol") or "BTC")
                elif text.startswith(alias + " "):
                    symbol = text[len(alias) :].strip()
        if symbol:
            await message.reply_text(await fetch_spot_summary(symbol))
            return True
    return False


async def handle_group_commands(context, message, user, chat, is_admin_user: bool) -> bool:
    cfg = get_group_config(chat.id)
    if await _handle_command_gate(context, message, chat, is_admin_user, cfg):
        return True
    if await _handle_points_commands(context, message, user, chat, cfg):
        return True
    if await _handle_activity_commands(context, message, user, chat, cfg):
        return True
    if await _handle_invite_commands(context, message, user, chat, is_admin_user, cfg):
        return True
    if await _handle_dice_commands(context, message, user, chat, cfg):
        return True
    if await _handle_gomoku_commands(context, message, user, chat, cfg):
        return True
    if await _handle_lottery_commands(context, message, chat, cfg):
        return True
    if await _handle_usdt_commands(message, cfg):
        return True
    if await _handle_crypto_commands(context, message, user, chat, is_admin_user, cfg):
        return True
    return False


async def handle_gomoku_join_callback(update, context, chat_id: int, game_id: str):
    query = update.callback_query
    game = _load_gomoku_game(chat_id, game_id)
    if game is None:
        await safe_answer(query, "未找到对局。", show_alert=True)
        return
    if game.get("status") != "waiting":
        await safe_answer(query, "Game already started", show_alert=True)
        return
    user_id = int(query.from_user.id)
    if user_id == int(game.get("creator_id") or 0):
        await safe_answer(query, "Waiting for another player", show_alert=False)
        return
    game["challenger_id"] = user_id
    game["status"] = "playing"
    game["started_at"] = int(time.time())
    game["turn"] = 1
    _save_gomoku_game(chat_id, game)
    creator_label = await _gomoku_label(context, chat_id, int(game.get("creator_id") or 0))
    challenger_label = await _gomoku_label(context, chat_id, user_id)
    turn_label = creator_label
    try:
        await query.edit_message_text(
            _gomoku_text(game, creator_label=creator_label, challenger_label=challenger_label, turn_label=turn_label),
            parse_mode=ParseMode.HTML,
            reply_markup=_gomoku_markup(chat_id, game),
            disable_web_page_preview=True,
        )
    except TelegramError:
        pass
    await safe_answer(query, "Game started", show_alert=False)


async def handle_gomoku_move_callback(update, context, chat_id: int, game_id: str, x: int, y: int):
    query = update.callback_query
    game = _load_gomoku_game(chat_id, game_id)
    if game is None:
        await safe_answer(query, "未找到对局。", show_alert=True)
        return
    if game.get("status") != "playing":
        await safe_answer(query, "Game is not active", show_alert=True)
        return
    size = int(game.get("size") or 8)
    if not (0 <= int(x) < size and 0 <= int(y) < size):
        await safe_answer(query, "Move is out of range", show_alert=True)
        return
    user_id = int(query.from_user.id)
    creator_id = int(game.get("creator_id") or 0)
    challenger_id = int(game.get("challenger_id") or 0)
    players = {creator_id: 1, challenger_id: 2}
    piece = players.get(user_id)
    if piece is None:
        await safe_answer(query, "You are not part of this game", show_alert=True)
        return
    if int(game.get("turn") or 1) != piece:
        await safe_answer(query, "It is not your turn", show_alert=False)
        return
    board = _normalize_gomoku_board(game.get("board") or [], size)
    if int(board[int(y)][int(x)] or 0) != 0:
        await safe_answer(query, "该位置已有棋子。", show_alert=False)
        return
    board[int(y)][int(x)] = piece
    game["board"] = board
    game["last_move"] = [int(x), int(y), piece]
    creator_label = await _gomoku_label(context, chat_id, creator_id)
    challenger_label = await _gomoku_label(context, chat_id, challenger_id)
    answer_text = "落子成功"
    markup = None
    if _gomoku_has_five(board, int(x), int(y), piece):
        game["status"] = "finished"
        game["winner_id"] = user_id
        game["finished_at"] = int(time.time())
        kv_set_json(_gomoku_active_key(chat_id), "")
        answer_text = "你赢了"
        winner_label = creator_label if user_id == creator_id else challenger_label
        text = _gomoku_text(game, creator_label=creator_label, challenger_label=challenger_label, winner_label=winner_label)
    elif _gomoku_board_full(board):
        game["status"] = "draw"
        game["winner_id"] = 0
        game["finished_at"] = int(time.time())
        kv_set_json(_gomoku_active_key(chat_id), "")
        answer_text = "平局"
        text = _gomoku_text(game, creator_label=creator_label, challenger_label=challenger_label)
    else:
        game["turn"] = 2 if piece == 1 else 1
        turn_label = creator_label if int(game.get("turn") or 1) == 1 else challenger_label
        text = _gomoku_text(game, creator_label=creator_label, challenger_label=challenger_label, turn_label=turn_label)
        markup = _gomoku_markup(chat_id, game)
    _save_gomoku_game(chat_id, game)
    try:
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=markup, disable_web_page_preview=True)
    except TelegramError:
        pass
    await safe_answer(query, answer_text, show_alert=False)


async def handle_gomoku_stop_callback(update, context, chat_id: int, game_id: str):
    query = update.callback_query
    game = _load_gomoku_game(chat_id, game_id)
    if game is None:
        await safe_answer(query, "未找到对局。", show_alert=True)
        return
    user_id = int(query.from_user.id)
    players = {int(game.get("creator_id") or 0), int(game.get("challenger_id") or 0)}
    players.discard(0)
    if user_id not in players:
        await safe_answer(query, "只有对局双方可以结束对局。", show_alert=True)
        return
    game["status"] = "stopped"
    game["finished_at"] = int(time.time())
    kv_set_json(_gomoku_active_key(chat_id), "")
    _save_gomoku_game(chat_id, game)
    creator_label = await _gomoku_label(context, chat_id, int(game.get("creator_id") or 0))
    challenger_label = await _gomoku_label(context, chat_id, int(game.get("challenger_id") or 0))
    try:
        await query.edit_message_text(
            _gomoku_text(game, creator_label=creator_label, challenger_label=challenger_label),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except TelegramError:
        pass
    await safe_answer(query, "对局已结束", show_alert=False)


async def handle_gomoku_noop_callback(update, context):
    del context
    await safe_answer(update.callback_query, "该位置已有棋子。", show_alert=False)


async def handle_lottery_join_callback(update, context, chat_id: int, lottery_id: str):
    del context
    query = update.callback_query
    lottery = _load_lottery(chat_id, lottery_id)
    if lottery is None:
        await safe_answer(query, "未找到抽奖活动。", show_alert=True)
        return
    if lottery.get("closed"):
        await safe_answer(query, "抽奖活动已结束。", show_alert=True)
        return
    user_id = int(query.from_user.id)
    participants = _normalize_user_index(lottery.get("participants") or [])
    if user_id in participants:
        await safe_answer(query, "你已经参加过抽奖。", show_alert=False)
        return
    participants.append(user_id)
    lottery["participants"] = participants
    _save_lottery(chat_id, lottery)
    try:
        await query.edit_message_text(
            _lottery_text(lottery),
            parse_mode=ParseMode.HTML,
            reply_markup=_lottery_markup(chat_id, lottery),
            disable_web_page_preview=True,
        )
    except TelegramError:
        pass
    await safe_answer(query, "已加入抽奖。", show_alert=False)


async def handle_lottery_draw_callback(update, context, chat_id: int, lottery_id: str):
    query = update.callback_query
    lottery = _load_lottery(chat_id, lottery_id)
    if lottery is None:
        await safe_answer(query, "未找到抽奖活动。", show_alert=True)
        return
    if lottery.get("closed"):
        await safe_answer(query, "抽奖活动已结束。", show_alert=True)
        return
    if int(query.from_user.id) != int(lottery.get("creator_id") or 0):
        await safe_answer(query, "只有发起人可以开奖。", show_alert=True)
        return
    participants = _normalize_user_index(lottery.get("participants") or [])
    if not participants:
        await safe_answer(query, "当前还没有参与者。", show_alert=True)
        return
    winner_count = min(max(1, int(lottery.get("winner_count") or 1)), len(participants))
    winners = random.sample(participants, winner_count)
    lottery["winners"] = winners
    lottery["closed"] = True
    lottery["drawn_at"] = int(time.time())
    _save_lottery(chat_id, lottery)
    if str(kv_get_json(_lottery_active_key(chat_id), "") or "") == str(lottery_id):
        kv_set_json(_lottery_active_key(chat_id), "")
    winner_labels = [await _resolve_user_label(context, chat_id, user_id) for user_id in winners]
    try:
        await query.edit_message_text(
            _lottery_text(lottery, winner_labels=winner_labels),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except TelegramError:
        pass
    cfg = get_group_config(chat_id).get("lottery", {}) or {}
    if cfg.get("pin_result") and lottery.get("message_id"):
        try:
            await context.bot.pin_chat_message(chat_id=chat_id, message_id=lottery.get("message_id"), disable_notification=True)
        except TelegramError:
            pass
    await safe_answer(query, "开奖完成。", show_alert=False)