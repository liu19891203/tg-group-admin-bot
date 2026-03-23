import html
import datetime
import hashlib
import json
import os
import re
import sys
import time
from importlib.machinery import SourcelessFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def _append_group_id(module, url: str, group_id: int) -> str:
    parts = urlsplit(module.normalize_url(url))
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["group_id"] = str(int(group_id))
    return urlunsplit((parts.scheme, parts.netloc, parts.path or "/web/", urlencode(query), parts.fragment))


def _toggle_notice(enabled: bool) -> str:
    return "\u5df2\u6253\u5f00" if enabled else "\u5df2\u5173\u95ed"


def _load_pyc_module(module_name: str, pyc_path: Path):
    module = sys.modules.get(module_name)
    if module is not None:
        return module
    loader = SourcelessFileLoader(module_name, str(pyc_path))
    spec = spec_from_loader(module_name, loader)
    if spec is None:
        raise RuntimeError(f"Unable to load {module_name} from pyc")
    module = module_from_spec(spec)
    sys.modules[module_name] = module
    loader.exec_module(module)
    return module


def load_patched_admin():
    import bot.handlers.admin as module
    import bot.handlers.admin_extra as admin_extra

    if getattr(module, "_runtime_patch_admin_applied", False):
        return module

    import bot.handlers.callbacks as callbacks
    import bot.services.auto_warn as auto_warn_service
    import bot.services.extra_features as extra_features
    import bot.services.verify as verify_service
    import bot.utils.telegram as telegram_utils


    original_admin_callback = module.admin_callback
    original_admin_message = module.admin_message
    original_extra_callback = admin_extra.handle_admin_extra_callback
    original_module_send_or_edit = module._send_or_edit
    original_extra_send_or_edit = admin_extra._send_or_edit
    original_show_verify_menu = module.show_verify_menu
    original_show_welcome_menu = module.show_welcome_menu
    original_show_autoban_menu = module.show_autoban_menu
    original_show_automute_menu = module.show_automute_menu
    original_show_autowarn_menu = module.show_autowarn_menu
    original_show_antispam_menu = module.show_antispam_menu
    original_show_ad_filter_menu = admin_extra.show_ad_filter_menu
    original_show_command_gate_menu = admin_extra.show_command_gate_menu
    original_show_crypto_menu = admin_extra.show_crypto_menu
    original_show_invite_menu = admin_extra.show_invite_menu
    original_show_member_menu = admin_extra.show_member_menu
    original_show_related_menu = admin_extra.show_related_menu
    original_show_schedule_menu = admin_extra.show_schedule_menu
    original_show_schedule_list_menu = admin_extra.show_schedule_list_menu
    original_show_language_menu = admin_extra.show_language_menu
    original_show_verified_placeholder = admin_extra.show_verified_placeholder
    original_extra_message = admin_extra.handle_admin_extra_message
    original_admin_photo = module.admin_photo
    original_crypto_commands = extra_features._handle_crypto_commands
    original_apply_warn = auto_warn_service.apply_warn
    original_handle_verification_failure = verify_service.handle_verification_failure
    original_apply_invite_success = extra_features._apply_invite_success
    original_handle_related_channel_message = extra_features.handle_related_channel_message
    original_callback_router = callbacks.callback_router

    TRON_USDT_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
    WALLET_HISTORY_PAGE_SIZE = 10
    WALLET_CACHE_TTL_SEC = 30
    WALLET_RATE_CACHE_TTL_SEC = 60
    WALLET_HISTORY_SCAN_MAX_REQUESTS = 100
    SHANGHAI_TZ = datetime.timezone(datetime.timedelta(hours=8))
    BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    wallet_view_cache: dict[str, dict] = {}

    RICH_BUTTON_CALLBACK_PREFIXES = {
        "verify_fail": "vfb",
        "autowarn": "awb",
        "invite_notify": "ivb",
        "related_comment": "rcb",
    }

    def _preview_text(value: str, fallback: str = "\u672a\u8bbe\u7f6e", limit: int = 64) -> str:
        text = str(value or "").strip()
        if not text:
            return fallback
        if len(text) > limit:
            return text[: limit - 1] + "\u2026"
        return text

    def _normalize_message_payload(data: dict | None) -> dict:
        payload = dict(data or {})
        return {
            "text": str(payload.get("text") or ""),
            "photo_file_id": str(payload.get("photo_file_id") or ""),
            "buttons": _normalize_schedule_buttons(payload.get("buttons") or []),
        }

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

    def _get_rich_message_target(group_id: int, target: str):
        if target == "verify_fail":
            cfg = module.get_group_config(group_id)
            return _normalize_message_payload(
                {
                    "text": cfg.get("verify_fail_text") or "",
                    "photo_file_id": cfg.get("verify_fail_photo_file_id") or "",
                    "buttons": cfg.get("verify_fail_buttons") or [],
                }
            )
        if target == "autowarn":
            cfg = module.get_group_auto_warn(group_id)
            return _normalize_message_payload(
                {
                    "text": cfg.get("warn_text") or "",
                    "photo_file_id": cfg.get("warn_photo_file_id") or "",
                    "buttons": cfg.get("warn_buttons") or [],
                }
            )
        if target == "invite_notify":
            cfg = admin_extra.get_group_config(group_id).get("invite_links", {}) or {}
            return _normalize_message_payload(
                {
                    "text": cfg.get("notify_text") or "",
                    "photo_file_id": cfg.get("notify_photo_file_id") or "",
                    "buttons": cfg.get("notify_buttons") or [],
                }
            )
        if target == "related_comment":
            cfg = admin_extra.get_group_config(group_id).get("related_channel", {}) or {}
            return _normalize_message_payload(
                {
                    "text": cfg.get("occupy_comment_text") or "",
                    "photo_file_id": cfg.get("occupy_comment_photo_file_id") or "",
                    "buttons": cfg.get("occupy_comment_buttons") or [],
                }
            )
        if target.startswith("schedule:"):
            try:
                schedule_id = int(target.split(":", 1)[1])
            except (IndexError, ValueError):
                return None
            _, item, _ = _get_schedule_item_by_id(group_id, schedule_id)
            return _normalize_message_payload(item) if item else None
        return None

    def _save_rich_message_target(group_id: int, target: str, payload: dict) -> bool:
        message = _normalize_message_payload(payload)
        if target == "verify_fail":
            cfg = module.get_group_config(group_id)
            cfg["verify_fail_text"] = message["text"]
            cfg["verify_fail_photo_file_id"] = message["photo_file_id"]
            cfg["verify_fail_buttons"] = message["buttons"]
            module.save_group_config(group_id, cfg)
            return True
        if target == "autowarn":
            cfg = module.get_group_auto_warn(group_id)
            cfg["warn_text"] = message["text"]
            cfg["warn_photo_file_id"] = message["photo_file_id"]
            cfg["warn_buttons"] = message["buttons"]
            module.save_group_auto_warn(group_id, cfg)
            return True
        if target == "invite_notify":
            cfg = admin_extra.get_group_config(group_id)
            invite_cfg = dict(cfg.get("invite_links", {}) or {})
            invite_cfg["notify_text"] = message["text"]
            invite_cfg["notify_photo_file_id"] = message["photo_file_id"]
            invite_cfg["notify_buttons"] = message["buttons"]
            cfg["invite_links"] = invite_cfg
            admin_extra.save_group_config(group_id, cfg)
            return True
        if target == "related_comment":
            cfg = admin_extra.get_group_config(group_id)
            related_cfg = dict(cfg.get("related_channel", {}) or {})
            related_cfg["occupy_comment_text"] = message["text"]
            related_cfg["occupy_comment_photo_file_id"] = message["photo_file_id"]
            related_cfg["occupy_comment_buttons"] = message["buttons"]
            cfg["related_channel"] = related_cfg
            admin_extra.save_group_config(group_id, cfg)
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
            items[idx] = _normalize_schedule_item(item, default_next_at=int(item.get("next_at", 0) or time.time()))
            save_schedule_items(group_id, items)
            return True
        return False

    def _rich_target_meta(target: str) -> dict | None:
        if target == "verify_fail":
            return {
                "title": "\u9a8c\u8bc1\u5931\u8d25\u6d88\u606f\u8bbe\u7f6e",
                "back": "admin:verify",
                "prompt_module": "verify_fail",
                "hint": "\u9a8c\u8bc1\u5931\u8d25\u65f6\u53ea\u53d1\u9001\u4e00\u6761\u6d88\u606f\uff0c\u652f\u6301\u56fe\u7247\u3001\u6587\u672c\u548c\u6309\u94ae\u3002",
            }
        if target == "autowarn":
            return {
                "title": "\u81ea\u52a8\u8b66\u544a\u6d88\u606f\u8bbe\u7f6e",
                "back": "admin:autowarn",
                "prompt_module": "autowarn",
                "hint": "\u89e6\u53d1\u81ea\u52a8\u8b66\u544a\u65f6\u53ea\u53d1\u9001\u4e00\u6761\u6d88\u606f\uff0c\u652f\u6301\u56fe\u7247\u3001\u6587\u672c\u548c\u6309\u94ae\u3002",
            }
        if target == "invite_notify":
            return {
                "title": "\u9080\u8bf7\u6210\u529f\u901a\u77e5\u8bbe\u7f6e",
                "back": "adminx:invite:menu",
                "prompt_module": "invite",
                "hint": "\u6210\u5458\u901a\u8fc7\u9080\u8bf7\u5165\u7fa4\u540e\u53ea\u53d1\u9001\u4e00\u6761\u901a\u77e5\u6d88\u606f\uff0c\u652f\u6301 {userName} {group} \u6807\u7b7e\u3002",
            }
        if target == "related_comment":
            return {
                "title": "\u62a2\u5360\u8bc4\u8bba\u533a\u6d88\u606f\u8bbe\u7f6e",
                "back": "adminx:related:menu",
                "prompt_module": "related",
                "hint": "\u5173\u8054\u9891\u9053\u8f6c\u53d1\u5230\u7fa4\u540e\uff0c\u673a\u5668\u4eba\u4f1a\u5728\u8be5\u6761\u6d88\u606f\u4e0b\u65b9\u56de\u590d\u8fd9\u4e00\u6761\u5bcc\u6d88\u606f\u3002",
            }
        if target.startswith("schedule:"):
            return {
                "title": "\u5b9a\u65f6\u6d88\u606f\u7f16\u8f91",
                "back": "adminx:schedule:list",
                "prompt_module": "schedule",
                "hint": "\u5b9a\u65f6\u6d88\u606f\u4f1a\u4ee5\u5355\u6761\u6d88\u606f\u53d1\u9001\uff1a\u56fe\u7247\u5728\u9876\u90e8\uff0c\u6587\u672c\u5728\u4e2d\u95f4\uff0c\u6309\u94ae\u5728\u4e0b\u65b9\u3002",
            }
        return None

    async def show_rich_message_editor(update, context, state: dict, target: str):
        del context
        group_id = module._current_group(state) or admin_extra._group_id(state)
        meta = _rich_target_meta(target)
        message = _get_rich_message_target(group_id, target) if group_id else None
        if not meta or message is None:
            await admin_extra._send_or_edit(update, "\u6d88\u606f\u914d\u7f6e\u4e0d\u5b58\u5728\u6216\u5df2\u88ab\u5220\u9664\u3002")
            return

        lines = [
            meta["title"],
            f"\u6587\u672c: {html.escape(_preview_text(message.get('text', ''), limit=120))}",
            f"\u56fe\u7247: {'\u5df2\u8bbe\u7f6e' if message.get('photo_file_id') else '\u672a\u8bbe\u7f6e'}",
            f"\u6309\u94ae\u6570\u91cf: {len(message.get('buttons', []) or [])}",
            meta["hint"],
        ]
        rows = [
            [
                admin_extra._btn("\u8bbe\u7f6e\u6587\u672c", f"adminx:rich:text:{target}"),
                admin_extra._btn("\u8bbe\u7f6e\u56fe\u7247", f"adminx:rich:photo:{target}"),
            ],
            [
                admin_extra._btn("\u6e05\u9664\u56fe\u7247", f"adminx:rich:clear_photo:{target}"),
                admin_extra._btn("\u8bbe\u7f6e\u6309\u94ae", f"adminx:rich:buttons:{target}"),
            ],
        ]
        if target.startswith("schedule:"):
            schedule_id = target.split(":", 1)[1]
            idx, item, _ = _get_schedule_item_by_id(group_id, int(schedule_id))
            if item is not None and idx >= 0:
                interval_min = max(1, int(item.get("interval_sec", 0) or 0) // 60)
                next_at = int(item.get("next_at", 0) or 0)
                next_text = time.strftime("%m-%d %H:%M", time.localtime(next_at)) if next_at > 0 else "\u672a\u5b89\u6392"
                lines.insert(1, f"\u72b6\u6001: {_status_label(bool(item.get('enabled', True)))}")
                lines.insert(2, f"\u95f4\u9694: \u6bcf {interval_min} \u5206\u949f")
                lines.insert(3, f"\u4e0b\u6b21\u53d1\u9001: {next_text}")
            rows.append(
                [
                    admin_extra._btn("\u542f\u7528/\u505c\u7528", f"adminx:rich:schedule:toggle:{schedule_id}"),
                    admin_extra._btn("\u5220\u9664", f"adminx:rich:schedule:delete:{schedule_id}"),
                ]
            )
        rows.append([admin_extra._btn("\u2b05\ufe0f \u8fd4\u56de", meta["back"])])
        await admin_extra._send_or_edit(update, "\n".join(lines), admin_extra.InlineKeyboardMarkup(rows))

    def _callback_value_for_buttons(buttons, idx: int):
        buttons = _normalize_schedule_buttons(buttons or [])
        if 0 <= idx < len(buttons):
            value = str(buttons[idx].get("value") or buttons[idx].get("text") or "").strip()
            if hasattr(callbacks, "_truncate_callback_text"):
                value = callbacks._truncate_callback_text(value)
            return value or "\u7a7a\u6309\u94ae"
        return None

    async def handle_rich_message_button_callback(update, context):
        del context
        query = update.callback_query
        data = getattr(query, "data", "") or ""
        stale_hint = "\u6309\u94ae\u5df2\u8fc7\u671f\uff0c\u8bf7\u91cd\u65b0\u6253\u5f00\u6d88\u606f\u3002"
        prefix_map = {
            "vfb:": "verify_fail",
            "awb:": "autowarn",
            "ivb:": "invite_notify",
            "rcb:": "related_comment",
        }
        target = next((mapped for prefix, mapped in prefix_map.items() if data.startswith(prefix)), None)
        if not target:
            return False
        try:
            _, group_id, button_idx = data.split(":", 2)
            payload = _get_rich_message_target(int(group_id), target)
            value = _callback_value_for_buttons((payload or {}).get("buttons") or [], int(button_idx))
        except Exception:
            value = None
        await callbacks.safe_answer(query, value or stale_hint, show_alert=True)
        return True

    async def _send_group_rich_message(context, chat_id: int, payload: dict, callback_prefix: str, text: str):
        markup = telegram_utils.build_buttons(payload.get("buttons") or [], chat_id, callback_prefix) if payload.get("buttons") else None
        return await telegram_utils.send_rich_message(
            bot=context.bot,
            chat_id=chat_id,
            text=text or "",
            photo=str(payload.get("photo_file_id") or ""),
            reply_markup=markup,
            parse_mode=extra_features.ParseMode.HTML,
            disable_web_page_preview=True,
        )

    async def _reply_rich_message(message, payload: dict, callback_prefix: str, text: str):
        reply_markup = telegram_utils.build_buttons(payload.get("buttons") or [], message.chat_id, callback_prefix) if payload.get("buttons") else None
        if payload.get("photo_file_id"):
            return await message.reply_photo(
                payload.get("photo_file_id"),
                caption=text or " ",
                parse_mode=extra_features.ParseMode.HTML,
                reply_markup=reply_markup,
            )
        return await message.reply_text(
            text or " ",
            parse_mode=extra_features.ParseMode.HTML,
            reply_markup=reply_markup,
            disable_web_page_preview=True,
        )

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
                    "text": str(item.get("text") or "鎸夐挳"),
                    "type": str(item.get("type") or "url"),
                    "value": str(item.get("value") or ""),
                    "row": row,
                }
            )
        return rows

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
                raise ValueError(f"JSON 鏍煎紡閿欒: {exc.msg}") from exc
            if not isinstance(data, dict):
                raise ValueError("定时消息 JSON 必须是对象")
        else:
            if "|" not in text_value:
                raise ValueError("格式错误，请发送 `消息内容 | 间隔分钟`，或发送一个 JSON 对象")
            raw_body, raw_minutes = [part.strip() for part in text_value.split("|", 1)]
            if not raw_body and not photo_file_id:
                raise ValueError("娑堟伅鍐呭涓嶈兘涓虹┖")
            try:
                minutes = int(raw_minutes)
            except ValueError as exc:
                raise ValueError("间隔分钟必须是整数") from exc
            data = {"text": raw_body, "interval_sec": max(60, minutes * 60)}
        if photo_file_id and not data.get("photo_file_id"):
            data["photo_file_id"] = photo_file_id
        item = _normalize_schedule_item(data, default_next_at=int(time.time()))
        if not item.get("text") and not item.get("photo_file_id") and not item.get("buttons"):
            raise ValueError("定时消息至少要有文本、图片或按钮其中一项")
        return item

    def load_schedule_items(group_id: int) -> list[dict]:
        items = extra_features.kv_get_json(extra_features._schedule_key(group_id), []) or []
        return [_normalize_schedule_item(item) for item in items if isinstance(item, dict)]

    def save_schedule_items(group_id: int, items: list[dict]):
        normalized = [_normalize_schedule_item(item) for item in list(items or []) if isinstance(item, dict)]
        extra_features.kv_set_json(extra_features._schedule_key(group_id), normalized)

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
            markup = telegram_utils.build_buttons(item.get("buttons") or [], chat_id, f"smb:{item.get('id')}") if item.get("buttons") else None
            try:
                if item.get("photo_file_id"):
                    await context.bot.send_photo(
                        chat_id=chat_id,
                        photo=item.get("photo_file_id"),
                        caption=item.get("text") or " ",
                        parse_mode=extra_features.ParseMode.HTML,
                        reply_markup=markup,
                    )
                else:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=item.get("text") or " ",
                        parse_mode=extra_features.ParseMode.HTML,
                        reply_markup=markup,
                        disable_web_page_preview=True,
                    )
            except extra_features.TelegramError as exc:
                extra_features.logger.warning("scheduled message failed: %s", exc)
            item["next_at"] = now_ts + interval_sec
            changed = True
        if changed:
            save_schedule_items(chat_id, items)

    def _resolve_schedule_button(group_id: int, schedule_id: int, button_idx: int):
        items = load_schedule_items(group_id)
        for item in items:
            try:
                item_id = int(item.get("id") or 0)
            except (TypeError, ValueError):
                item_id = 0
            if item_id != schedule_id:
                continue
            buttons = item.get("buttons", []) or []
            if 0 <= button_idx < len(buttons):
                return buttons[button_idx]
            return None
        return None

    async def handle_schedule_button_callback(update, context):
        del context
        query = update.callback_query
        data = getattr(query, "data", "") or ""
        if not data.startswith("smb:"):
            return False
        stale_hint = "\u6309\u94ae\u5df2\u8fc7\u671f\uff0c\u8bf7\u91cd\u65b0\u6253\u5f00\u6d88\u606f\u3002"
        try:
            _, schedule_id, group_id, button_idx = data.split(":", 3)
            button = _resolve_schedule_button(int(group_id), int(schedule_id), int(button_idx))
        except Exception:
            await callbacks.safe_answer(query, stale_hint, show_alert=True)
            return True
        if not button:
            await callbacks.safe_answer(query, stale_hint, show_alert=True)
            return True
        value = str(button.get("value") or button.get("text") or "").strip() or "\u7a7a\u6309\u94ae"
        if hasattr(callbacks, "_truncate_callback_text"):
            value = callbacks._truncate_callback_text(value)
        await callbacks.safe_answer(query, value, show_alert=True)
        return True

    def _status_label(enabled: bool) -> str:
        return "\u5df2\u5f00\u542f" if enabled else "\u5df2\u5173\u95ed"

    def _yes_no(enabled: bool) -> str:
        return "\u662f" if enabled else "\u5426"

    def _join_labels(labels: list[str], fallback: str = "\u65e0") -> str:
        cleaned = [str(item).strip() for item in labels if str(item).strip()]
        return "\u3001".join(cleaned) if cleaned else fallback

    def _rule_preview(rules: list[dict], fallback: str = "\u65e0") -> str:
        if not rules:
            return fallback
        items = []
        for rule in rules[:3]:
            mode = str(rule.get("mode", "contains") or "contains").lower()
            mode_label = {"regex": "\u6b63\u5219", "exact": "\u7cbe\u786e", "contains": "\u5305\u542b"}.get(mode, mode)
            keyword = str(rule.get("keyword", "") or "").strip() or "\u7a7a\u89c4\u5219"
            items.append(f"[{mode_label}] {keyword}")
        if len(rules) > 3:
            items.append(f"\u7b49 {len(rules)} \u6761")
        return "\uff1b".join(items)

    def _wallet_cache_get(key: str, ttl_sec: int | float | None = None):
        item = wallet_view_cache.get(key)
        if not item:
            return None
        ttl_value = WALLET_CACHE_TTL_SEC if ttl_sec is None else float(ttl_sec)
        if time.time() - float(item.get("at", 0)) > ttl_value:
            wallet_view_cache.pop(key, None)
            return None
        return item.get("value")

    def _wallet_cache_set(key: str, value):
        wallet_view_cache[key] = {"at": time.time(), "value": value}
        return value

    def _base58check_encode(raw: bytes) -> str:
        number = int.from_bytes(raw, "big")
        chars = []
        while number > 0:
            number, remainder = divmod(number, 58)
            chars.append(BASE58_ALPHABET[remainder])
        prefix = "1" * (len(raw) - len(raw.lstrip(b"\x00")))
        body = "".join(reversed(chars)) if chars else "1"
        return prefix + body

    def _base58check_decode(text: str) -> bytes | None:
        value = str(text or "").strip()
        if not value:
            return None
        number = 0
        for ch in value:
            idx = BASE58_ALPHABET.find(ch)
            if idx < 0:
                return None
            number = number * 58 + idx
        raw = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
        prefix_zeros = len(value) - len(value.lstrip("1"))
        raw = (b"\x00" * prefix_zeros) + raw
        if len(raw) < 5:
            return None
        payload, checksum = raw[:-4], raw[-4:]
        expected = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
        if checksum != expected:
            return None
        return payload

    def _tron_hex_to_base58(value: str) -> str:
        text = str(value or "").strip().lower()
        if text.startswith("0x"):
            text = text[2:]
        if len(text) == 40:
            text = "41" + text
        if not re.fullmatch(r"41[0-9a-f]{40}", text):
            return str(value or "")
        raw = bytes.fromhex(text)
        checksum = hashlib.sha256(hashlib.sha256(raw).digest()).digest()[:4]
        return _base58check_encode(raw + checksum)

    def _is_valid_tron_address(value: str) -> bool:
        text = str(value or "").strip()
        if not re.fullmatch(r"T[1-9A-HJ-NP-Za-km-z]{33}", text):
            return False
        payload = _base58check_decode(text)
        return bool(payload and len(payload) == 21 and payload[0] == 0x41)

    def _is_valid_wallet_query_address(value: str) -> bool:
        chain = extra_features._classify_wallet_address(value)
        if chain == "tron":
            return _is_valid_tron_address(value)
        return chain is not None

    def _normalize_tron_address(value) -> str:
        text = str(value or "").strip()
        if _is_valid_tron_address(text):
            return text
        lowered = text.lower()
        if lowered.startswith("0x"):
            lowered = lowered[2:]
        if re.fullmatch(r"(41)?[0-9a-f]{40}", lowered):
            return _tron_hex_to_base58(lowered)
        return text or "-"

    def _format_wallet_time(timestamp_ms: int | float | None) -> str:
        if not timestamp_ms:
            return "\u6682\u65e0\u8bb0\u5f55"
        try:
            dt = datetime.datetime.fromtimestamp(float(timestamp_ms) / 1000, tz=SHANGHAI_TZ)
        except (TypeError, ValueError, OSError):
            return "\u6682\u65e0\u8bb0\u5f55"
        return dt.strftime("%Y-%m-%d %H:%M:%S")

    def _format_wallet_now() -> str:
        return datetime.datetime.now(SHANGHAI_TZ).strftime("%Y-%m-%d %H:%M:%S")

    def _wallet_counterparty(direction: str, owner: str, target: str) -> str:
        if direction == "out":
            return target
        if direction == "in":
            return owner
        return f"{owner} -> {target}"

    def _extract_tron_trc20_balance(account_data: dict, contract_address: str, decimals: int = 6):
        target = str(contract_address or "").strip().lower()
        rows = account_data.get("trc20") or []
        if isinstance(rows, dict):
            rows = [rows]
        for row in rows:
            if not isinstance(row, dict):
                continue
            for key, value in row.items():
                if str(key or "").strip().lower() != target:
                    continue
                raw_text = str(value if value is not None else "0").strip()
                amount = extra_features._to_decimal(raw_text)
                if amount <= 0:
                    return extra_features.Decimal("0")
                if "." in raw_text:
                    return amount
                return amount / extra_features._pow10(decimals)
        return extra_features.Decimal("0")

    def _parse_trx_transfer(address: str, row: dict) -> dict | None:
        raw_data = row.get("raw_data") or {}
        contracts = raw_data.get("contract") or []
        contract = contracts[0] if contracts else {}
        if (contract or {}).get("type") != "TransferContract":
            return None
        value = ((contract.get("parameter") or {}).get("value") or {})
        owner = _normalize_tron_address(value.get("owner_address"))
        target = _normalize_tron_address(value.get("to_address"))
        direction = "out" if owner == address else "in" if target == address else "other"
        amount = extra_features._to_decimal(value.get("amount")) / extra_features.TRON_SUN_PER_TRX
        return {
            "timestamp": int(row.get("block_timestamp") or raw_data.get("timestamp") or 0),
            "txid": str(row.get("txID") or row.get("txid") or "").strip(),
            "amount": amount,
            "direction": direction,
            "owner": owner,
            "target": target,
            "counterparty": _wallet_counterparty(direction, owner, target),
        }

    def _is_tron_multisig(account_data: dict) -> bool:
        permissions = []
        owner_permission = account_data.get("owner_permission")
        if isinstance(owner_permission, dict):
            permissions.append(owner_permission)
        permissions.extend(item for item in (account_data.get("active_permission") or []) if isinstance(item, dict))
        for permission in permissions:
            try:
                threshold = int(permission.get("threshold") or 1)
            except (TypeError, ValueError):
                threshold = 1
            keys = permission.get("keys") or []
            if threshold > 1 or len(keys) > 1:
                return True
        return False

    async def _fetch_tron_account_info(address: str) -> dict:
        data = await extra_features._fetch_json("GET", f"https://api.trongrid.io/v1/accounts/{address}")
        rows = data.get("data") or []
        return rows[0] if rows else {}

    async def _fetch_best_usdt_cny_rate() -> float | None:
        cache_key = "wallet:usdt_rate"
        cached = _wallet_cache_get(cache_key, WALLET_RATE_CACHE_TTL_SEC)
        if cached is not None:
            return cached
        rates: list[float] = []
        try:
            payload = {
                "asset": "USDT",
                "fiat": "CNY",
                "merchantCheck": False,
                "page": 1,
                "rows": 5,
                "payTypes": [extra_features.BINANCE_PAYMENT_MAP["alipay"]],
                "tradeType": "SELL",
            }
            data = await extra_features._fetch_json(
                "POST",
                "https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/adv/search",
                json=payload,
            )
            adv = ((data.get("data") or [{}])[0] or {}).get("adv") or {}
            if adv.get("price"):
                rates.append(float(adv["price"]))
        except Exception:
            pass
        try:
            data = await extra_features._fetch_json(
                "GET",
                "https://www.okx.com/v3/c2c/tradingOrders/books",
                params={"quoteCurrency": "cny", "baseCurrency": "usdt", "side": "sell", "paymentMethod": extra_features.OKX_PAYMENT_MAP["alipay"]},
            )
            row = ((data.get("data") or {}).get("sell") or [{}])[0] or {}
            if row.get("price"):
                rates.append(float(row["price"]))
        except Exception:
            pass
        try:
            htx_rate = await extra_features._fetch_htx_usdt_cny_rate()
            if htx_rate:
                rates.append(float(htx_rate))
        except Exception:
            pass
        result = min(rates) if rates else None
        _wallet_cache_set(cache_key, result)
        return result

    def _parse_usdt_transfer(address: str, row: dict) -> dict:
        decimals = 6
        token_info = row.get("token_info") or {}
        try:
            decimals = int(token_info.get("decimals") or 6)
        except (TypeError, ValueError):
            decimals = 6
        amount = extra_features._to_decimal(row.get("value")) / extra_features._pow10(decimals)
        sender = _normalize_tron_address(row.get("from"))
        target = _normalize_tron_address(row.get("to"))
        direction = "out" if sender == address else "in" if target == address else "other"
        return {
            "timestamp": int(row.get("block_timestamp") or 0),
            "txid": str(row.get("transaction_id") or "").strip(),
            "amount": amount,
            "direction": direction,
            "counterparty": _wallet_counterparty(direction, sender, target),
            "symbol": str((token_info.get("symbol") or "USDT")).strip() or "USDT",
        }

    async def _scan_tron_trx_history(address: str) -> dict:
        cache_key = f"wallet:trx:{address}"
        cached = _wallet_cache_get(cache_key)
        if cached is not None:
            return cached
        url = f"https://api.trongrid.io/v1/accounts/{address}/transactions"
        fingerprint = None
        items = []
        request_count = 0
        truncated = False
        while True:
            params = {"only_confirmed": "true", "limit": 200, "order_by": "block_timestamp,desc"}
            if fingerprint:
                params["fingerprint"] = fingerprint
            payload = await extra_features._fetch_json("GET", url, params=params)
            rows = payload.get("data") or []
            for row in rows:
                item = _parse_trx_transfer(address, row)
                if item:
                    items.append(item)
            request_count += 1
            fingerprint = ((payload.get("meta") or {}).get("fingerprint") or "").strip()
            if not rows or not fingerprint:
                break
            if request_count >= WALLET_HISTORY_SCAN_MAX_REQUESTS:
                truncated = True
                break
        return _wallet_cache_set(cache_key, {"items": items, "truncated": truncated})

    async def _scan_tron_usdt_history(address: str) -> dict:
        cache_key = f"wallet:usdt:scan:{address}"
        cached = _wallet_cache_get(cache_key)
        if cached is not None:
            return cached
        url = f"https://api.trongrid.io/v1/accounts/{address}/transactions/trc20"
        fingerprint = None
        items = []
        request_count = 0
        truncated = False
        while True:
            params = {
                "only_confirmed": "true",
                "limit": 200,
                "contract_address": TRON_USDT_CONTRACT,
            }
            if fingerprint:
                params["fingerprint"] = fingerprint
            payload = await extra_features._fetch_json("GET", url, params=params)
            rows = payload.get("data") or []
            for row in rows:
                items.append(_parse_usdt_transfer(address, row))
            request_count += 1
            fingerprint = ((payload.get("meta") or {}).get("fingerprint") or "").strip()
            if not rows or not fingerprint:
                break
            if request_count >= WALLET_HISTORY_SCAN_MAX_REQUESTS:
                truncated = True
                break
        return _wallet_cache_set(cache_key, {"items": items, "truncated": truncated})

    def _wallet_summary_markup(address: str):
        return extra_features.InlineKeyboardMarkup(
            [
                [
                    extra_features.InlineKeyboardButton("\U0001f4ca U\u8f6c\u8d26\u6d41\u6c34", callback_data=f"wallet:hist:usdt:1:{address}"),
                    extra_features.InlineKeyboardButton("\U0001f4ca TRX\u8f6c\u8d26\u6d41\u6c34", callback_data=f"wallet:hist:trx:1:{address}"),
                ]
            ]
        )

    def _wallet_history_markup(address: str, kind: str, page: int, has_prev: bool, has_next: bool):
        rows = []
        nav = []
        if has_prev:
            nav.append(extra_features.InlineKeyboardButton("\u2b05\ufe0f \u4e0a\u4e00\u9875", callback_data=f"wallet:hist:{kind}:{page - 1}:{address}"))
        if has_next:
            nav.append(extra_features.InlineKeyboardButton("\u27a1\ufe0f \u4e0b\u4e00\u9875", callback_data=f"wallet:hist:{kind}:{page + 1}:{address}"))
        if nav:
            rows.append(nav)
        rows.append([extra_features.InlineKeyboardButton("\u21a9\ufe0f \u8fd4\u56de\u6458\u8981", callback_data=f"wallet:summary:{address}")])
        return extra_features.InlineKeyboardMarkup(rows)

    async def _build_tron_wallet_summary(address: str) -> tuple[str, object]:
        cache_key = f"wallet:summary:{address}"
        cached = _wallet_cache_get(cache_key)
        if cached is not None:
            return cached
        account_result, trc20_result, trx_history_result, usdt_history_result, rate_result = await extra_features.asyncio.gather(
            _fetch_tron_account_info(address),
            extra_features._fetch_tron_trc20_assets(address),
            _scan_tron_trx_history(address),
            _scan_tron_usdt_history(address),
            _fetch_best_usdt_cny_rate(),
            return_exceptions=True,
        )

        account_data = {} if isinstance(account_result, Exception) else dict(account_result or {})
        trc20_assets = [] if isinstance(trc20_result, Exception) else list(trc20_result or [])
        trx_scan = {} if isinstance(trx_history_result, Exception) else dict(trx_history_result or {})
        usdt_scan = {} if isinstance(usdt_history_result, Exception) else dict(usdt_history_result or {})
        trx_history = list(trx_scan.get("items") or [])
        usdt_history = list(usdt_scan.get("items") or [])
        trx_truncated = bool(trx_scan.get("truncated"))
        usdt_truncated = bool(usdt_scan.get("truncated"))
        usdt_balance = _extract_tron_trc20_balance(account_data, TRON_USDT_CONTRACT, 6)
        if usdt_balance <= 0:
            for asset in trc20_assets:
                if str(asset.get("symbol") or "").strip().upper() == "USDT":
                    usdt_balance = extra_features._to_decimal(asset.get("balance"))
                    break
        trx_balance = extra_features._to_decimal(account_data.get("balance")) / extra_features.TRON_SUN_PER_TRX
        rate_value = None if isinstance(rate_result, Exception) else rate_result
        cny_value = usdt_balance * extra_features.Decimal(str(rate_value)) if rate_value else extra_features.Decimal("0")
        trx_in_count = sum(1 for item in trx_history if item.get("direction") == "in")
        trx_out_count = sum(1 for item in trx_history if item.get("direction") == "out")
        usdt_in_count = sum(1 for item in usdt_history if item.get("direction") == "in")
        usdt_out_count = sum(1 for item in usdt_history if item.get("direction") == "out")
        all_history = [*trx_history, *usdt_history]
        first_ts = min((int(item.get("timestamp") or 0) for item in all_history), default=0) or int(account_data.get("create_time") or 0)
        last_ts = max((int(item.get("timestamp") or 0) for item in all_history), default=0) or int(account_data.get("latest_opration_time") or account_data.get("latest_consume_time") or 0)
        multi_sign_enabled = _is_tron_multisig(account_data)

        lines = [
            "\u5730\u5740:",
            address,
            "",
            f"\U0001f4b0 \u5f53\u524d\u4f59\u989d: {extra_features._format_amount(usdt_balance, 2)}USDT",
            f"\u26a1 \u5f53\u524dTRX: {extra_features._format_amount(trx_balance, 6)}",
            f"\U0001f4b5 \u53c2\u8003\u4f30\u503c: {extra_features._format_amount(cny_value, 2)}\u5143\u4eba\u6c11\u5e01" if rate_value else "\U0001f4b5 \u53c2\u8003\u4f30\u503c: \u6682\u65e0\u62a5\u4ef7",
            f"\U0001f4b1 \u53c2\u8003\u6c47\u7387: {extra_features._format_amount(extra_features.Decimal(str(rate_value)), 4)}\u5143/\u679aUSDT" if rate_value else "\U0001f4b1 \u53c2\u8003\u6c47\u7387: \u6682\u65e0\u62a5\u4ef7",
            f"\u25cb TRX\u4ea4\u6613: {len(trx_history)}",
            f"\u2b07\ufe0f TRX\u8f6c\u5165: {trx_in_count}",
            f"\u2b06\ufe0f TRX\u8f6c\u51fa: {trx_out_count}",
            f"\u25cb USDT\u4ea4\u6613: {len(usdt_history)}",
            f"\u2b07\ufe0f USDT\u8f6c\u5165: {usdt_in_count}",
            f"\u2b06\ufe0f USDT\u8f6c\u51fa: {usdt_out_count}",
            "\u26a0\ufe0f \u8be5\u5730\u5740\u5df2\u542f\u7528\u591a\u7b7e" if multi_sign_enabled else "\u2705 \u8be5\u5730\u5740\u65e0\u591a\u7b7e",
            f"\u9996\u6b21\u4f7f\u7528: {_format_wallet_time(first_ts)}",
            f"\u4e0a\u6b21\u4f7f\u7528: {_format_wallet_time(last_ts)}",
            f"\u672c\u6b21\u76d1\u6d4b: {_format_wallet_now()}",
        ]
        if trx_truncated or usdt_truncated:
            scoped = []
            if trx_truncated:
                scoped.append(f"TRX\u6700\u8fd1 {len(trx_history)} \u7b14")
            if usdt_truncated:
                scoped.append(f"USDT\u6700\u8fd1 {len(usdt_history)} \u7b14")
            lines.append(f"\u6ce8: \u7edf\u8ba1\u4ec5\u57fa\u4e8e {'\uff1b'.join(scoped)} \u5df2\u786e\u8ba4\u8f6c\u8d26")
        else:
            lines.append("\u6ce8: \u7edf\u8ba1\u4ec5\u57fa\u4e8e\u5df2\u786e\u8ba4\u7684 TRX / USDT \u8f6c\u8d26")
        result = ("\n".join(lines), _wallet_summary_markup(address))
        return _wallet_cache_set(cache_key, result)

    def _format_history_line(index: int, item: dict, symbol: str) -> str:
        direction = item.get("direction")
        direction_label = "\u8f6c\u5165" if direction == "in" else "\u8f6c\u51fa" if direction == "out" else "\u5176\u4ed6"
        amount_prefix = "+" if direction == "in" else "-"
        amount = extra_features._format_amount(item.get("amount"), 6 if symbol == "TRX" else 2)
        counterparty = extra_features._short_addr(str(item.get("counterparty") or "-"), 6, 4)
        txid = extra_features._short_addr(str(item.get("txid") or "-"), 6, 4)
        return f"{index}. {_format_wallet_time(item.get('timestamp'))} {direction_label} {amount_prefix}{amount} {symbol} {counterparty} {txid}"

    async def _build_wallet_history_page(address: str, kind: str, page: int) -> tuple[str, object]:
        page = max(1, int(page or 1))
        if kind == "trx":
            scan = await _scan_tron_trx_history(address)
            history = list(scan.get("items") or [])
            truncated = bool(scan.get("truncated"))
            total = len(history)
            start = (page - 1) * WALLET_HISTORY_PAGE_SIZE
            rows = history[start:start + WALLET_HISTORY_PAGE_SIZE]
            has_next = start + WALLET_HISTORY_PAGE_SIZE < total
            symbol = "TRX"
            title = "\u3010TRX\u8f6c\u8d26\u6d41\u6c34\u3011"
            header = [
                title,
                f"\u5730\u5740: {extra_features._short_addr(address, 8, 6)}",
                f"第 {page} 页，{'已加载最近' if truncated else '共'} {total} 笔",
                "",
            ]
        else:
            scan = await _scan_tron_usdt_history(address)
            history = list(scan.get("items") or [])
            truncated = bool(scan.get("truncated"))
            total = len(history)
            start = (page - 1) * WALLET_HISTORY_PAGE_SIZE
            rows = history[start:start + WALLET_HISTORY_PAGE_SIZE]
            has_next = start + WALLET_HISTORY_PAGE_SIZE < total
            symbol = "USDT"
            title = "\u3010U\u8f6c\u8d26\u6d41\u6c34\u3011"
            header = [
                title,
                f"\u5730\u5740: {extra_features._short_addr(address, 8, 6)}",
                f"第 {page} 页，{'已加载最近' if truncated else '共'} {total} 笔",
                "",
            ]
        if not rows:
            header.append("\u6682\u65e0\u6d41\u6c34\u8bb0\u5f55")
        else:
            line_index = (page - 1) * WALLET_HISTORY_PAGE_SIZE + 1
            for item in rows:
                header.append(_format_history_line(line_index, item, symbol))
                line_index += 1
        markup = _wallet_history_markup(address, kind, page, page > 1, has_next)
        return "\n".join(header), markup

    async def _build_wallet_reply(address: str) -> tuple[str, object | None]:
        if extra_features._classify_wallet_address(address) == "tron":
            return await _build_tron_wallet_summary(address)
        return await extra_features.fetch_wallet_summary(address), None

    async def _handle_crypto_commands(message, cfg: dict) -> bool:
        crypto_cfg = cfg.get("crypto", {})
        text = (message.text or "").strip()
        alias = (crypto_cfg.get("query_alias", "\u67e5") or "\u67e5").strip()
        if crypto_cfg.get("wallet_query_enabled", True) and " " not in text and extra_features._looks_like_wallet_address(text):
            if not _is_valid_wallet_query_address(text):
                await message.reply_text("\u5730\u5740\u683c\u5f0f\u65e0\u6548\uff0c\u8bf7\u68c0\u67e5\u540e\u91cd\u8bd5\u3002")
                return True
            try:
                reply_text, markup = await _build_wallet_reply(text)
                await message.reply_text(reply_text, reply_markup=markup)
            except Exception:
                await message.reply_text("\u5730\u5740\u8d44\u4ea7\u67e5\u8be2\u5931\u8d25\uff0c\u8bf7\u7a0d\u540e\u518d\u8bd5\u3002")
            return True
        if text == alias:
            arg = crypto_cfg.get("default_symbol", "BTC")
        elif text.startswith(alias + " "):
            arg = text[len(alias):].strip() or crypto_cfg.get("default_symbol", "BTC")
        else:
            return await original_crypto_commands(message, cfg)
        if extra_features._looks_like_wallet_address(arg):
            if not crypto_cfg.get("wallet_query_enabled", True):
                await message.reply_text("\u94b1\u5305\u5730\u5740\u67e5\u8be2\u672a\u5f00\u542f\u3002")
                return True
            if not _is_valid_wallet_query_address(arg):
                await message.reply_text("\u5730\u5740\u683c\u5f0f\u65e0\u6548\uff0c\u8bf7\u68c0\u67e5\u540e\u91cd\u8bd5\u3002")
                return True
            try:
                reply_text, markup = await _build_wallet_reply(arg)
                await message.reply_text(reply_text, reply_markup=markup)
            except Exception:
                await message.reply_text("\u5730\u5740\u8d44\u4ea7\u67e5\u8be2\u5931\u8d25\uff0c\u8bf7\u7a0d\u540e\u518d\u8bd5\u3002")
            return True
        if not crypto_cfg.get("price_query_enabled", True):
            return False
        await message.reply_text(await extra_features.fetch_spot_summary(arg))
        return True

    async def handle_wallet_callback(update, context):
        del context
        query = update.callback_query
        data = getattr(query, "data", "") or ""
        if not data.startswith("wallet:"):
            return False
        await callbacks.safe_answer(query)
        try:
            parts = data.split(":")
            action = parts[1]
            if action == "summary" and len(parts) >= 3:
                address = parts[2]
                if not _is_valid_wallet_query_address(address):
                    await callbacks.safe_answer(query, "\u5730\u5740\u683c\u5f0f\u65e0\u6548", show_alert=True)
                    return True
                text, markup = await _build_tron_wallet_summary(address)
            elif action == "hist" and len(parts) >= 5:
                kind = parts[2]
                page = int(parts[3])
                address = parts[4]
                if not _is_valid_wallet_query_address(address):
                    await callbacks.safe_answer(query, "\u5730\u5740\u683c\u5f0f\u65e0\u6548", show_alert=True)
                    return True
                text, markup = await _build_wallet_history_page(address, kind, page)
            else:
                await callbacks.safe_answer(query, "\u65e0\u6548\u7684\u5730\u5740\u6d41\u6c34\u6309\u94ae", show_alert=True)
                return True
            await query.edit_message_text(text, reply_markup=markup)
        except Exception:
            await callbacks.safe_answer(query, "\u5730\u5740\u6d41\u6c34\u67e5\u8be2\u5931\u8d25\uff0c\u8bf7\u7a0d\u540e\u518d\u8bd5\u3002", show_alert=True)
        return True

    async def callback_router(update, context):
        data = update.callback_query.data or ""
        if data.startswith("wallet:"):
            handled = await handle_wallet_callback(update, context)
            if handled:
                return
        if data.startswith(("vfb:", "awb:", "ivb:", "rcb:")):
            handled = await handle_rich_message_button_callback(update, context)
            if handled:
                return
        if data.startswith("smb:"):
            handled = await handle_schedule_button_callback(update, context)
            if handled:
                return
        await original_callback_router(update, context)

    def _inject_summary(text: str, summary: str) -> str:
        summary = (summary or "").strip()
        if not summary:
            return text
        if not text:
            return summary
        lines = str(text).splitlines()
        if not lines:
            return summary
        if len(lines) == 1:
            return f"{lines[0]}\n{summary}"
        head = lines[0]
        tail = "\n".join(lines[1:])
        return f"{head}\n{summary}\n\n{tail}" if tail else f"{head}\n{summary}"

    async def _module_send_or_edit(update, context, text: str, reply_markup=None):
        summary = getattr(module, "_runtime_menu_summary", "")
        return await original_module_send_or_edit(update, context, _inject_summary(text, summary), reply_markup)

    async def _extra_send_or_edit(update, text: str, reply_markup=None):
        summary = getattr(admin_extra, "_runtime_menu_summary", "")
        return await original_extra_send_or_edit(update, _inject_summary(text, summary), reply_markup)

    async def _with_module_summary(summary: str, func, *args, **kwargs):
        previous = getattr(module, "_runtime_menu_summary", "")
        module._runtime_menu_summary = summary
        try:
            return await func(*args, **kwargs)
        finally:
            module._runtime_menu_summary = previous

    async def _with_extra_summary(summary: str, func, *args, **kwargs):
        previous = getattr(admin_extra, "_runtime_menu_summary", "")
        admin_extra._runtime_menu_summary = summary
        try:
            return await func(*args, **kwargs)
        finally:
            admin_extra._runtime_menu_summary = previous

    async def _web_admin_base_url(context) -> str:
        configured = module.normalize_url(os.environ.get("WEB_APP_URL", "").strip())
        if configured:
            return configured
        try:
            webhook = await context.bot.get_webhook_info()
        except module.TelegramError:
            webhook = None
        webhook_url = module.normalize_url(getattr(webhook, "url", "") or "")
        if webhook_url:
            parts = urlsplit(webhook_url)
            if parts.scheme and parts.netloc:
                return urlunsplit((parts.scheme, parts.netloc, "/web/", "", ""))
        port = int(os.environ.get("PORT", "8000") or 8000)
        return f"http://127.0.0.1:{port}/web/"

    async def show_group_select(update, context, state: dict):
        user_id = update.effective_user.id
        groups = await module._manageable_groups(context, user_id)
        web_base_url = await module._web_admin_base_url(context)
        lines = [
            "\u7fa4\u7ec4\u8bbe\u7f6e",
            "\u8bf7\u9009\u62e9\u8981\u7ba1\u7406\u7684\u7fa4\u7ec4\u3002",
            "\u8bf7\u786e\u4fdd\u673a\u5668\u4eba\u4e3a\u7ba1\u7406\u5458\u3002",
        ]
        if not groups:
            lines.append("\u6682\u65e0\u53ef\u7ba1\u7406\u7684\u7fa4\u7ec4\u3002")
            if not module.KV_ENABLED:
                lines.extend(
                    [
                        "",
                        "\u5f53\u524d\u672a\u914d\u7f6e KV \u6301\u4e45\u5316\uff0c\u8fd0\u884c\u5728 Vercel \u65f6\u65e0\u6cd5\u8bb0\u4f4f\u7fa4\u7ec4\u548c\u7ba1\u7406\u72b6\u6001\u3002",
                        "\u8bf7\u914d\u7f6e KV_REST_API_URL \u4e0e KV_REST_API_TOKEN \u540e\uff0c\u518d\u5728\u76ee\u6807\u7fa4\u5185\u53d1\u4e00\u6761\u6d88\u606f\u89e6\u53d1\u767b\u8bb0\u3002",
                    ]
                )
        rows = []
        for group in groups:
            group_id = int(group.get("id"))
            rows.append(
                [
                    module.InlineKeyboardButton(
                        group.get("title", str(group_id)),
                        callback_data=f"admin:select_group:{group_id}",
                    ),
                    module._url_btn("\U0001f310 \u8fdb\u5165Web", _append_group_id(module, web_base_url, group_id)),
                ]
            )
        if not rows:
            rows.append([module._btn("\u6682\u65e0\u7fa4\u7ec4", "admin:none")])
        rows.append([module._btn("\U0001f3e0 \u9996\u9875", "admin:home")])
        markup = module.InlineKeyboardMarkup(rows)
        await module._send_or_edit(update, context, "\n".join(lines), markup)

    async def _fetch_tron_wallet_summary(address: str) -> str:
        account_info_result, account_result, trc20_result = await extra_features.asyncio.gather(
            _fetch_tron_account_info(address),
            extra_features._fetch_json(
                "POST",
                "https://api.trongrid.io/walletsolidity/getaccount",
                json={"address": address, "visible": True},
            ),
            extra_features._fetch_tron_trc20_assets(address),
            return_exceptions=True,
        )

        if isinstance(account_result, Exception):
            raise account_result
        account_info = {} if isinstance(account_info_result, Exception) else dict(account_info_result or {})
        data = account_result
        trx_balance = extra_features._to_decimal(data.get("balance")) / extra_features.TRON_SUN_PER_TRX
        trc10_assets = extra_features._iter_tron_assets(data.get("assetV2") or data.get("asset"))
        trc20_assets = [] if isinstance(trc20_result, Exception) else list(trc20_result or [])
        usdt_balance = _extract_tron_trc20_balance(account_info, TRON_USDT_CONTRACT, 6)
        if usdt_balance <= 0:
            for asset in trc20_assets:
                if str(asset.get("symbol") or "").strip().upper() == "USDT":
                    usdt_balance = extra_features._to_decimal(asset.get("balance"))
                    break

        lines = [
            "\u94fe: TRON",
            f"\u5730\u5740: {extra_features._short_addr(address, 8, 6)}",
            f"USDT: {extra_features._format_amount(usdt_balance, 2)}",
            f"TRX: {extra_features._format_amount(trx_balance, 6)}",
        ]
        if trc10_assets:
            lines.append(f"TRC10 \u5171 {len(trc10_assets)} \u9879\uff0c\u5c55\u793a\u524d {min(len(trc10_assets), 8)} \u9879:")
            for key, amount in trc10_assets[:8]:
                lines.append(extra_features._wallet_asset_line(f"TRC10#{key}", amount, suffix="(\u672a\u8ba1\u4ef7)"))
        else:
            lines.append("\u6682\u65e0 TRC10 \u8d44\u4ea7")

        if trc20_assets:
            lines.append(f"TRC20 \u5171 {len(trc20_assets)} \u9879\uff0c\u5c55\u793a\u524d {min(len(trc20_assets), 8)} \u9879:")
            for asset in trc20_assets[:8]:
                lines.append(
                    extra_features._wallet_asset_line(
                        asset.get("symbol", "TRC20"),
                        asset.get("balance"),
                        asset.get("usd"),
                    )
                )
        elif isinstance(trc20_result, Exception):
            lines.append("TRC20 \u8d44\u4ea7\u52a0\u8f7d\u5931\u8d25\uff0c\u8bf7\u7a0d\u540e\u91cd\u8bd5")
        else:
            lines.append("\u6682\u65e0 TRC20 \u8d44\u4ea7")
        return "\n".join(lines)

    async def show_verify_menu(update, context, state: dict):
        group_id = module._current_group(state)
        cfg = module.get_group_config(group_id)
        verify_fail_buttons = _normalize_schedule_buttons(cfg.get("verify_fail_buttons") or [])
        summary = "\n".join(
            [
                f"\u5f53\u524d\u72b6\u6001: {_status_label(bool(cfg.get('verify_enabled', True)))}",
                f"\u9a8c\u8bc1\u5931\u8d25\u6587\u672c: {module._verify_preview_text(cfg.get('verify_fail_text', ''), limit=48)}",
                f"\u9a8c\u8bc1\u5931\u8d25\u56fe\u7247: {'\u5df2\u8bbe\u7f6e' if cfg.get('verify_fail_photo_file_id') else '\u672a\u8bbe\u7f6e'}",
                f"\u9a8c\u8bc1\u5931\u8d25\u6309\u94ae: {len(verify_fail_buttons)} \u4e2a",
            ]
        )
        await _with_module_summary(summary, original_show_verify_menu, update, context, state)

    async def show_welcome_menu(update, context, state: dict):
        group_id = module._current_group(state)
        cfg = module.get_group_config(group_id)
        summary = "\n".join(
            [
                f"\u5f53\u524d\u72b6\u6001: {_status_label(bool(cfg.get('welcome_enabled', True)))}",
                f"\u81ea\u52a8\u5220\u9664\u6b22\u8fce\u8bed: {int(cfg.get('welcome_ttl_sec', 0) or 0)} \u79d2",
                f"\u4ec5\u4fdd\u7559\u6700\u65b0\u4e00\u6761: {_status_label(bool(cfg.get('welcome_delete_prev', False)))}",
            ]
        )
        await _with_module_summary(summary, original_show_welcome_menu, update, context, state)

    async def show_autodelete_menu(update, context, state: dict):
        group_id = module._current_group(state)
        cfg = module.get_group_auto_delete(group_id)
        delete_items = [
            ("delete_system", "\u7cfb\u7edf\u6d88\u606f"),
            ("delete_channel_mask", "\u9891\u9053\u9a6c\u7532"),
            ("delete_links", "\u94fe\u63a5\u6d88\u606f"),
            ("delete_long", "\u8d85\u957f\u6d88\u606f"),
            ("delete_videos", "\u89c6\u9891\u6d88\u606f"),
            ("delete_stickers", "\u8d34\u7eb8\u6d88\u606f"),
            ("delete_forwarded", "\u7981\u6b62\u8f6c\u53d1"),
            ("delete_ad_stickers", "\u5220\u9664\u5e7f\u544a\u8d34\u7eb8"),
            ("delete_archives", "\u538b\u7f29\u5305"),
            ("delete_executables", "\u53ef\u6267\u884c\u6587\u4ef6"),
            ("delete_notice_text", "\u63d0\u9192\u6587\u5b57"),
            ("delete_documents", "\u6587\u6863"),
            ("delete_mentions", "\u5220\u9664@"),
            ("delete_other_commands", "\u5176\u4ed6\u547d\u4ee4"),
            ("delete_qr", "\u4e8c\u7ef4\u7801"),
            ("delete_edited", "\u7f16\u8f91\u6d88\u606f"),
            ("delete_member_emoji", "\u4f1a\u5458\u8868\u60c5"),
            ("delete_member_emoji_only", "\u4ec5\u8868\u60c5"),
            ("delete_external_reply", "\u5220\u9664\u5916\u90e8\u56de\u590d"),
            ("delete_shared_contact", "\u5220\u9664\u5206\u4eab\u8054\u7cfb\u4eba"),
        ]
        enabled_labels = [label for key, label in delete_items if cfg.get(key)]
        text = "\n".join(
            [
                "\u81ea\u52a8\u5220\u9664\u6d88\u606f",
                "\u2705 = \u5f00\u542f\u5220\u9664",
                "\u274c = \u5173\u95ed\u5220\u9664",
                f"\u5df2\u5f00\u542f\u9879\u76ee: {len(enabled_labels)}/{len(delete_items)}",
                f"\u6392\u9664\u7ba1\u7406\u5458: {_status_label(bool(cfg.get('exclude_admins', False)))}",
                f"\u81ea\u5b9a\u4e49\u89c4\u5219: {len(cfg.get('custom_rules', []) or [])} \u6761",
                f"\u5f53\u524d\u542f\u7528: {_join_labels(enabled_labels)}",
            ]
        )

        def t(key, label):
            return module._btn(("\u2705 " if cfg.get(key) else "\u274c ") + label, f"admin:ad_toggle:{key}")

        rows = module._menu_two_cols(
            [
                t("delete_system", "\u7cfb\u7edf\u6d88\u606f"),
                t("delete_channel_mask", "\u9891\u9053\u9a6c\u7532"),
                t("delete_links", "\u94fe\u63a5\u6d88\u606f"),
                t("delete_long", "\u8d85\u957f\u6d88\u606f"),
                t("delete_videos", "\u89c6\u9891\u6d88\u606f"),
                t("delete_stickers", "\u8d34\u7eb8\u6d88\u606f"),
                t("delete_forwarded", "\u7981\u6b62\u8f6c\u53d1"),
                t("delete_ad_stickers", "\u5220\u9664\u5e7f\u544a\u8d34\u7eb8"),
                t("delete_archives", "\u538b\u7f29\u5305"),
                t("delete_executables", "\u53ef\u6267\u884c\u6587\u4ef6"),
                t("delete_notice_text", "\u63d0\u9192\u6587\u5b57"),
                t("delete_documents", "\u6587\u6863"),
                t("delete_mentions", "\u5220\u9664@"),
                t("delete_other_commands", "\u5176\u4ed6\u547d\u4ee4"),
                t("delete_qr", "\u4e8c\u7ef4\u7801"),
                t("delete_edited", "\u7f16\u8f91\u6d88\u606f"),
                t("delete_member_emoji", "\u4f1a\u5458\u8868\u60c5"),
                t("delete_member_emoji_only", "\u4ec5\u8868\u60c5"),
                t("delete_external_reply", "\u5220\u9664\u5916\u90e8\u56de\u590d"),
                t("delete_shared_contact", "\u5220\u9664\u5206\u4eab\u8054\u7cfb\u4eba"),
                t("exclude_admins", "\u6392\u9664\u7ba1\u7406\u5458"),
            ]
        )
        rows.append(
            [
                module._btn("\U0001F4C4 \u6dfb\u52a0\u81ea\u5b9a\u4e49\u89c4\u5219", "admin:ad_add_rule"),
                module._btn("\U0001F4DA \u5168\u90e8\u81ea\u5b9a\u4e49\u89c4\u5219", "admin:ad_rules"),
            ]
        )
        rows.append([module._btn("\u2b05\ufe0f \u8fd4\u56de", "admin:main")])
        await module._send_or_edit(update, context, text, module.InlineKeyboardMarkup(rows))

    async def show_autoban_menu(update, context, state: dict):
        group_id = module._current_group(state)
        cfg = module.get_group_auto_ban(group_id)
        rules = list(cfg.get("rules", []) or [])
        summary = "\n".join(
            [
                f"\u9ed8\u8ba4\u5c01\u7981\u65f6\u957f: {int(cfg.get('default_duration_sec', 86400) or 86400)} \u79d2",
                f"\u89c4\u5219\u6570\u91cf: {len(rules)} \u6761",
                f"\u5f53\u524d\u89c4\u5219: {_rule_preview(rules)}",
            ]
        )
        await _with_module_summary(summary, original_show_autoban_menu, update, context, state)

    async def show_automute_menu(update, context, state: dict):
        group_id = module._current_group(state)
        cfg = module.get_group_auto_mute(group_id)
        rules = list(cfg.get("rules", []) or [])
        summary = "\n".join(
            [
                f"\u9ed8\u8ba4\u7981\u8a00\u65f6\u957f: {int(cfg.get('default_duration_sec', 60) or 60)} \u79d2",
                f"\u89c4\u5219\u6570\u91cf: {len(rules)} \u6761",
                f"\u5f53\u524d\u89c4\u5219: {_rule_preview(rules)}",
            ]
        )
        await _with_module_summary(summary, original_show_automute_menu, update, context, state)

    async def show_autowarn_menu(update, context, state: dict):
        group_id = module._current_group(state)
        cfg = module.get_group_auto_warn(group_id)
        action = "kick" if str(cfg.get("action", "mute") or "mute").lower() == "kick" else "mute"
        punish_text = "\u8e22\u51fa" if action == "kick" else f"\u7981\u8a00 {int(cfg.get('mute_seconds', 86400) or 86400)} \u79d2"
        warn_buttons = _normalize_schedule_buttons(cfg.get("warn_buttons") or [])
        summary = "\n".join(
            [
                f"\u8b66\u544a\u4e0a\u9650: {int(cfg.get('warn_limit', 3) or 3)} \u6b21",
                f"\u8fbe\u5230\u4e0a\u9650\u540e: {punish_text}",
                f"\u547d\u4ee4\u7c7b\u6d88\u606f\u4e5f\u8ba1\u5165\u8b66\u544a: {_status_label(bool(cfg.get('cmd_mute_enabled', False)))}",
                f"\u89c4\u5219\u6570\u91cf: {len(cfg.get('rules', []) or [])} \u6761",
                f"\u63d0\u793a\u6587\u6848: {module._verify_preview_text(cfg.get('warn_text', ''), limit=64)}",
                f"\u63d0\u793a\u56fe\u7247: {'\u5df2\u8bbe\u7f6e' if cfg.get('warn_photo_file_id') else '\u672a\u8bbe\u7f6e'}",
                f"\u63d0\u793a\u6309\u94ae: {len(warn_buttons)} \u4e2a",
            ]
        )
        await _with_module_summary(summary, original_show_autowarn_menu, update, context, state)

    async def show_antispam_menu(update, context, state: dict):
        group_id = module._current_group(state)
        cfg = module.get_group_anti_spam(group_id)
        type_labels = {
            "text": "\u6587\u672c",
            "photo": "\u56fe\u7247",
            "video": "\u89c6\u9891",
            "document": "\u6587\u4ef6",
            "voice": "\u8bed\u97f3",
            "sticker": "\u8d34\u7eb8",
            "link": "\u94fe\u63a5",
        }
        enabled_types = [type_labels.get(item, item) for item in (cfg.get("types") or [])]
        action = "ban" if str(cfg.get("action", "mute") or "mute").lower() == "ban" else "mute"
        punish_text = "\u5c01\u7981" if action == "ban" else f"\u7981\u8a00 {int(cfg.get('mute_seconds', 300) or 300)} \u79d2"
        summary = "\n".join(
            [
                f"\u5f53\u524d\u72b6\u6001: {_status_label(bool(cfg.get('enabled', False)))}",
                f"\u89e6\u53d1\u6761\u4ef6: {int(cfg.get('window_sec', 10) or 10)} \u79d2\u5185\u53d1\u9001 {int(cfg.get('threshold', 3) or 3)} \u6b21",
                f"\u5904\u7406\u65b9\u5f0f: {punish_text}",
                f"\u68c0\u6d4b\u7c7b\u578b: {_join_labels(enabled_types)}",
            ]
        )
        await _with_module_summary(summary, original_show_antispam_menu, update, context, state)

    async def show_ad_filter_menu(update, context, state: dict):
        group_id = admin_extra._group_id(state)
        ad_cfg = admin_extra.get_group_config(group_id).get("ad_filter", {}) or {}
        summary = "\n".join(
            [
                f"\u6635\u79f0\u8fc7\u6ee4: {_status_label(bool(ad_cfg.get('nickname_enabled', False)))}",
                f"\u8d34\u7eb8\u8fc7\u6ee4: {_status_label(bool(ad_cfg.get('sticker_enabled', False)))}",
                f"\u5e7f\u544a\u6d88\u606f\u8fc7\u6ee4: {_status_label(bool(ad_cfg.get('message_enabled', False)))}",
                f"\u5141\u8bb8\u9891\u9053\u9a6c\u7532\u53d1\u8a00: {_status_label(bool(ad_cfg.get('block_channel_mask', False)))}",
            ]
        )
        await _with_extra_summary(summary, original_show_ad_filter_menu, update, context, state)

    async def show_command_gate_menu(update, context, state: dict):
        group_id = admin_extra._group_id(state)
        gate = admin_extra.get_group_config(group_id).get("command_gate", {}) or {}
        command_labels = {
            "sign": "\u7b7e\u5230",
            "profile": "\u4e2a\u4eba\u4fe1\u606f",
            "warn": "\u8b66\u544a",
            "help": "\u5e2e\u52a9",
            "config": "\u914d\u7f6e",
            "ban": "\u5c01\u7981",
            "kick": "\u8e22\u51fa",
            "mute": "\u7981\u8a00",
        }
        disabled = [label for key, label in command_labels.items() if gate.get(key)]
        summary = "\n".join(
            [
                f"\u5df2\u5173\u95ed\u547d\u4ee4: {len(disabled)} \u9879",
                f"\u5f53\u524d\u5173\u95ed: {_join_labels(disabled)}",
            ]
        )
        await _with_extra_summary(summary, original_show_command_gate_menu, update, context, state)

    async def show_crypto_menu(update, context, state: dict):
        group_id = admin_extra._group_id(state)
        crypto_cfg = admin_extra.get_group_config(group_id).get("crypto", {}) or {}
        summary = "\n".join(
            [
                f"\u5e01\u4ef7\u63a8\u9001: {_status_label(bool(crypto_cfg.get('push_enabled', False)))}",
            ]
        )
        await _with_extra_summary(summary, original_show_crypto_menu, update, context, state)

    async def show_related_menu(update, context, state: dict):
        group_id = admin_extra._group_id(state)
        cfg = admin_extra.get_group_config(group_id).get("related_channel", {}) or {}
        buttons = _normalize_schedule_buttons(cfg.get("occupy_comment_buttons") or [])
        summary = "\n".join(
            [
                f"\u53d6\u6d88\u5173\u8054\u9891\u9053\u7f6e\u9876: {_status_label(bool(cfg.get('cancel_top_pin', False)))}",
                f"\u62a2\u5360\u8bc4\u8bba\u533a: {_status_label(bool(cfg.get('occupy_comment', False)))}",
                f"\u8bc4\u8bba\u6d88\u606f: {admin_extra._escape(_preview_text(cfg.get('occupy_comment_text', ''), limit=48))}",
                f"\u8bc4\u8bba\u56fe\u7247: {'\u5df2\u8bbe\u7f6e' if cfg.get('occupy_comment_photo_file_id') else '\u672a\u8bbe\u7f6e'}",
                f"\u8bc4\u8bba\u6309\u94ae: {len(buttons)} \u4e2a",
            ]
        )
        await _with_extra_summary(summary, original_show_related_menu, update, context, state)

    async def show_invite_menu(update, context, state: dict):
        group_id = admin_extra._group_id(state)
        cfg = admin_extra.get_group_config(group_id).get("invite_links", {}) or {}
        buttons = _normalize_schedule_buttons(cfg.get("notify_buttons") or [])
        summary = "\n".join(
            [
                f"\u5f53\u524d\u72b6\u6001: {_status_label(bool(cfg.get('enabled', False)))}",
                f"\u9080\u8bf7\u6210\u529f\u7fa4\u5185\u901a\u77e5: {_status_label(bool(cfg.get('notify_enabled', False)))}",
                f"\u901a\u77e5\u6587\u672c: {admin_extra._escape(_preview_text(cfg.get('notify_text', ''), limit=48))}",
                f"\u901a\u77e5\u56fe\u7247: {'\u5df2\u8bbe\u7f6e' if cfg.get('notify_photo_file_id') else '\u672a\u8bbe\u7f6e'}",
                f"\u901a\u77e5\u6309\u94ae: {len(buttons)} \u4e2a",
            ]
        )
        await _with_extra_summary(summary, original_show_invite_menu, update, context, state)

    async def show_member_menu(update, context, state: dict):
        group_id = admin_extra._group_id(state)
        member_cfg = admin_extra.get_group_config(group_id).get("member_watch", {}) or {}
        summary = "\n".join(
            [
                f"\u6635\u79f0\u53d8\u66f4\u68c0\u6d4b: {_status_label(bool(member_cfg.get('nickname_change_detect', False)))}",
                f"\u7fa4\u5185\u63d0\u9192: {_status_label(bool(member_cfg.get('nickname_change_notice', False)))}",
                "\u7cfb\u7edf\u4f1a\u4fdd\u7559\u6700\u8fd1 50 \u6761\u53d8\u66f4\u8bb0\u5f55",
            ]
        )
        await _with_extra_summary(summary, original_show_member_menu, update, context, state)

    async def show_language_menu(update, context, state: dict):
        group_id = admin_extra._group_id(state)
        lang_cfg = admin_extra.get_group_config(group_id).get("language_whitelist", {}) or {}
        allowed = list(lang_cfg.get("allowed", []) or [])
        allowed_labels = [str(code).strip() for code in allowed if str(code).strip()]
        summary = "\n".join(
            [
                f"\u5f53\u524d\u72b6\u6001: {_status_label(bool(lang_cfg.get('enabled', False)))}",
                f"\u5141\u8bb8\u8bed\u8a00: {len(allowed_labels)} \u79cd",
                f"\u5f53\u524d\u767d\u540d\u5355: {_join_labels(allowed_labels)}",
            ]
        )
        await _with_extra_summary(summary, original_show_language_menu, update, context, state)

    async def show_verified_placeholder(update, context, state: dict):
        group_id = admin_extra._group_id(state)
        verified_cfg = admin_extra.get_group_config(group_id).get("verified_user", {}) or {}
        summary = "\n".join(
            [
                f"\u5f53\u524d\u72b6\u6001: {_status_label(bool(verified_cfg.get('enabled', False)))}",
                "\u8be5\u6a21\u5757\u73b0\u5728\u4ee5 Web \u7ba1\u7406\u4e3a\u4e3b\uff0cTelegram \u7aef\u4ec5\u4fdd\u7559\u5165\u53e3\u63d0\u793a",
            ]
        )
        await _with_extra_summary(summary, original_show_verified_placeholder, update, context, state)

    async def show_schedule_menu(update, context, state: dict):
        group_id = admin_extra._group_id(state)
        items = admin_extra.load_schedule_items(group_id)
        enabled_count = sum(1 for item in items if item.get("enabled", True))
        limit = admin_extra.schedule_limit_for_group(group_id)
        lines = [
            "⏰ 定时消息",
            "",
            f"当前套餐: {admin_extra.group_plan_label(group_id)}",
            f"当前共有 {len(items)}/{limit} 条定时消息，已启用 {enabled_count} 条。",
            "发送格式支持同一条消息内的图片、文本、按钮。",
            "图片会显示在顶部，文本在中间，按钮在下方。",
            "快捷添加: 消息内容 | 间隔分钟",
            "示例: 晚安，各位 | 120",
            "也可以直接发图片并把上述格式写在 caption 里，或发送 JSON 对象（支持 text / photo_file_id / buttons / interval_minutes）。",
        ]
        if len(items) >= limit:
            lines.append("已达到当前套餐上限，续费后才可继续新增定时消息。")
        rows = [
            [admin_extra._btn("添加定时消息", "adminx:schedule:add")],
            [admin_extra._btn("全部定时消息", "adminx:schedule:list")],
            [admin_extra._btn("返回", "admin:main")],
        ]
        await admin_extra._send_or_edit(update, "\n".join(lines), admin_extra.InlineKeyboardMarkup(rows))

    async def show_schedule_list_menu(update, context, state: dict):
        group_id = admin_extra._group_id(state)
        items = admin_extra.load_schedule_items(group_id)
        lines = ["⏰ 全部定时消息", ""]
        rows = []
        if not items:
            lines.append("暂无定时消息。")
        else:
            for idx, item in enumerate(items):
                interval_min = max(1, int(item.get("interval_sec", 0) or 0) // 60)
                next_at = int(item.get("next_at", 0) or 0)
                next_text = time.strftime("%m-%d %H:%M", time.localtime(next_at)) if next_at > 0 else "未安排"
                preview = admin_extra._escape(admin_extra._preview(item.get("text", ""), "(仅图片/按钮)", 24))
                photo_flag = "有图" if item.get("photo_file_id") else "无图"
                button_count = len(item.get("buttons", []) or [])
                lines.append(f"{idx + 1}. {admin_extra._checked(item.get('enabled', True))} {preview}")
                lines.append(f"   每 {interval_min} 分钟，下次 {next_text}")
                lines.append(f"   {photo_flag} / 按钮 {button_count} 个")
                rows.append([
                    admin_extra._btn("启用/停用", f"adminx:schedule:toggle:{idx}"),
                    admin_extra._btn("删除", f"adminx:schedule:delete:{idx}"),
                ])
        rows.append([admin_extra._btn("返回", "adminx:schedule:menu")])
        await admin_extra._send_or_edit(update, "\n".join(lines), admin_extra.InlineKeyboardMarkup(rows))

    async def handle_admin_extra_message(update, context, state: dict, msg_text: str) -> bool:
        current_state = str((state or {}).get("state") or "")
        if current_state != "x:schedule:add":
            return await original_extra_message(update, context, state, msg_text)

        user_id = update.effective_user.id
        group_id = admin_extra._group_id(state)
        if not group_id:
            await update.effective_message.reply_text("\u8bf7\u5148\u9009\u62e9\u7fa4\u7ec4")
            return True
        items = admin_extra.load_schedule_items(group_id)
        limit = admin_extra.schedule_limit_for_group(group_id)
        if len(items) >= limit:
            await update.effective_message.reply_text(f"\u5f53\u524d\u5957\u9910\u6700\u591a\u652f\u6301 {limit} \u6761\u5b9a\u65f6\u6d88\u606f\u3002")
            return True
        photos = getattr(update.effective_message, "photo", None) or []
        photo_file_id = photos[-1].file_id if photos else ""
        try:
            item = extra_features.parse_schedule_message_input((msg_text or "").strip(), photo_file_id=photo_file_id)
        except ValueError as exc:
            await update.effective_message.reply_text(str(exc))
            return True
        items.append(item)
        admin_extra.save_schedule_items(group_id, items)
        new_state = admin_extra._state_with(state, state=None, tmp={})
        admin_extra._save_state(user_id, new_state)
        await show_schedule_menu(update, context, new_state)
        return True

    async def admin_photo(update, context):
        if getattr(update.effective_chat, "type", "") == "private" and getattr(update.effective_message, "photo", None):
            user_id = update.effective_user.id
            state = module.get_admin_state(user_id)
            group_id = module._current_group(state)
            if group_id and await module._can_manage_group(context, user_id, group_id):
                st = state.get("state")
                if st and str(st).startswith("x:"):
                    msg_text = update.effective_message.text or update.effective_message.caption or ""
                    handled = await admin_extra.handle_admin_extra_message(update, context, state, msg_text)
                    if handled:
                        return
        return await original_admin_photo(update, context)

    _base_handle_admin_extra_message = handle_admin_extra_message

    async def show_schedule_list_menu(update, context, state: dict):
        del context
        group_id = admin_extra._group_id(state)
        items = admin_extra.load_schedule_items(group_id)
        lines = ["\u23f0 \u5168\u90e8\u5b9a\u65f6\u6d88\u606f", ""]
        rows = []
        if not items:
            lines.append("\u6682\u65e0\u5b9a\u65f6\u6d88\u606f\u3002")
        else:
            for idx, item in enumerate(items):
                interval_min = max(1, int(item.get("interval_sec", 0) or 0) // 60)
                next_at = int(item.get("next_at", 0) or 0)
                next_text = time.strftime("%m-%d %H:%M", time.localtime(next_at)) if next_at > 0 else "\u672a\u5b89\u6392"
                preview = admin_extra._escape(admin_extra._preview(item.get("text", ""), "(\u4ec5\u56fe\u7247/\u6309\u94ae)", 24))
                photo_flag = "\u6709\u56fe" if item.get("photo_file_id") else "\u65e0\u56fe"
                button_count = len(item.get("buttons", []) or [])
                lines.append(f"{idx + 1}. {admin_extra._checked(item.get('enabled', True))} {preview}")
                lines.append(f"   \u6bcf {interval_min} \u5206\u949f\uff0c\u4e0b\u6b21 {next_text}")
                lines.append(f"   {photo_flag} / \u6309\u94ae {button_count} \u4e2a")
                rows.append(
                    [
                        admin_extra._btn("\u7f16\u8f91\u6d88\u606f", f"adminx:schedule:edit:{idx}"),
                        admin_extra._btn("\u542f\u7528/\u505c\u7528", f"adminx:schedule:toggle:{idx}"),
                    ]
                )
                rows.append([admin_extra._btn("\u5220\u9664", f"adminx:schedule:delete:{idx}")])
        rows.append([admin_extra._btn("\u8fd4\u56de", "adminx:schedule:menu")])
        await admin_extra._send_or_edit(update, "\n".join(lines), admin_extra.InlineKeyboardMarkup(rows))

    async def handle_admin_extra_message(update, context, state: dict, msg_text: str) -> bool:
        current_state = str((state or {}).get("state") or "")
        if current_state.startswith("x:rich:text:") or current_state.startswith("x:rich:photo:"):
            user_id = update.effective_user.id
            group_id = admin_extra._group_id(state)
            if not group_id:
                await update.effective_message.reply_text("\u8bf7\u5148\u9009\u62e9\u7fa4\u7ec4")
                return True
            target = current_state.split(":", 3)[-1]
            payload = _get_rich_message_target(group_id, target)
            if payload is None:
                await update.effective_message.reply_text("\u6d88\u606f\u914d\u7f6e\u4e0d\u5b58\u5728\u6216\u5df2\u88ab\u5220\u9664\u3002")
                return True
            if current_state.startswith("x:rich:text:"):
                payload["text"] = (msg_text or "").strip()
            else:
                photos = getattr(update.effective_message, "photo", None) or []
                if not photos:
                    await update.effective_message.reply_text("\u8bf7\u53d1\u9001\u56fe\u7247\u3002")
                    return True
                payload["photo_file_id"] = photos[-1].file_id
            if not _save_rich_message_target(group_id, target, payload):
                await update.effective_message.reply_text("\u4fdd\u5b58\u5931\u8d25\uff0c\u8bf7\u7a0d\u540e\u518d\u8bd5\u3002")
                return True
            new_state = admin_extra._state_with(state, state=None, tmp={})
            admin_extra._save_state(user_id, new_state)
            await show_rich_message_editor(update, context, new_state, target)
            return True
        return await _base_handle_admin_extra_message(update, context, state, msg_text)

    async def admin_message(update, context):
        if getattr(update.effective_chat, "type", "") != "private":
            return await original_admin_message(update, context)
        user_id = update.effective_user.id
        state = module.get_admin_state(user_id)
        msg_text = update.effective_message.text or update.effective_message.caption or ""

        if await module.handle_private_home_message(update, context, state, msg_text):
            return

        group_id = module._current_group(state)
        if not group_id or not await module._can_manage_group(context, user_id, group_id):
            if not module.is_super_admin(user_id) and state.get("active_group_id"):
                new_state = module._state_with(state, active_group_id=None, state=module.STATE_NONE, tmp={})
                module._save_state(user_id, new_state)
            return

        st = state.get("state")
        if st and str(st).startswith("x:"):
            handled = await admin_extra.handle_admin_extra_message(update, context, state, msg_text)
            if handled:
                return

        if st == module.STATE_BTN_ROW:
            tmp = state.get("tmp", {}) or {}
            target = str(tmp.get("btn_target") or "")
            if target.startswith("rich:"):
                try:
                    row = int((msg_text or "").strip())
                except Exception:
                    await update.effective_message.reply_text("\u8bf7\u8f93\u5165\u6570\u5b57\u884c\u53f7")
                    return
                rich_target = target.split(":", 1)[1]
                payload = _get_rich_message_target(group_id, rich_target)
                if payload is None:
                    await update.effective_message.reply_text("\u6d88\u606f\u914d\u7f6e\u4e0d\u5b58\u5728\u6216\u5df2\u88ab\u5220\u9664\u3002")
                    return
                buttons = list(payload.get("buttons") or [])
                buttons.append(
                    {
                        "text": str(tmp.get("btn_text") or "\u6309\u94ae"),
                        "type": str(tmp.get("btn_type") or "url"),
                        "value": str(tmp.get("btn_value") or ""),
                        "row": max(0, row),
                    }
                )
                payload["buttons"] = buttons
                _save_rich_message_target(group_id, rich_target, payload)
                new_state = module._state_with(state, state=module.STATE_NONE, tmp={})
                module._save_state(user_id, new_state)
                await show_rich_message_editor(update, context, new_state, rich_target)
                return

        return await original_admin_message(update, context)

    async def _guard_active_group(update, context, state: dict):
        query = update.callback_query
        user_id = query.from_user.id
        group_id = module._current_group(state)
        if group_id and await module._can_manage_group(context, user_id, group_id):
            return state, group_id, False
        new_state = module._state_with(state, active_group_id=None, state=module.STATE_NONE, tmp={})
        module._save_state(user_id, new_state)
        if group_id:
            await module.safe_answer(query, "\u5f53\u524d\u7fa4\u7ec4\u65e0\u6743\u9650", show_alert=True)
        await module.show_group_select(update, context, new_state)
        return new_state, None, True

    async def _toggle_extra_config(update, context, state: dict, path: tuple[str, ...], module_name: str):
        group_id = admin_extra._group_id(state)
        cfg = admin_extra.get_group_config(group_id)
        enabled = admin_extra._toggle(cfg, path)
        admin_extra.save_group_config(group_id, cfg)
        query = getattr(update, "callback_query", None)
        if query:
            await admin_extra.safe_answer(query, _toggle_notice(enabled), show_alert=True)
        await admin_extra._show_menu(module_name, update, context, state)

    async def handle_admin_extra_callback(update, context, state: dict) -> bool:
        query = getattr(update, "callback_query", None)
        data = getattr(query, "data", "") or ""
        if not data.startswith("adminx:"):
            return False

        user_id = update.effective_user.id
        group_id = admin_extra._group_id(state)
        if not group_id:
            if query:
                await admin_extra.safe_answer(query, "\u8bf7\u5148\u9009\u62e9\u7fa4\u7ec4", show_alert=True)
            return True

        parts = data.split(":")
        if len(parts) >= 3 and parts[1] == "schedule":
            action = parts[2]
            items = admin_extra.load_schedule_items(group_id)
            if action == "add":
                limit = admin_extra.schedule_limit_for_group(group_id)
                if len(items) >= limit:
                    if query:
                        await admin_extra.safe_answer(query, f"当前套餐最多支持 {limit} 条定时消息", show_alert=True)
                    await admin_extra.show_schedule_menu(update, context, state)
                    return True
                prompt = (
                    "请发送定时消息。\n"
                    "快捷格式: 消息内容 | 间隔分钟\n"
                    "也可以直接发图片并把上述格式写在 caption 里，或发送 JSON 对象（支持 text / photo_file_id / buttons / interval_minutes）。"
                )
                await admin_extra._begin_input(update, user_id, state, "x:schedule:add", "schedule", prompt)
                return True
            if action == "list":
                await admin_extra.show_schedule_list_menu(update, context, state)
                return True
            if action in {"toggle", "delete"} and len(parts) >= 4:
                try:
                    idx = int(parts[3])
                except ValueError:
                    idx = -1
                if not (0 <= idx < len(items)):
                    if query:
                        await admin_extra.safe_answer(query, "定时消息不存在", show_alert=True)
                    return True
                if action == "toggle":
                    items[idx]["enabled"] = not items[idx].get("enabled", True)
                    enabled = bool(items[idx]["enabled"])
                    admin_extra.save_schedule_items(group_id, items)
                    if query:
                        await admin_extra.safe_answer(query, _toggle_notice(enabled), show_alert=True)
                else:
                    items.pop(idx)
                    admin_extra.save_schedule_items(group_id, items)
                    if query:
                        await admin_extra.safe_answer(query, "已删除", show_alert=True)
                await admin_extra.show_schedule_list_menu(update, context, state)
                return True

        if len(parts) >= 4 and parts[1] == "lang" and parts[2] == "allow":
            code = parts[3]
            cfg = admin_extra.get_group_config(group_id)
            allowed = list(cfg.get("language_whitelist", {}).get("allowed", []) or [])
            if code in allowed:
                allowed = [item for item in allowed if item != code]
            else:
                allowed.append(code)
            enabled = code in allowed
            admin_extra._nested_set(cfg, ("language_whitelist", "allowed"), allowed)
            admin_extra.save_group_config(group_id, cfg)
            if query:
                await admin_extra.safe_answer(query, _toggle_notice(enabled), show_alert=True)
            await admin_extra.show_language_menu(update, context, state)
            return True

        return await original_extra_callback(update, context, state)

    async def _handle_main_toggle(update, context, state: dict, group_id: int, data: str) -> bool:
        query = update.callback_query

        if data == "admin:verify_toggle":
            cfg = module.get_group_config(group_id)
            enabled = not cfg.get("verify_enabled", True)
            cfg["verify_enabled"] = enabled
            module.save_group_config(group_id, cfg)
            await module.safe_answer(query, _toggle_notice(enabled), show_alert=True)
            await module.show_verify_menu(update, context, state)
            return True

        if data == "admin:verify_private":
            cfg = module.get_group_config(group_id)
            enabled = not cfg.get("verify_private", False)
            cfg["verify_private"] = enabled
            module.save_group_config(group_id, cfg)
            await module.safe_answer(query, _toggle_notice(enabled), show_alert=True)
            await module.show_verify_menu(update, context, state)
            return True

        if data == "admin:welcome_toggle":
            cfg = module.get_group_config(group_id)
            enabled = not cfg.get("welcome_enabled", True)
            cfg["welcome_enabled"] = enabled
            module.save_group_config(group_id, cfg)
            await module.safe_answer(query, _toggle_notice(enabled), show_alert=True)
            await module.show_welcome_menu(update, context, state)
            return True

        if data == "admin:welcome_delete_prev":
            cfg = module.get_group_config(group_id)
            enabled = not cfg.get("welcome_delete_prev", False)
            cfg["welcome_delete_prev"] = enabled
            module.save_group_config(group_id, cfg)
            await module.safe_answer(query, _toggle_notice(enabled), show_alert=True)
            await module.show_welcome_menu(update, context, state)
            return True

        if data.startswith("admin:ar_toggle:"):
            idx = int(data.split(":")[-1])
            rules = module.get_group_auto_replies(group_id)
            if 0 <= idx < len(rules):
                rules[idx]["enabled"] = not rules[idx].get("enabled", True)
                enabled = bool(rules[idx]["enabled"])
                module.save_group_auto_replies(group_id, rules)
                await module.safe_answer(query, _toggle_notice(enabled), show_alert=True)
            await module.show_ar_rule_menu(update, context, state, idx)
            return True

        if data.startswith("admin:ad_toggle:"):
            key = data.split(":")[-1]
            cfg = module.get_group_auto_delete(group_id)
            enabled = not cfg.get(key, False)
            cfg[key] = enabled
            module.save_group_auto_delete(group_id, cfg)
            await module.safe_answer(query, _toggle_notice(enabled), show_alert=True)
            await module.show_autodelete_menu(update, context, state)
            return True

        if data == "admin:aw_cmd_toggle":
            cfg = module.get_group_auto_warn(group_id)
            enabled = not cfg.get("cmd_mute_enabled", False)
            cfg["cmd_mute_enabled"] = enabled
            module.save_group_auto_warn(group_id, cfg)
            await module.safe_answer(query, _toggle_notice(enabled), show_alert=True)
            await module.show_autowarn_menu(update, context, state)
            return True

        if data == "admin:spam_toggle":
            cfg = module.get_group_anti_spam(group_id)
            enabled = not cfg.get("enabled", False)
            cfg["enabled"] = enabled
            module.save_group_anti_spam(group_id, cfg)
            await module.safe_answer(query, _toggle_notice(enabled), show_alert=True)
            await module.show_antispam_menu(update, context, state)
            return True

        if data.startswith("admin:spam_type:"):
            msg_type = data.split(":")[-1]
            cfg = module.get_group_anti_spam(group_id)
            types = set(cfg.get("types") or [])
            if msg_type in types:
                types.remove(msg_type)
            else:
                types.add(msg_type)
            enabled = msg_type in types
            cfg["types"] = list(types)
            module.save_group_anti_spam(group_id, cfg)
            await module.safe_answer(query, _toggle_notice(enabled), show_alert=True)
            await module.show_antispam_menu(update, context, state)
            return True

        return False

    async def admin_callback(update, context):
        query = update.callback_query
        data = getattr(query, "data", "") or ""
        user_id = query.from_user.id
        state = module.get_admin_state(user_id)

        if data.startswith("adminx:"):
            if await module.handle_private_home_callback(update, context, state):
                return
            state, _, handled = await _guard_active_group(update, context, state)
            if handled:
                return
            if await admin_extra.handle_admin_extra_callback(update, context, state):
                return

        main_toggle_prefixes = ("admin:ar_toggle:", "admin:ad_toggle:", "admin:spam_type:")
        main_toggle_values = {
            "admin:verify_toggle",
            "admin:verify_private",
            "admin:welcome_toggle",
            "admin:welcome_delete_prev",
            "admin:aw_cmd_toggle",
            "admin:spam_toggle",
        }
        if data in main_toggle_values or any(data.startswith(prefix) for prefix in main_toggle_prefixes):
            if await module.handle_private_home_callback(update, context, state):
                return
            state, group_id, handled = await _guard_active_group(update, context, state)
            if handled:
                return
            if await _handle_main_toggle(update, context, state, group_id, data):
                return

        await original_admin_callback(update, context)

    _base_handle_admin_extra_callback = handle_admin_extra_callback
    _base_admin_callback = admin_callback

    async def handle_verification_failure(context, chat, user, cfg, reason: str = "timeout"):
        del reason
        payload = _get_rich_message_target(chat.id, "verify_fail") or _normalize_message_payload({})
        if payload.get("text") or payload.get("photo_file_id") or payload.get("buttons"):
            try:
                rendered = verify_service.render_template(payload.get("text", ""), user, chat)
                await _send_group_rich_message(context, chat.id, payload, RICH_BUTTON_CALLBACK_PREFIXES["verify_fail"], rendered)
            except verify_service.TelegramError:
                pass
        action = cfg.get("verify_fail_action", "mute")
        try:
            if action == "ban":
                await context.bot.ban_chat_member(chat_id=chat.id, user_id=user.id)
            elif action == "kick":
                await context.bot.ban_chat_member(chat_id=chat.id, user_id=user.id)
                await context.bot.unban_chat_member(chat_id=chat.id, user_id=user.id)
            elif action == "mute":
                await context.bot.restrict_chat_member(chat_id=chat.id, user_id=user.id, permissions=verify_service.muted_permissions())
        except verify_service.TelegramError:
            pass

    async def apply_warn(context, chat, user, cfg):
        data = auto_warn_service.increment_warn(chat.id, user.id)
        limit = int(cfg.get("warn_limit", 3))
        count = int(data.get("count", 0))
        payload = _get_rich_message_target(chat.id, "autowarn") or _normalize_message_payload({})
        if payload.get("text") or payload.get("photo_file_id") or payload.get("buttons"):
            try:
                rendered = auto_warn_service.render_template(payload.get("text", ""), user, chat, {"count": count, "limit": limit})
                await _send_group_rich_message(context, chat.id, payload, RICH_BUTTON_CALLBACK_PREFIXES["autowarn"], rendered)
            except auto_warn_service.TelegramError:
                pass
        if count >= limit:
            action = cfg.get("action", "mute")
            try:
                if action == "kick":
                    await context.bot.ban_chat_member(chat_id=chat.id, user_id=user.id)
                    await context.bot.unban_chat_member(chat_id=chat.id, user_id=user.id)
                else:
                    mute_seconds = int(cfg.get("mute_seconds", 86400))
                    await context.bot.restrict_chat_member(
                        chat_id=chat.id,
                        user_id=user.id,
                        permissions=auto_warn_service.muted_permissions(),
                        until_date=int(time.time()) + mute_seconds,
                    )
            except auto_warn_service.TelegramError as exc:
                auto_warn_service.logger.warning("auto_warn action failed: %s", exc)
            return True
        return False

    async def _apply_invite_success(context, chat, member, invite_cfg: dict, inviter_id: int):
        if inviter_id == member.id:
            return
        extra_features.increment_invite_stats(chat.id, inviter_id)
        reward_points = int(invite_cfg.get("reward_points", 0) or 0)
        if reward_points > 0:
            extra_features.add_points(chat.id, inviter_id, reward_points)
        if invite_cfg.get("notify_enabled"):
            payload = _normalize_message_payload(
                {
                    "text": invite_cfg.get("notify_text") or "{userName} \u901a\u8fc7\u9080\u8bf7\u94fe\u63a5\u52a0\u5165\u7fa4\u7ec4",
                    "photo_file_id": invite_cfg.get("notify_photo_file_id") or "",
                    "buttons": invite_cfg.get("notify_buttons") or [],
                }
            )
            try:
                rendered = verify_service.render_template(payload.get("text", ""), member, chat)
                await _send_group_rich_message(context, chat.id, payload, RICH_BUTTON_CALLBACK_PREFIXES["invite_notify"], rendered)
            except extra_features.TelegramError:
                pass

    async def handle_related_channel_message(context, message, chat) -> bool:
        cfg = extra_features.get_group_config(chat.id).get("related_channel", {})
        pinned_message = getattr(message, "pinned_message", None)
        if cfg.get("cancel_top_pin") and pinned_message and extra_features._is_related_channel_forward(pinned_message):
            handled = False
            try:
                await context.bot.unpin_chat_message(chat_id=chat.id, message_id=pinned_message.message_id)
                handled = True
            except extra_features.TelegramError:
                try:
                    await context.bot.unpin_chat_message(chat_id=chat.id)
                    handled = True
                except extra_features.TelegramError:
                    pass
            try:
                await context.bot.delete_message(chat_id=chat.id, message_id=message.message_id)
                handled = True
            except extra_features.TelegramError:
                pass
            return handled
        if cfg.get("occupy_comment") and getattr(message, "is_automatic_forward", False):
            payload = _normalize_message_payload(
                {
                    "text": cfg.get("occupy_comment_text") or "\u62a2\u5360\u8bc4\u8bba\u533a",
                    "photo_file_id": cfg.get("occupy_comment_photo_file_id") or "",
                    "buttons": cfg.get("occupy_comment_buttons") or [],
                }
            )
            try:
                rendered = verify_service.render_template(payload.get("text", ""), None, chat)
                await _reply_rich_message(message, payload, RICH_BUTTON_CALLBACK_PREFIXES["related_comment"], rendered)
                return True
            except extra_features.TelegramError:
                return False
        return False

    async def handle_admin_extra_callback(update, context, state: dict) -> bool:
        query = getattr(update, "callback_query", None)
        data = getattr(query, "data", "") or ""
        if not data.startswith("adminx:"):
            return False

        user_id = update.effective_user.id
        group_id = admin_extra._group_id(state)

        if data.startswith("adminx:cancel_input:"):
            current_state = str((state or {}).get("state") or "")
            if current_state.startswith("x:rich:"):
                target = current_state.split(":", 3)[-1]
                new_state = admin_extra._state_with(state, state=None, tmp={})
                admin_extra._save_state(user_id, new_state)
                await show_rich_message_editor(update, context, new_state, target)
                return True

        if group_id:
            if data == "adminx:invite:prompt:notify_text":
                await show_rich_message_editor(update, context, state, "invite_notify")
                return True
            if data == "adminx:related:prompt:occupy_comment_text":
                await show_rich_message_editor(update, context, state, "related_comment")
                return True
            if data.startswith("adminx:schedule:edit:"):
                items = admin_extra.load_schedule_items(group_id)
                try:
                    idx = int(data.split(":")[-1])
                except ValueError:
                    idx = -1
                if not (0 <= idx < len(items)):
                    if query:
                        await admin_extra.safe_answer(query, "\u5b9a\u65f6\u6d88\u606f\u4e0d\u5b58\u5728", show_alert=True)
                    return True
                await show_rich_message_editor(update, context, state, f"schedule:{int(items[idx].get('id') or 0)}")
                return True
            if data.startswith("adminx:rich:schedule:toggle:"):
                try:
                    schedule_id = int(data.split(":")[-1])
                except ValueError:
                    schedule_id = 0
                idx, item, items = _get_schedule_item_by_id(group_id, schedule_id)
                if item is None or idx < 0:
                    if query:
                        await admin_extra.safe_answer(query, "\u5b9a\u65f6\u6d88\u606f\u4e0d\u5b58\u5728", show_alert=True)
                    return True
                item["enabled"] = not item.get("enabled", True)
                items[idx] = item
                save_schedule_items(group_id, items)
                if query:
                    await admin_extra.safe_answer(query, _toggle_notice(bool(item.get("enabled", True))), show_alert=True)
                await show_rich_message_editor(update, context, state, f"schedule:{schedule_id}")
                return True
            if data.startswith("adminx:rich:schedule:delete:"):
                try:
                    schedule_id = int(data.split(":")[-1])
                except ValueError:
                    schedule_id = 0
                idx, item, items = _get_schedule_item_by_id(group_id, schedule_id)
                if item is None or idx < 0:
                    if query:
                        await admin_extra.safe_answer(query, "\u5b9a\u65f6\u6d88\u606f\u4e0d\u5b58\u5728", show_alert=True)
                    return True
                items.pop(idx)
                save_schedule_items(group_id, items)
                if query:
                    await admin_extra.safe_answer(query, "\u5df2\u5220\u9664", show_alert=True)
                await show_schedule_list_menu(update, context, state)
                return True
            if data.startswith("adminx:rich:"):
                parts = data.split(":")
                action = parts[2] if len(parts) >= 3 else ""
                target = ":".join(parts[3:]) if len(parts) >= 4 else ""
                if action == "text":
                    meta = _rich_target_meta(target)
                    if meta:
                        await admin_extra._begin_input(
                            update,
                            user_id,
                            state,
                            f"x:rich:text:{target}",
                            meta["prompt_module"],
                            "\u8bf7\u8f93\u5165\u6d88\u606f\u6587\u672c",
                        )
                    return True
                if action == "photo":
                    meta = _rich_target_meta(target)
                    if meta:
                        await admin_extra._begin_input(
                            update,
                            user_id,
                            state,
                            f"x:rich:photo:{target}",
                            meta["prompt_module"],
                            "\u8bf7\u53d1\u9001\u6d88\u606f\u56fe\u7247",
                        )
                    return True
                if action == "clear_photo":
                    payload = _get_rich_message_target(group_id, target)
                    if payload is None:
                        if query:
                            await admin_extra.safe_answer(query, "\u6d88\u606f\u914d\u7f6e\u4e0d\u5b58\u5728", show_alert=True)
                        return True
                    payload["photo_file_id"] = ""
                    _save_rich_message_target(group_id, target, payload)
                    await show_rich_message_editor(update, context, state, target)
                    return True
                if action == "buttons":
                    new_state = module._state_with(state, state=module.STATE_BTN_TEXT, tmp={"group_id": group_id, "btn_target": f"rich:{target}"})
                    module._save_state(user_id, new_state)
                    await admin_extra._send_or_edit(update, "\u8bf7\u8f93\u5165\u6309\u94ae\u6587\u5b57")
                    return True

        return await _base_handle_admin_extra_callback(update, context, state)

    async def admin_callback(update, context):
        query = update.callback_query
        data = getattr(query, "data", "") or ""
        user_id = query.from_user.id
        state = module.get_admin_state(user_id)

        if data in {"admin:verify_fail_text", "admin:aw_text"}:
            if await module.handle_private_home_callback(update, context, state):
                return
            state, _, handled = await _guard_active_group(update, context, state)
            if handled:
                return
            target = "verify_fail" if data == "admin:verify_fail_text" else "autowarn"
            await show_rich_message_editor(update, context, state, target)
            return

        await _base_admin_callback(update, context)

    schedule_draft_store: dict[int, dict] = {}

    def _draft_target(user_id: int) -> str:
        return f"schedule_draft:{int(user_id)}"

    def _parse_draft_target(target: str) -> int | None:
        if not str(target).startswith("schedule_draft:"):
            return None
        try:
            return int(str(target).split(":", 1)[1])
        except (IndexError, ValueError):
            return None

    def _normalize_schedule_draft(data: dict | None = None) -> dict:
        payload = _normalize_message_payload(data)
        try:
            interval_sec = int((data or {}).get("interval_sec", 3600) or 3600)
        except (TypeError, ValueError):
            interval_sec = 3600
        payload["interval_sec"] = max(60, interval_sec)
        return payload

    _base_get_rich_message_target = _get_rich_message_target

    def _get_rich_message_target(group_id: int, target: str):
        draft_user_id = _parse_draft_target(target)
        if draft_user_id is not None:
            return _normalize_schedule_draft(schedule_draft_store.get(draft_user_id))
        return _base_get_rich_message_target(group_id, target)

    _base_save_rich_message_target = _save_rich_message_target

    def _save_rich_message_target(group_id: int, target: str, payload: dict) -> bool:
        draft_user_id = _parse_draft_target(target)
        if draft_user_id is not None:
            current = schedule_draft_store.get(draft_user_id) or {}
            current.update(_normalize_schedule_draft(payload))
            schedule_draft_store[draft_user_id] = _normalize_schedule_draft(current)
            return True
        return _base_save_rich_message_target(group_id, target, payload)

    _base_rich_target_meta = _rich_target_meta

    def _rich_target_meta(target: str) -> dict | None:
        if _parse_draft_target(target) is not None:
            return {
                "title": "\u65b0\u5efa\u5b9a\u65f6\u6d88\u606f",
                "back": "adminx:schedule:menu",
                "prompt_module": "schedule",
                "hint": "\u5b9a\u65f6\u6d88\u606f\u4ee5\u5355\u6761\u6d88\u606f\u53d1\u9001\uff1a\u56fe\u7247\u5728\u9876\u90e8\uff0c\u6587\u672c\u5728\u4e2d\u95f4\uff0c\u6309\u94ae\u5728\u4e0b\u65b9\u3002",
            }
        return _base_rich_target_meta(target)

    _base_show_rich_message_editor = show_rich_message_editor

    async def show_rich_message_editor(update, context, state: dict, target: str):
        del context
        group_id = module._current_group(state) or admin_extra._group_id(state)
        draft_user_id = _parse_draft_target(target)
        if draft_user_id is None and not str(target).startswith("schedule:"):
            return await _base_show_rich_message_editor(update, None, state, target)

        meta = _rich_target_meta(target)
        payload = _get_rich_message_target(group_id, target) if group_id else None
        if not meta or payload is None:
            await admin_extra._send_or_edit(update, "\u6d88\u606f\u914d\u7f6e\u4e0d\u5b58\u5728\u6216\u5df2\u88ab\u5220\u9664\u3002")
            return

        interval_min = max(1, int(payload.get("interval_sec", 3600) or 3600) // 60)
        lines = [
            meta["title"],
            f"\u6587\u672c: {html.escape(_preview_text(payload.get('text', ''), limit=120))}",
            f"\u56fe\u7247: {'\u5df2\u8bbe\u7f6e' if payload.get('photo_file_id') else '\u672a\u8bbe\u7f6e'}",
            f"\u6309\u94ae\u6570\u91cf: {len(payload.get('buttons', []) or [])}",
            f"\u95f4\u9694: \u6bcf {interval_min} \u5206\u949f",
            meta["hint"],
        ]
        rows = [
            [
                admin_extra._btn("\u8bbe\u7f6e\u6587\u672c", f"adminx:rich:text:{target}"),
                admin_extra._btn("\u8bbe\u7f6e\u56fe\u7247", f"adminx:rich:photo:{target}"),
            ],
            [
                admin_extra._btn("\u6e05\u9664\u56fe\u7247", f"adminx:rich:clear_photo:{target}"),
                admin_extra._btn("\u8bbe\u7f6e\u6309\u94ae", f"adminx:rich:buttons:{target}"),
            ],
            [admin_extra._btn("\u8bbe\u7f6e\u95f4\u9694", f"adminx:rich:interval:{target}")],
        ]

        if draft_user_id is not None:
            rows.append([admin_extra._btn("\u4fdd\u5b58\u5b9a\u65f6\u6d88\u606f", f"adminx:rich:save:{target}")])
        else:
            try:
                schedule_id = int(str(target).split(":", 1)[1])
            except (IndexError, ValueError):
                schedule_id = 0
            idx, item, _ = _get_schedule_item_by_id(group_id, schedule_id)
            if item is not None and idx >= 0:
                next_at = int(item.get("next_at", 0) or 0)
                next_text = time.strftime("%m-%d %H:%M", time.localtime(next_at)) if next_at > 0 else "\u672a\u5b89\u6392"
                lines.insert(1, f"\u72b6\u6001: {_status_label(bool(item.get('enabled', True)))}")
                lines.insert(5, f"\u4e0b\u6b21\u53d1\u9001: {next_text}")
            rows.append(
                [
                    admin_extra._btn("\u542f\u7528/\u505c\u7528", f"adminx:rich:schedule:toggle:{schedule_id}"),
                    admin_extra._btn("\u5220\u9664", f"adminx:rich:schedule:delete:{schedule_id}"),
                ]
            )
        rows.append([admin_extra._btn("\u2b05\ufe0f \u8fd4\u56de", meta["back"])])
        await admin_extra._send_or_edit(update, "\n".join(lines), admin_extra.InlineKeyboardMarkup(rows))

    _base_show_schedule_menu = show_schedule_menu

    async def show_schedule_menu(update, context, state: dict):
        del context
        group_id = admin_extra._group_id(state)
        items = admin_extra.load_schedule_items(group_id)
        enabled_count = sum(1 for item in items if item.get("enabled", True))
        limit = admin_extra.schedule_limit_for_group(group_id)
        lines = [
            "\u23f0 \u5b9a\u65f6\u6d88\u606f",
            "",
            f"\u5f53\u524d\u5957\u9910: {admin_extra.group_plan_label(group_id)}",
            f"\u5f53\u524d\u5171\u6709 {len(items)}/{limit} \u6761\u5b9a\u65f6\u6d88\u606f\uff0c\u5df2\u542f\u7528 {enabled_count} \u6761\u3002",
            "\u6dfb\u52a0\u65b0\u5b9a\u65f6\u6d88\u606f\u540e\uff0c\u4f1a\u8fdb\u5165\u7edf\u4e00\u7f16\u8f91\u5668\uff0c\u53ef\u5206\u522b\u8bbe\u7f6e\u6587\u672c\u3001\u56fe\u7247\u3001\u6309\u94ae\u548c\u53d1\u9001\u95f4\u9694\u3002",
            "\u6d88\u606f\u4ecd\u7136\u4ee5\u5355\u6761\u6d88\u606f\u53d1\u9001\uff1a\u56fe\u7247\u5728\u9876\u90e8\uff0c\u6587\u672c\u5728\u4e2d\u95f4\uff0c\u6309\u94ae\u5728\u4e0b\u65b9\u3002",
        ]
        if len(items) >= limit:
            lines.append("\u5df2\u8fbe\u5230\u5f53\u524d\u5957\u9910\u4e0a\u9650\uff0c\u7eed\u8d39\u540e\u624d\u53ef\u7ee7\u7eed\u65b0\u589e\u5b9a\u65f6\u6d88\u606f\u3002")
        rows = [
            [admin_extra._btn("\u6dfb\u52a0\u5b9a\u65f6\u6d88\u606f", "adminx:schedule:add")],
            [admin_extra._btn("\u5168\u90e8\u5b9a\u65f6\u6d88\u606f", "adminx:schedule:list")],
            [admin_extra._btn("\u8fd4\u56de", "admin:main")],
        ]
        await admin_extra._send_or_edit(update, "\n".join(lines), admin_extra.InlineKeyboardMarkup(rows))

    _base_handle_admin_extra_message_v2 = handle_admin_extra_message

    async def handle_admin_extra_message(update, context, state: dict, msg_text: str) -> bool:
        current_state = str((state or {}).get("state") or "")
        if current_state.startswith("x:schedule:interval:"):
            user_id = update.effective_user.id
            group_id = admin_extra._group_id(state)
            target = current_state.split("x:schedule:interval:", 1)[1]
            try:
                minutes = int((msg_text or "").strip())
            except ValueError:
                await update.effective_message.reply_text("\u8bf7\u8f93\u5165\u6574\u6570\u5206\u949f")
                return True
            interval_sec = max(60, minutes * 60)
            draft_user_id = _parse_draft_target(target)
            if draft_user_id is not None:
                draft = _normalize_schedule_draft(schedule_draft_store.get(draft_user_id))
                draft["interval_sec"] = interval_sec
                schedule_draft_store[draft_user_id] = draft
            else:
                try:
                    schedule_id = int(str(target).split(":", 1)[1])
                except (IndexError, ValueError):
                    schedule_id = 0
                idx, item, items = _get_schedule_item_by_id(group_id, schedule_id)
                if item is None or idx < 0:
                    await update.effective_message.reply_text("\u5b9a\u65f6\u6d88\u606f\u4e0d\u5b58\u5728")
                    return True
                item["interval_sec"] = interval_sec
                item["next_at"] = int(time.time()) + interval_sec
                items[idx] = _normalize_schedule_item(item, default_next_at=item["next_at"])
                save_schedule_items(group_id, items)
            new_state = admin_extra._state_with(state, state=None, tmp={})
            admin_extra._save_state(user_id, new_state)
            await show_rich_message_editor(update, context, new_state, target)
            return True
        return await _base_handle_admin_extra_message_v2(update, context, state, msg_text)

    _base_handle_admin_extra_callback_v2 = handle_admin_extra_callback

    async def handle_admin_extra_callback(update, context, state: dict) -> bool:
        query = getattr(update, "callback_query", None)
        data = getattr(query, "data", "") or ""
        if not data.startswith("adminx:"):
            return False

        user_id = update.effective_user.id
        group_id = admin_extra._group_id(state)
        target_draft = _draft_target(user_id)

        if data.startswith("adminx:cancel_input:"):
            current_state = str((state or {}).get("state") or "")
            if current_state.startswith("x:schedule:interval:"):
                target = current_state.split("x:schedule:interval:", 1)[1]
                new_state = admin_extra._state_with(state, state=None, tmp={})
                admin_extra._save_state(user_id, new_state)
                await show_rich_message_editor(update, context, new_state, target)
                return True

        if group_id and data == "adminx:schedule:add":
            items = admin_extra.load_schedule_items(group_id)
            limit = admin_extra.schedule_limit_for_group(group_id)
            if len(items) >= limit:
                if query:
                    await admin_extra.safe_answer(query, f"\u5f53\u524d\u5957\u9910\u6700\u591a\u652f\u6301 {limit} \u6761\u5b9a\u65f6\u6d88\u606f", show_alert=True)
                await show_schedule_menu(update, context, state)
                return True
            schedule_draft_store[user_id] = _normalize_schedule_draft({"interval_sec": 3600})
            await show_rich_message_editor(update, context, state, target_draft)
            return True

        if group_id and data.startswith("adminx:rich:interval:"):
            target = data.split("adminx:rich:interval:", 1)[1]
            meta = _rich_target_meta(target)
            if meta:
                new_state = admin_extra._state_with(state, state=f"x:schedule:interval:{target}", tmp={})
                admin_extra._save_state(user_id, new_state)
                await admin_extra._send_or_edit(update, "\u8bf7\u8f93\u5165\u53d1\u9001\u95f4\u9694\u5206\u949f\u6570", admin_extra._prompt_markup(meta["prompt_module"]))
                return True

        if group_id and data.startswith("adminx:rich:save:"):
            target = data.split("adminx:rich:save:", 1)[1]
            draft_user_id = _parse_draft_target(target)
            if draft_user_id is not None:
                items = admin_extra.load_schedule_items(group_id)
                limit = admin_extra.schedule_limit_for_group(group_id)
                if len(items) >= limit:
                    if query:
                        await admin_extra.safe_answer(query, f"\u5f53\u524d\u5957\u9910\u6700\u591a\u652f\u6301 {limit} \u6761\u5b9a\u65f6\u6d88\u606f", show_alert=True)
                    await show_schedule_menu(update, context, state)
                    return True
                draft = _normalize_schedule_draft(schedule_draft_store.get(draft_user_id))
                if not draft.get("text") and not draft.get("photo_file_id") and not draft.get("buttons"):
                    if query:
                        await admin_extra.safe_answer(query, "\u5b9a\u65f6\u6d88\u606f\u81f3\u5c11\u8981\u6709\u6587\u672c\u3001\u56fe\u7247\u6216\u6309\u94ae", show_alert=True)
                    await show_rich_message_editor(update, context, state, target)
                    return True
                item = _normalize_schedule_item(
                    {
                        "text": draft.get("text") or "",
                        "photo_file_id": draft.get("photo_file_id") or "",
                        "buttons": draft.get("buttons") or [],
                        "interval_sec": draft.get("interval_sec") or 3600,
                    },
                    default_next_at=int(time.time()),
                )
                items.append(item)
                save_schedule_items(group_id, items)
                schedule_draft_store.pop(draft_user_id, None)
                new_state = admin_extra._state_with(state, state=None, tmp={})
                admin_extra._save_state(user_id, new_state)
                if query:
                    await admin_extra.safe_answer(query, "\u5df2\u4fdd\u5b58", show_alert=True)
                await show_schedule_list_menu(update, context, new_state)
                return True

        return await _base_handle_admin_extra_callback_v2(update, context, state)

    admin_extra._send_or_edit = _extra_send_or_edit
    admin_extra._toggle_config = _toggle_extra_config
    admin_extra.handle_admin_extra_callback = original_extra_callback
    admin_extra.show_ad_filter_menu = show_ad_filter_menu
    admin_extra.show_command_gate_menu = show_command_gate_menu
    admin_extra.show_crypto_menu = show_crypto_menu
    admin_extra.show_invite_menu = show_invite_menu
    admin_extra.show_member_menu = show_member_menu
    admin_extra.show_related_menu = show_related_menu
    admin_extra.show_schedule_menu = original_show_schedule_menu
    admin_extra.show_schedule_list_menu = original_show_schedule_list_menu
    admin_extra.show_language_menu = show_language_menu
    admin_extra.show_verified_placeholder = show_verified_placeholder
    admin_extra.handle_admin_extra_message = original_extra_message
    admin_extra.MENU_ROUTES["ad"] = show_ad_filter_menu
    admin_extra.MENU_ROUTES["cmd"] = show_command_gate_menu
    admin_extra.MENU_ROUTES["crypto"] = show_crypto_menu
    admin_extra.MENU_ROUTES["invite"] = show_invite_menu
    admin_extra.MENU_ROUTES["member"] = show_member_menu
    admin_extra.MENU_ROUTES["related"] = show_related_menu
    admin_extra.MENU_ROUTES["schedule"] = original_show_schedule_menu
    admin_extra.MENU_ROUTES["lang"] = show_language_menu
    admin_extra.MENU_ROUTES["verified"] = show_verified_placeholder
    extra_features.parse_schedule_message_input = parse_schedule_message_input
    extra_features.load_schedule_items = load_schedule_items
    extra_features.save_schedule_items = save_schedule_items
    extra_features.process_scheduled_messages = process_scheduled_messages
    extra_features._apply_invite_success = _apply_invite_success
    admin_extra.parse_schedule_message_input = parse_schedule_message_input
    admin_extra.load_schedule_items = load_schedule_items
    admin_extra.save_schedule_items = save_schedule_items
    extra_features._fetch_tron_wallet_summary = _fetch_tron_wallet_summary
    extra_features._handle_crypto_commands = _handle_crypto_commands
    extra_features.handle_related_channel_message = handle_related_channel_message
    extra_features.handle_wallet_callback = handle_wallet_callback
    verify_service.handle_verification_failure = handle_verification_failure
    auto_warn_service.apply_warn = apply_warn
    module._send_or_edit = _module_send_or_edit
    module.handle_admin_extra_callback = handle_admin_extra_callback
    module._web_admin_base_url = _web_admin_base_url
    module.show_group_select = show_group_select
    module.show_verify_menu = show_verify_menu
    module.show_welcome_menu = show_welcome_menu
    module.show_autodelete_menu = show_autodelete_menu
    module.show_autoban_menu = show_autoban_menu
    module.show_automute_menu = show_automute_menu
    module.show_autowarn_menu = show_autowarn_menu
    module.show_antispam_menu = show_antispam_menu
    module.admin_message = admin_message
    module.admin_photo = admin_photo
    module.admin_callback = admin_callback
    callbacks.admin_callback = admin_callback
    callbacks.callback_router = callback_router
    module._runtime_patch_admin_applied = True
    return module

