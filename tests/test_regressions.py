import importlib
from copy import deepcopy
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram.error import TelegramError

sys.path.insert(0, ".")

from api import tg_bot
import api.web as web_api_module
import bot.handlers.admin as admin_module
import bot.handlers.admin_extra as admin_extra
import bot.handlers.callbacks as callbacks_module
import bot.services.extra_features as extra_features_module
import bot.services.verify as verify_service_module
import bot.web.schemas as web_schemas_module
import bot.web.service as web_service_module


class _MemoryKv:
    def __init__(self):
        self.data = {}

    def get_json(self, key, default=None):
        return self.data.get(key, default)

    def set_json(self, key, value):
        self.data[key] = value


class _FallbackUser:
    def __init__(self, user_id: int, full_name: str):
        self.id = user_id
        self.full_name = full_name

    def mention_html(self) -> str:
        return self.full_name


class _PrivateFallbackBot:
    def __init__(self):
        self.calls = []

    async def send_message(self, **kwargs):
        self.calls.append(kwargs["chat_id"])
        if kwargs["chat_id"] == 42:
            raise TelegramError("bot was blocked by the user")
        return SimpleNamespace(message_id=1)


class AdminExtraRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_schedule_add_initializes_draft_state(self):
        admin_extra.SCHEDULE_DRAFTS.clear()
        update = SimpleNamespace(
            callback_query=SimpleNamespace(data="adminx:schedule:add"),
            effective_user=SimpleNamespace(id=42),
            effective_message=None,
        )
        state = {"active_group_id": -100123, "state": None, "tmp": {}}

        with patch.object(admin_extra, "load_schedule_items", return_value=[]), patch.object(
            admin_extra,
            "schedule_limit_for_group",
            return_value=5,
        ), patch.object(admin_extra, "show_rich_message_editor", new=AsyncMock()) as show_editor:
            handled = await admin_extra.handle_admin_extra_callback(update, None, state)

        self.assertTrue(handled)
        self.assertEqual(admin_extra.SCHEDULE_DRAFTS[42]["interval_sec"], 3600)
        show_editor.assert_awaited_once()


class ScheduledMessageRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_process_scheduled_messages_does_not_advance_on_send_failure(self):
        extra_features = importlib.reload(extra_features_module)
        memory = _MemoryKv()
        item = {
            "id": 12345,
            "text": "hello",
            "photo_file_id": "",
            "buttons": [],
            "interval_sec": 120,
            "next_at": 1,
            "enabled": True,
        }
        key = extra_features._schedule_key(-100123)
        memory.data[key] = [item]
        bot = SimpleNamespace(
            send_message=AsyncMock(side_effect=TelegramError("boom")),
            send_photo=AsyncMock(side_effect=TelegramError("boom")),
        )
        context = SimpleNamespace(bot=bot)

        with patch.object(extra_features, "kv_get_json", side_effect=memory.get_json), patch.object(
            extra_features,
            "kv_set_json",
            side_effect=memory.set_json,
        ) as set_json, patch("bot.services.extra_features.time.time", return_value=1000):
            await extra_features.process_scheduled_messages(context, -100123)

        bot.send_message.assert_awaited_once()
        set_json.assert_not_called()
        self.assertEqual(memory.data[key][0]["next_at"], 1)


class VerifyPromptRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_verify_prompt_falls_back_to_group(self):
        verify_service = importlib.reload(verify_service_module)
        context = SimpleNamespace(bot=_PrivateFallbackBot())
        chat = SimpleNamespace(id=-100123, title="Test Group")
        user = _FallbackUser(42, "Tester")
        session = {"question": "1 + 1 = ?", "options": [1, 2], "correct_index": 1}

        sent = await verify_service.send_verify_prompt(
            context,
            chat,
            user,
            {},
            [],
            "calc",
            session,
            send_private=True,
        )

        self.assertTrue(sent)
        self.assertEqual(context.bot.calls, [42, -100123])


class WebhookSecurityRegressionTests(unittest.TestCase):
    def test_webhook_secret_requires_exact_match(self):
        with patch.object(tg_bot, "WEBHOOK_SECRET", "secret-token"):
            self.assertFalse(tg_bot.is_authorized_webhook_secret(None))
            self.assertFalse(tg_bot.is_authorized_webhook_secret(""))
            self.assertFalse(tg_bot.is_authorized_webhook_secret("wrong"))
            self.assertTrue(tg_bot.is_authorized_webhook_secret("secret-token"))

        with patch.object(tg_bot, "WEBHOOK_SECRET", ""):
            self.assertTrue(tg_bot.is_authorized_webhook_secret(None))


if __name__ == "__main__":
    unittest.main()

class InviteAndRelatedRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_apply_invite_success_sends_rich_message(self):
        extra_features = importlib.reload(extra_features_module)
        bot = SimpleNamespace(
            send_photo=AsyncMock(return_value=SimpleNamespace(message_id=10)),
            send_message=AsyncMock(return_value=SimpleNamespace(message_id=11)),
        )
        context = SimpleNamespace(bot=bot)
        chat = SimpleNamespace(id=-100123, title="Test Group")
        member = _FallbackUser(7, "Alice")
        invite_cfg = {
            "notify_enabled": True,
            "notify_text": "Welcome {userName}",
            "notify_photo_file_id": "PHOTO-1",
            "notify_buttons": [{"text": "More", "type": "callback", "value": "detail", "row": 0}],
            "reward_points": 5,
        }

        with patch.object(extra_features, "increment_invite_stats"), patch.object(extra_features, "add_points"):
            await extra_features._apply_invite_success(context, chat, member, invite_cfg, inviter_id=42)

        bot.send_photo.assert_awaited_once()
        kwargs = bot.send_photo.await_args.kwargs
        self.assertEqual(kwargs["chat_id"], -100123)
        self.assertEqual(kwargs["photo"], "PHOTO-1")
        self.assertEqual(kwargs["reply_markup"].inline_keyboard[0][0].callback_data, "ivb:-100123:0")
        self.assertIn("Alice", kwargs["caption"])

    async def test_handle_related_channel_message_replies_with_rich_message(self):
        extra_features = importlib.reload(extra_features_module)
        message = SimpleNamespace(
            is_automatic_forward=True,
            chat_id=-100123,
            chat=SimpleNamespace(id=-100123),
            reply_text=AsyncMock(return_value=SimpleNamespace(message_id=12)),
            reply_photo=AsyncMock(return_value=SimpleNamespace(message_id=13)),
        )
        chat = SimpleNamespace(id=-100123, title="Test Group")
        cfg = {
            "related_channel": {
                "occupy_comment": True,
                "occupy_comment_text": "Comment for {group}",
                "occupy_comment_photo_file_id": "",
                "occupy_comment_buttons": [{"text": "Ping", "type": "callback", "value": "pong", "row": 0}],
            }
        }

        with patch.object(extra_features, "get_group_config", return_value=cfg):
            handled = await extra_features.handle_related_channel_message(SimpleNamespace(bot=None), message, chat)

        self.assertTrue(handled)
        message.reply_text.assert_awaited_once()
        kwargs = message.reply_text.await_args.kwargs
        args = message.reply_text.await_args.args
        self.assertEqual(kwargs["reply_markup"].inline_keyboard[0][0].callback_data, "rcb:-100123:0")
        self.assertIn("Test Group", args[0])

    async def test_invite_notify_callback_uses_saved_value(self):
        callbacks = importlib.reload(callbacks_module)
        query = SimpleNamespace(data="ivb:-100123:0", answer=AsyncMock(), from_user=SimpleNamespace(id=42))
        update = SimpleNamespace(callback_query=query)
        cfg = {
            "invite_links": {
                "notify_buttons": [{"text": "Info", "type": "callback", "value": "details", "row": 0}]
            }
        }

        with patch.object(callbacks, "get_group_config", return_value=cfg):
            await callbacks.callback_router(update, SimpleNamespace())

        query.answer.assert_awaited_once_with("details", show_alert=True)

class RuntimeFeatureRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_record_message_metrics_updates_points_and_activity(self):
        extra_features = importlib.reload(extra_features_module)
        memory = _MemoryKv()
        message = SimpleNamespace(
            text="hello world",
            caption=None,
            photo=None,
            video=None,
            document=None,
            voice=None,
            audio=None,
        )
        user = SimpleNamespace(id=7)
        chat = SimpleNamespace(id=-100123)
        cfg = {
            "points": {
                "enabled": True,
                "chat_points_enabled": True,
                "chat_points_per_message": 2,
                "min_text_length": 3,
            },
            "activity": {"enabled": True},
        }

        with patch.object(extra_features, "kv_get_json", side_effect=memory.get_json), patch.object(
            extra_features,
            "kv_set_json",
            side_effect=memory.set_json,
        ), patch.object(extra_features, "get_group_config", return_value=cfg):
            await extra_features.record_message_metrics(message, user, chat)

        self.assertEqual(memory.data[extra_features._points_key(-100123, 7)]["balance"], 2)
        activity = memory.data[extra_features._activity_key(-100123, 7)]
        self.assertEqual(activity["today"], 1)
        self.assertEqual(activity["month"], 1)
        self.assertEqual(activity["total"], 1)

    async def test_handle_group_commands_supports_sign_in(self):
        extra_features = importlib.reload(extra_features_module)
        memory = _MemoryKv()
        bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=20)))
        context = SimpleNamespace(bot=bot)
        message = SimpleNamespace(text="签到", caption=None, message_id=9)
        user = _FallbackUser(7, "Alice")
        chat = SimpleNamespace(id=-100123)
        cfg = {
            "points": {
                "enabled": True,
                "sign_command": "签到",
                "query_command": "积分",
                "rank_command": "积分排行",
                "sign_points": 5,
            },
            "activity": {"enabled": False},
            "invite_links": {"enabled": False},
            "crypto": {},
        }

        with patch.object(extra_features, "kv_get_json", side_effect=memory.get_json), patch.object(
            extra_features,
            "kv_set_json",
            side_effect=memory.set_json,
        ), patch.object(extra_features, "get_group_config", return_value=cfg):
            handled = await extra_features.handle_group_commands(context, message, user, chat, False)

        self.assertTrue(handled)
        self.assertEqual(memory.data[extra_features._points_key(-100123, 7)]["balance"], 5)
        bot.send_message.assert_awaited_once()
        self.assertIn("签到成功", bot.send_message.await_args.kwargs["text"])

    async def test_handle_group_commands_supports_invite_rankings(self):
        extra_features = importlib.reload(extra_features_module)
        memory = _MemoryKv()
        memory.data[extra_features._invite_users_key(-100123)] = [7, 8]
        memory.data[extra_features._invite_stats_key(-100123, 7)] = {
            "today": 1,
            "month": 2,
            "total": 5,
            "day_stamp": extra_features._day_stamp(),
            "month_stamp": extra_features._month_stamp(),
        }
        memory.data[extra_features._invite_stats_key(-100123, 8)] = {
            "today": 3,
            "month": 4,
            "total": 9,
            "day_stamp": extra_features._day_stamp(),
            "month_stamp": extra_features._month_stamp(),
        }

        async def _get_chat_member(chat_id, user_id):
            names = {7: "Alice", 8: "Bob"}
            return SimpleNamespace(user=SimpleNamespace(full_name=names[user_id]))

        bot = SimpleNamespace(
            send_message=AsyncMock(return_value=SimpleNamespace(message_id=21)),
            get_chat_member=AsyncMock(side_effect=_get_chat_member),
        )
        context = SimpleNamespace(bot=bot)
        message = SimpleNamespace(text="总邀请排行", caption=None, message_id=10)
        user = _FallbackUser(7, "Alice")
        chat = SimpleNamespace(id=-100123)
        cfg = {
            "points": {"enabled": False},
            "activity": {"enabled": False},
            "invite_links": {
                "enabled": True,
                "query_command": "/link",
                "today_rank_command": "今日邀请排行",
                "month_rank_command": "本月邀请排行",
                "total_rank_command": "总邀请排行",
                "auto_delete_sec": 0,
            },
            "crypto": {},
        }

        with patch.object(extra_features, "kv_get_json", side_effect=memory.get_json), patch.object(
            extra_features,
            "kv_set_json",
            side_effect=memory.set_json,
        ), patch.object(extra_features, "get_group_config", return_value=cfg):
            handled = await extra_features.handle_group_commands(context, message, user, chat, False)

        self.assertTrue(handled)
        text = bot.send_message.await_args.kwargs["text"]
        self.assertIn("Bob", text)
        self.assertIn("Alice", text)
        self.assertLess(text.index("Bob"), text.index("Alice"))

    async def test_handle_ad_filter_deletes_spam_message(self):
        extra_features = importlib.reload(extra_features_module)
        bot = SimpleNamespace(delete_message=AsyncMock())
        context = SimpleNamespace(bot=bot)
        message = SimpleNamespace(
            text="https://spam.example",
            caption=None,
            entities=None,
            caption_entities=None,
            document=None,
            contact=None,
            chat=SimpleNamespace(id=-100123),
            message_id=30,
            sender_chat=None,
            is_automatic_forward=False,
            sticker=None,
        )
        user = SimpleNamespace(full_name="Normal User", username="normal")
        chat = SimpleNamespace(id=-100123)
        cfg = {"ad_filter": {"message_enabled": True}}

        with patch.object(extra_features, "get_group_config", return_value=cfg):
            handled = await extra_features.handle_ad_filter(context, message, user, chat, False)

        self.assertTrue(handled)
        bot.delete_message.assert_awaited_once_with(chat_id=-100123, message_id=30)

    async def test_handle_language_whitelist_deletes_disallowed_language(self):
        extra_features = importlib.reload(extra_features_module)
        bot = SimpleNamespace(delete_message=AsyncMock())
        context = SimpleNamespace(bot=bot)
        message = SimpleNamespace(text="hello world", caption=None, chat=SimpleNamespace(id=-100123), message_id=31)
        chat = SimpleNamespace(id=-100123)
        cfg = {"language_whitelist": {"enabled": True, "allowed": ["zh"]}}

        with patch.object(extra_features, "get_group_config", return_value=cfg):
            handled = await extra_features.handle_language_whitelist(context, message, chat, False)

        self.assertTrue(handled)
        bot.delete_message.assert_awaited_once_with(chat_id=-100123, message_id=31)

    async def test_handle_nsfw_filter_deletes_and_notices(self):
        extra_features = importlib.reload(extra_features_module)
        bot = SimpleNamespace(
            delete_message=AsyncMock(),
            send_message=AsyncMock(return_value=SimpleNamespace(message_id=32)),
        )
        context = SimpleNamespace(bot=bot)
        message = SimpleNamespace(text="free porn now", caption=None, chat=SimpleNamespace(id=-100123), message_id=33)
        chat = SimpleNamespace(id=-100123)
        cfg = {"nsfw": {"enabled": True, "notice_enabled": True, "delay_delete_sec": 0}}

        with patch.object(extra_features, "get_group_config", return_value=cfg):
            handled = await extra_features.handle_nsfw_filter(context, message, chat, False)

        self.assertTrue(handled)
        bot.delete_message.assert_awaited_once_with(chat_id=-100123, message_id=33)
        bot.send_message.assert_awaited_once()
        self.assertIn("NSFW", bot.send_message.await_args.kwargs["text"])
class CryptoAndMenuRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_spot_summary_formats_binance_payload(self):
        extra_features = importlib.reload(extra_features_module)
        payload = {
            "lastPrice": "65000.12",
            "priceChangePercent": "2.5",
            "highPrice": "66000.00",
            "lowPrice": "63000.00",
            "volume": "1234.56",
        }

        with patch.object(extra_features, "_fetch_json", new=AsyncMock(return_value=payload)):
            text = await extra_features.fetch_spot_summary("btc")

        self.assertIn("BTC/USDT", text)
        self.assertIn("Last: 65000.12 USDT", text)
        self.assertIn("24h: +2.50%", text)

    async def test_handle_group_commands_supports_crypto_query_alias(self):
        extra_features = importlib.reload(extra_features_module)
        message = SimpleNamespace(text="q eth", caption=None, reply_text=AsyncMock())
        context = SimpleNamespace()
        user = SimpleNamespace(id=7)
        chat = SimpleNamespace(id=-100123)
        cfg = {
            "command_gate": {},
            "points": {"enabled": False},
            "activity": {"enabled": False},
            "invite_links": {"enabled": False},
            "crypto": {
                "price_query_enabled": True,
                "query_alias": "q",
                "default_symbol": "BTC",
                "wallet_query_enabled": True,
            },
        }

        with patch.object(extra_features, "get_group_config", return_value=cfg), patch.object(
            extra_features,
            "fetch_spot_summary",
            new=AsyncMock(return_value="ETH/USDT"),
        ):
            handled = await extra_features.handle_group_commands(context, message, user, chat, False)

        self.assertTrue(handled)
        message.reply_text.assert_awaited_once_with("ETH/USDT")

    async def test_handle_group_commands_enforces_command_gate(self):
        extra_features = importlib.reload(extra_features_module)
        bot = SimpleNamespace(delete_message=AsyncMock())
        context = SimpleNamespace(bot=bot)
        message = SimpleNamespace(
            text="/ban 42",
            caption=None,
            chat=SimpleNamespace(id=-100123),
            message_id=44,
        )
        user = SimpleNamespace(id=7)
        chat = SimpleNamespace(id=-100123)
        cfg = {
            "command_gate": {"ban": True},
            "points": {"enabled": False},
            "activity": {"enabled": False},
            "invite_links": {"enabled": False},
            "crypto": {},
        }

        with patch.object(extra_features, "get_group_config", return_value=cfg):
            handled = await extra_features.handle_group_commands(context, message, user, chat, False)

        self.assertTrue(handled)
        bot.delete_message.assert_awaited_once_with(chat_id=-100123, message_id=44)

    async def test_verified_menu_exposes_enabled_toggle(self):
        update = SimpleNamespace(callback_query=None, effective_message=None)
        state = {"active_group_id": -100123}
        cfg = {"verified_user": {"enabled": True}}

        with patch.object(admin_extra, "get_group_config", return_value=cfg), patch.object(
            admin_extra,
            "_send_or_edit",
            new=AsyncMock(),
        ) as send_ui:
            await admin_extra.show_verified_placeholder(update, None, state)

        args = send_ui.await_args.args
        self.assertIn("Module enabled", args[1])
        self.assertEqual(args[2].inline_keyboard[0][0].callback_data, "adminx:verified:toggle:enabled")
class WebServiceRegressionTests(unittest.TestCase):
    def test_build_group_summary_reports_verified_state(self):
        web_service = importlib.reload(web_service_module)
        cfg = {"group_title": "Test Group", "verified_user": {"enabled": True}}

        with patch.object(web_service, "get_group_config", return_value=cfg):
            summary = web_service.build_group_summary(-100123)

        verified = next(item for item in summary["modules"] if item["key"] == "verified")
        self.assertNotEqual(verified["summary"], "placeholder")
        self.assertIn("enabled", verified["summary"])

    def test_build_group_summary_includes_runtime_preview_for_overview(self):
        web_service = importlib.reload(web_service_module)
        cfg = {
            "group_title": "Test Group",
            "verify_private": True,
            "command_gate": {"sign": True, "warn": True},
            "points": {"chat_points_enabled": True},
            "activity": {"today_command": "/today"},
            "entertainment": {"dice_enabled": True, "gomoku_enabled": True},
            "usdt_price": {"tier": "best", "exchanges": ["binance", "okx"]},
            "invite_links": {"reward_points": 9},
            "lottery": {"query_command": "/lottery"},
            "verified_user": {"enabled": True},
            "admin_access": {"mode": "service_owner"},
            "nsfw": {"enabled": True, "notice_enabled": True, "sensitivity": "high", "allow_miss": False},
        }
        kv_map = {
            web_service._points_users_key(-100123): [1, 2, 3],
            web_service._activity_users_key(-100123): [1, 2],
            web_service._invite_users_key(-100123): [5],
        }

        with patch.object(web_service, "get_group_config", return_value=cfg), patch.object(
            web_service,
            "get_verify_session_users",
            return_value=[10, 11],
        ), patch.object(web_service, "get_welcome_queue", return_value=[]), patch.object(
            web_service,
            "get_group_auto_replies",
            return_value=[{"enabled": True}, {"enabled": False}],
        ), patch.object(web_service, "get_group_auto_delete", return_value={"delete_links": True, "custom_rules": [{"keyword": "spam"}]}), patch.object(
            web_service,
            "get_group_auto_ban",
            return_value={"rules": [{"keyword": "spam"}], "default_duration_sec": 3600},
        ), patch.object(web_service, "get_group_auto_mute", return_value={"rules": [{"keyword": "flood"}], "default_duration_sec": 90}), patch.object(
            web_service,
            "get_group_auto_warn",
            return_value={"warn_limit": 4, "rules": [{"keyword": "rude"}]},
        ), patch.object(web_service, "get_group_anti_spam", return_value={"enabled": True, "action": "ban", "threshold": 5, "types": ["text", "photo"]}), patch.object(
            web_service,
            "load_schedule_items",
            return_value=[{"enabled": True}, {"enabled": False}],
        ), patch.object(web_service, "auto_reply_limit_for_group", return_value=10), patch.object(
            web_service,
            "schedule_limit_for_group",
            return_value=5,
        ), patch.object(web_service, "group_service_owner_id", return_value=42), patch.object(
            web_service,
            "has_active_membership",
            return_value=True,
        ), patch.object(web_service, "get_active_gomoku_game", return_value={"status": "playing", "creator_id": 1, "challenger_id": 2}), patch.object(
            web_service,
            "get_active_lottery",
            return_value={"participants": [1, 2, 3], "winner_count": 1},
        ), patch.object(web_service, "kv_get_json", side_effect=lambda key, default=None: kv_map.get(key, default if default is not None else [])):
            summary = web_service.build_group_summary(-100123)

        verify = next(item for item in summary["modules"] if item["key"] == "verify")
        points = next(item for item in summary["modules"] if item["key"] == "points")
        fun = next(item for item in summary["modules"] if item["key"] == "fun")
        lottery = next(item for item in summary["modules"] if item["key"] == "lottery")
        verified = next(item for item in summary["modules"] if item["key"] == "verified")

        self.assertEqual(verify["runtime_preview"], ["2 pending", "private delivery"])
        self.assertEqual(points["runtime_preview"], ["3 tracked", "chat points on"])
        self.assertEqual(fun["runtime_preview"], ["dice on", "gomoku playing"])
        self.assertEqual(lottery["runtime_preview"], ["active lottery", "3 participants"])
        self.assertEqual(verified["runtime_preview"], ["enabled"])
        self.assertEqual(verify["runtime_alerts"], ["No verify targets configured"])
    def test_build_group_summary_includes_runtime_alerts_for_risky_modules(self):
        web_service = importlib.reload(web_service_module)
        cfg = {
            "group_title": "Risk Group",
            "verify_enabled": True,
            "verify_private": False,
            "admin_access": {"mode": "service_owner"},
            "schedule": {"enabled": True},
        }

        with patch.object(web_service, "get_group_config", return_value=cfg), patch.object(
            web_service,
            "get_group_targets",
            return_value=[],
        ), patch.object(web_service, "group_service_owner_id", return_value=0), patch.object(
            web_service,
            "has_active_membership",
            return_value=False,
        ), patch.object(web_service, "load_schedule_items", return_value=[{"enabled": False}, {"enabled": False}, {"enabled": False}]), patch.object(
            web_service,
            "schedule_limit_for_group",
            return_value=3,
        ):
            summary = web_service.build_group_summary(-100123)

        verify = next(item for item in summary["modules"] if item["key"] == "verify")
        admin_access = next(item for item in summary["modules"] if item["key"] == "admin_access")
        schedule = next(item for item in summary["modules"] if item["key"] == "schedule")

        self.assertEqual(verify["runtime_alerts"], ["No verify targets configured"])
        self.assertEqual(admin_access["runtime_alerts"], ["Service owner is not bound"])
        self.assertEqual(schedule["runtime_alerts"], ["Schedule limit reached", "All schedule items are disabled"])
        self.assertEqual(verify["runtime_alert_details"], [{"severity": "error", "message": "No verify targets configured"}])
        self.assertEqual(admin_access["runtime_alert_details"], [{"severity": "warning", "message": "Service owner is not bound"}])
        self.assertEqual(schedule["runtime_alert_details"], [{"severity": "warning", "message": "Schedule limit reached"}, {"severity": "info", "message": "All schedule items are disabled"}])

class UsdtPriceRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_group_commands_supports_usdt_query_alias(self):
        extra_features = importlib.reload(extra_features_module)
        message = SimpleNamespace(text="z", caption=None, reply_text=AsyncMock())
        context = SimpleNamespace()
        user = SimpleNamespace(id=7)
        chat = SimpleNamespace(id=-100123)
        cfg = {
            "command_gate": {},
            "points": {"enabled": False},
            "activity": {"enabled": False},
            "invite_links": {"enabled": False},
            "usdt_price": {
                "enabled": True,
                "show_query_message": True,
                "show_calc_message": True,
                "alias_z": "z",
                "alias_w": "w",
                "alias_k": "k",
            },
            "crypto": {"price_query_enabled": False},
        }

        with patch.object(extra_features, "get_group_config", return_value=cfg), patch.object(
            extra_features,
            "fetch_usdt_price_summary",
            new=AsyncMock(return_value="USDT CNY\nReference: 7.2000 CNY/USDT"),
        ):
            handled = await extra_features.handle_group_commands(context, message, user, chat, False)

        self.assertTrue(handled)
        message.reply_text.assert_awaited_once_with("USDT CNY\nReference: 7.2000 CNY/USDT")

    async def test_handle_group_commands_supports_cny_to_usdt_calc(self):
        extra_features = importlib.reload(extra_features_module)
        message = SimpleNamespace(text="w1000", caption=None, reply_text=AsyncMock())
        context = SimpleNamespace()
        user = SimpleNamespace(id=7)
        chat = SimpleNamespace(id=-100123)
        cfg = {
            "command_gate": {},
            "points": {"enabled": False},
            "activity": {"enabled": False},
            "invite_links": {"enabled": False},
            "usdt_price": {
                "enabled": True,
                "show_query_message": True,
                "show_calc_message": True,
                "alias_z": "z",
                "alias_w": "w",
                "alias_k": "k",
            },
            "crypto": {"price_query_enabled": False},
        }
        snapshot = {"reference_price": "7.2", "rows": [{"exchange": "binance", "best_price": "7.2"}]}

        with patch.object(extra_features, "get_group_config", return_value=cfg), patch.object(
            extra_features,
            "fetch_usdt_price_snapshot",
            new=AsyncMock(return_value=snapshot),
        ):
            handled = await extra_features.handle_group_commands(context, message, user, chat, False)

        self.assertTrue(handled)
        text = message.reply_text.await_args.args[0]
        self.assertIn("1000", text)
        self.assertIn("USDT", text)
        self.assertIn("138.8889", text)

    async def test_handle_group_commands_supports_usdt_to_cny_calc(self):
        extra_features = importlib.reload(extra_features_module)
        message = SimpleNamespace(text="k 100", caption=None, reply_text=AsyncMock())
        context = SimpleNamespace()
        user = SimpleNamespace(id=7)
        chat = SimpleNamespace(id=-100123)
        cfg = {
            "command_gate": {},
            "points": {"enabled": False},
            "activity": {"enabled": False},
            "invite_links": {"enabled": False},
            "usdt_price": {
                "enabled": True,
                "show_query_message": True,
                "show_calc_message": True,
                "alias_z": "z",
                "alias_w": "w",
                "alias_k": "k",
            },
            "crypto": {"price_query_enabled": False},
        }
        snapshot = {"reference_price": "7.2", "rows": [{"exchange": "binance", "best_price": "7.2"}]}

        with patch.object(extra_features, "get_group_config", return_value=cfg), patch.object(
            extra_features,
            "fetch_usdt_price_snapshot",
            new=AsyncMock(return_value=snapshot),
        ):
            handled = await extra_features.handle_group_commands(context, message, user, chat, False)

        self.assertTrue(handled)
        text = message.reply_text.await_args.args[0]
        self.assertIn("100", text)
        self.assertIn("720", text)
        self.assertIn("CNY", text)
class LotteryRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_publish_lottery_persists_active_message(self):
        extra_features = importlib.reload(extra_features_module)
        memory = _MemoryKv()
        bot = SimpleNamespace(
            send_message=AsyncMock(return_value=SimpleNamespace(message_id=88)),
            pin_chat_message=AsyncMock(),
        )
        context = SimpleNamespace(bot=bot)

        with patch.object(extra_features, "kv_get_json", side_effect=memory.get_json), patch.object(
            extra_features,
            "kv_set_json",
            side_effect=memory.set_json,
        ), patch.object(extra_features, "get_group_config", return_value={"lottery": {"pin_post": False}}):
            lottery = await extra_features.publish_lottery(context, -100123, 42, "Lucky draw | 2")

        self.assertEqual(lottery["message_id"], 88)
        self.assertEqual(memory.data[extra_features._lottery_active_key(-100123)], lottery["id"])
        stored = memory.data[extra_features._lottery_key(-100123, lottery["id"])]
        self.assertEqual(stored["title"], "Lucky draw")
        self.assertEqual(stored["winner_count"], 2)
        bot.send_message.assert_awaited_once()

    async def test_lottery_join_callback_adds_participant(self):
        extra_features = importlib.reload(extra_features_module)
        memory = _MemoryKv()
        lottery = {
            "id": "lot-1",
            "title": "Lucky draw",
            "winner_count": 1,
            "creator_id": 42,
            "participants": [],
            "winners": [],
            "message_id": 88,
            "created_at": 1,
            "closed": False,
            "drawn_at": 0,
        }
        memory.data[extra_features._lottery_key(-100123, "lot-1")] = lottery
        query = SimpleNamespace(
            from_user=SimpleNamespace(id=7),
            edit_message_text=AsyncMock(),
            answer=AsyncMock(),
        )
        update = SimpleNamespace(callback_query=query)

        with patch.object(extra_features, "kv_get_json", side_effect=memory.get_json), patch.object(
            extra_features,
            "kv_set_json",
            side_effect=memory.set_json,
        ), patch.object(extra_features, "safe_answer", new=AsyncMock()) as safe_answer:
            await extra_features.handle_lottery_join_callback(update, SimpleNamespace(), -100123, "lot-1")

        stored = memory.data[extra_features._lottery_key(-100123, "lot-1")]
        self.assertEqual(stored["participants"], [7])
        query.edit_message_text.assert_awaited_once()
        safe_answer.assert_awaited_once_with(query, "Joined", show_alert=False)

    async def test_lottery_draw_callback_closes_lottery_and_clears_active(self):
        extra_features = importlib.reload(extra_features_module)
        memory = _MemoryKv()
        lottery = {
            "id": "lot-2",
            "title": "Lucky draw",
            "winner_count": 1,
            "creator_id": 42,
            "participants": [7, 8],
            "winners": [],
            "message_id": 99,
            "created_at": 1,
            "closed": False,
            "drawn_at": 0,
        }
        memory.data[extra_features._lottery_key(-100123, "lot-2")] = lottery
        memory.data[extra_features._lottery_active_key(-100123)] = "lot-2"
        query = SimpleNamespace(
            from_user=SimpleNamespace(id=42),
            edit_message_text=AsyncMock(),
            answer=AsyncMock(),
        )
        update = SimpleNamespace(callback_query=query)
        context = SimpleNamespace(bot=SimpleNamespace(pin_chat_message=AsyncMock()))

        with patch.object(extra_features, "kv_get_json", side_effect=memory.get_json), patch.object(
            extra_features,
            "kv_set_json",
            side_effect=memory.set_json,
        ), patch.object(extra_features.random, "sample", return_value=[8]), patch.object(
            extra_features,
            "_resolve_user_label",
            new=AsyncMock(return_value="Bob"),
        ), patch.object(extra_features, "safe_answer", new=AsyncMock()) as safe_answer, patch.object(
            extra_features,
            "get_group_config",
            return_value={"lottery": {"pin_result": True}},
        ):
            await extra_features.handle_lottery_draw_callback(update, context, -100123, "lot-2")

        stored = memory.data[extra_features._lottery_key(-100123, "lot-2")]
        self.assertTrue(stored["closed"])
        self.assertEqual(stored["winners"], [8])
        self.assertEqual(memory.data[extra_features._lottery_active_key(-100123)], "")
        query.edit_message_text.assert_awaited_once()
        self.assertIn("Bob", query.edit_message_text.await_args.args[0])
        context.bot.pin_chat_message.assert_awaited_once_with(chat_id=-100123, message_id=99, disable_notification=True)
        safe_answer.assert_awaited_once_with(query, "Lottery drawn", show_alert=False)

class GomokuRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_group_commands_starts_gomoku_game(self):
        extra_features = importlib.reload(extra_features_module)
        memory = _MemoryKv()
        bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=55)))
        context = SimpleNamespace(bot=bot)
        message = SimpleNamespace(text="/gomoku", caption=None, message_id=12)
        user = SimpleNamespace(id=7)
        chat = SimpleNamespace(id=-100123)
        cfg = {
            "command_gate": {},
            "points": {"enabled": False},
            "activity": {"enabled": False},
            "invite_links": {"enabled": False},
            "entertainment": {"gomoku_enabled": True},
            "lottery": {"enabled": False},
            "usdt_price": {"enabled": False},
            "crypto": {"price_query_enabled": False},
        }

        with patch.object(extra_features, "kv_get_json", side_effect=memory.get_json), patch.object(
            extra_features,
            "kv_set_json",
            side_effect=memory.set_json,
        ), patch.object(extra_features, "get_group_config", return_value=cfg):
            handled = await extra_features.handle_group_commands(context, message, user, chat, False)

        self.assertTrue(handled)
        bot.send_message.assert_awaited_once()
        active_id = memory.data[extra_features._gomoku_active_key(-100123)]
        self.assertTrue(active_id)
        stored = memory.data[extra_features._gomoku_key(-100123, active_id)]
        self.assertEqual(stored["creator_id"], 7)
        self.assertEqual(stored["status"], "waiting")

    async def test_gomoku_join_callback_starts_game(self):
        extra_features = importlib.reload(extra_features_module)
        memory = _MemoryKv()
        game = {
            "id": "game-1",
            "creator_id": 7,
            "challenger_id": 0,
            "size": 8,
            "board": [[0] * 8 for _ in range(8)],
            "turn": 1,
            "status": "waiting",
            "winner_id": 0,
            "message_id": 55,
            "created_at": 1,
            "started_at": 0,
            "finished_at": 0,
            "last_move": [],
        }
        memory.data[extra_features._gomoku_key(-100123, "game-1")] = game
        memory.data[extra_features._gomoku_active_key(-100123)] = "game-1"
        query = SimpleNamespace(from_user=SimpleNamespace(id=8), edit_message_text=AsyncMock(), answer=AsyncMock())
        update = SimpleNamespace(callback_query=query)
        context = SimpleNamespace(bot=SimpleNamespace())

        with patch.object(extra_features, "kv_get_json", side_effect=memory.get_json), patch.object(
            extra_features,
            "kv_set_json",
            side_effect=memory.set_json,
        ), patch.object(extra_features, "_resolve_user_label", new=AsyncMock(return_value="Player")), patch.object(
            extra_features,
            "safe_answer",
            new=AsyncMock(),
        ) as safe_answer:
            await extra_features.handle_gomoku_join_callback(update, context, -100123, "game-1")

        stored = memory.data[extra_features._gomoku_key(-100123, "game-1")]
        self.assertEqual(stored["challenger_id"], 8)
        self.assertEqual(stored["status"], "playing")
        query.edit_message_text.assert_awaited_once()
        safe_answer.assert_awaited_once_with(query, "Game started", show_alert=False)

    async def test_gomoku_move_callback_finishes_win_and_clears_active(self):
        extra_features = importlib.reload(extra_features_module)
        memory = _MemoryKv()
        board = [[0] * 8 for _ in range(8)]
        for x in range(4):
            board[0][x] = 1
        game = {
            "id": "game-2",
            "creator_id": 7,
            "challenger_id": 8,
            "size": 8,
            "board": board,
            "turn": 1,
            "status": "playing",
            "winner_id": 0,
            "message_id": 56,
            "created_at": 1,
            "started_at": 2,
            "finished_at": 0,
            "last_move": [],
        }
        memory.data[extra_features._gomoku_key(-100123, "game-2")] = game
        memory.data[extra_features._gomoku_active_key(-100123)] = "game-2"
        query = SimpleNamespace(from_user=SimpleNamespace(id=7), edit_message_text=AsyncMock(), answer=AsyncMock())
        update = SimpleNamespace(callback_query=query)
        context = SimpleNamespace(bot=SimpleNamespace())

        with patch.object(extra_features, "kv_get_json", side_effect=memory.get_json), patch.object(
            extra_features,
            "kv_set_json",
            side_effect=memory.set_json,
        ), patch.object(extra_features, "_resolve_user_label", new=AsyncMock(return_value="Alice")), patch.object(
            extra_features,
            "safe_answer",
            new=AsyncMock(),
        ) as safe_answer:
            await extra_features.handle_gomoku_move_callback(update, context, -100123, "game-2", 4, 0)

        stored = memory.data[extra_features._gomoku_key(-100123, "game-2")]
        self.assertEqual(stored["winner_id"], 7)
        self.assertEqual(stored["status"], "finished")
        self.assertEqual(memory.data[extra_features._gomoku_active_key(-100123)], "")
        query.edit_message_text.assert_awaited_once()
        self.assertIn("Winner", query.edit_message_text.await_args.args[0])
        safe_answer.assert_awaited_once_with(query, "You win", show_alert=False)


class DiceAndFunRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_group_commands_supports_dice_and_deducts_points(self):
        extra_features = importlib.reload(extra_features_module)
        memory = _MemoryKv()
        memory.data[extra_features._points_key(-100123, 7)] = {
            "balance": 10,
            "last_sign_day": "",
            "sign_count": 0,
        }
        bot = SimpleNamespace(send_dice=AsyncMock(return_value=SimpleNamespace(message_id=66)))
        context = SimpleNamespace(bot=bot)
        message = SimpleNamespace(text="/dice", caption=None, message_id=19)
        user = SimpleNamespace(id=7, mention_html=lambda: "Alice")
        chat = SimpleNamespace(id=-100123)
        cfg = {
            "command_gate": {},
            "points": {"enabled": True},
            "activity": {"enabled": False},
            "invite_links": {"enabled": False},
            "entertainment": {"dice_enabled": True, "dice_cost": 3, "dice_command": "/dice", "gomoku_enabled": False},
            "lottery": {"enabled": False},
            "usdt_price": {"enabled": False},
            "crypto": {"price_query_enabled": False},
        }

        with patch.object(extra_features, "kv_get_json", side_effect=memory.get_json), patch.object(
            extra_features,
            "kv_set_json",
            side_effect=memory.set_json,
        ), patch.object(extra_features, "get_group_config", return_value=cfg):
            handled = await extra_features.handle_group_commands(context, message, user, chat, False)

        self.assertTrue(handled)
        bot.send_dice.assert_awaited_once_with(chat_id=-100123, emoji="??", reply_to_message_id=19)
        self.assertEqual(memory.data[extra_features._points_key(-100123, 7)]["balance"], 7)

    async def test_show_fun_menu_exposes_gomoku_toggle(self):
        update = SimpleNamespace(callback_query=None, effective_message=None)
        state = {"active_group_id": -100123}
        cfg = {"entertainment": {"dice_enabled": True, "dice_cost": 3, "dice_command": "/dice", "gomoku_enabled": True, "gomoku_command": "/gomoku"}}

        with patch.object(admin_extra, "get_group_config", return_value=cfg), patch.object(
            admin_extra,
            "_send_or_edit",
            new=AsyncMock(),
        ) as send_ui:
            await admin_extra.show_fun_menu(update, None, state)

        args = send_ui.await_args.args
        self.assertIn("Gomoku", args[1])
        keyboard = args[2].inline_keyboard
        self.assertEqual(keyboard[0][0].callback_data, "adminx:fun:toggle:dice_enabled")
        self.assertEqual(keyboard[2][0].callback_data, "adminx:fun:toggle:gomoku_enabled")


class WebApiSecurityRegressionTests(unittest.TestCase):
    def test_local_debug_login_requires_explicit_opt_in_and_secret(self):
        with patch.object(web_api_module, "SUPER_ADMIN_ID", 42), patch.dict(
            web_api_module.os.environ,
            {"WEB_LOCAL_DEBUG_LOGIN_ENABLED": "1", "WEB_LOCAL_DEBUG_LOGIN_SECRET": ""},
            clear=False,
        ):
            settings = web_api_module.local_debug_login_settings()
        self.assertFalse(settings["enabled"])

        with patch.object(web_api_module, "SUPER_ADMIN_ID", 42), patch.dict(
            web_api_module.os.environ,
            {"WEB_LOCAL_DEBUG_LOGIN_ENABLED": "1", "WEB_LOCAL_DEBUG_LOGIN_SECRET": "secret-value"},
            clear=False,
        ):
            settings = web_api_module.local_debug_login_settings()
        self.assertTrue(settings["enabled"])
        self.assertTrue(settings["requires_secret"])
        self.assertTrue(settings["loopback_only"])

    def test_local_debug_secret_requires_exact_match(self):
        with patch.dict(
            web_api_module.os.environ,
            {"WEB_LOCAL_DEBUG_LOGIN_SECRET": "secret-value"},
            clear=False,
        ):
            self.assertFalse(web_api_module.is_authorized_local_debug_secret(None))
            self.assertFalse(web_api_module.is_authorized_local_debug_secret(""))
            self.assertFalse(web_api_module.is_authorized_local_debug_secret("wrong"))
            self.assertTrue(web_api_module.is_authorized_local_debug_secret("secret-value"))


class WebServiceValidationRegressionTests(unittest.TestCase):
    def test_save_module_payload_rejects_non_object_json_module(self):
        web_service = importlib.reload(web_service_module)

        with patch.object(web_service, "get_group_config", return_value={"entertainment": {}}):
            with self.assertRaisesRegex(ValueError, "fun.data must be a JSON object"):
                web_service.save_module_payload(-100123, "fun", {"data": []})

    def test_save_module_payload_normalizes_fun_types(self):
        web_service = importlib.reload(web_service_module)
        saved = {}

        def _save(group_id, cfg):
            saved["group_id"] = group_id
            saved["cfg"] = cfg

        payload = {
            "data": {
                "dice_enabled": "1",
                "dice_cost": "30",
                "dice_command": 77,
                "gomoku_enabled": "",
                "gomoku_command": None,
            }
        }

        with patch.object(web_service, "get_group_config", return_value={"entertainment": {}}), patch.object(
            web_service,
            "save_group_config",
            side_effect=_save,
        ), patch.object(web_service, "load_module_payload", return_value={"ok": True}):
            web_service.save_module_payload(-100123, "fun", payload)

        self.assertEqual(saved["group_id"], -100123)
        fun_cfg = saved["cfg"]["entertainment"]
        self.assertTrue(fun_cfg["dice_enabled"])
        self.assertEqual(fun_cfg["dice_cost"], 30)
        self.assertEqual(fun_cfg["dice_command"], "77")
        self.assertFalse(fun_cfg["gomoku_enabled"])
        self.assertEqual(fun_cfg["gomoku_command"], "/gomoku")

    def test_save_module_payload_rejects_schedule_items_when_not_array(self):
        web_service = importlib.reload(web_service_module)

        with patch.object(web_service, "get_group_config", return_value={"schedule": {}}):
            with self.assertRaisesRegex(ValueError, "schedule.data.items must be a JSON array"):
                web_service.save_module_payload(-100123, "schedule", {"data": {"config": {}, "items": {}}})

    def test_render_preview_replaces_server_side_placeholders_and_filters_buttons(self):
        web_service = importlib.reload(web_service_module)

        preview = web_service.render_preview(
            {
                "text": "{user} | {question} | {date}",
                "photo_file_id": "PHOTO-1",
                "buttons": ["bad", {"text": "Open", "type": "url", "value": "https://example.com", "row": 0}],
            },
            {"user": "Alice", "question": "1 + 1 = ?"},
        )

        self.assertIn("Alice", preview["text_html"])
        self.assertIn("1 + 1 = ?", preview["text_html"])
        self.assertNotIn("{date}", preview["text_html"])
        self.assertEqual(preview["photo_file_id"], "PHOTO-1")
        self.assertEqual(len(preview["rows"]), 1)
        self.assertEqual(preview["rows"][0][0]["text"], "Open")


class WebServiceDedicatedEditorRegressionTests(unittest.TestCase):
    def test_load_module_payload_returns_dedicated_editors_for_web_forms(self):
        web_service = importlib.reload(web_service_module)
        cfg = {
            "crypto": {
                "wallet_query_enabled": False,
                "price_query_enabled": True,
                "push_enabled": True,
                "default_symbol": "ETH",
                "query_alias": "price",
            },
            "entertainment": {
                "dice_enabled": False,
                "dice_cost": 12,
                "dice_command": "/roll",
                "gomoku_enabled": True,
                "gomoku_command": "/five",
            },
            "lottery": {
                "enabled": True,
                "query_command": "/lottery",
                "auto_delete_sec": 90,
                "pin_post": False,
                "pin_result": True,
            },
            "invite_links": {
                "enabled": True,
                "notify_enabled": True,
                "join_review": True,
                "reward_points": 8,
                "query_command": "/link",
                "today_rank_command": "today link",
                "month_rank_command": "month link",
                "total_rank_command": "all link",
                "result_format": "text",
                "only_admin_can_query_rank": True,
                "auto_delete_sec": 45,
                "notify_text": "Welcome {userName}",
                "notify_photo_file_id": "PHOTO-7",
                "notify_buttons": [{"text": "Open", "type": "url", "value": "https://example.com", "row": 0}],
            },
        }

        with patch.object(web_service, "get_group_config", return_value=cfg):
            crypto_payload = web_service.load_module_payload(-100123, "crypto")
            fun_payload = web_service.load_module_payload(-100123, "fun")
            lottery_payload = web_service.load_module_payload(-100123, "lottery")
            invite_payload = web_service.load_module_payload(-100123, "invite")

        self.assertEqual(crypto_payload["editor"], "crypto")
        self.assertEqual(crypto_payload["data"]["default_symbol"], "ETH")
        self.assertEqual(fun_payload["editor"], "fun")
        self.assertEqual(fun_payload["data"]["dice_command"], "/roll")
        self.assertEqual(lottery_payload["editor"], "lottery")
        self.assertEqual(lottery_payload["data"]["auto_delete_sec"], 90)
        self.assertEqual(invite_payload["editor"], "invite")
        self.assertEqual(invite_payload["data"]["notify_message"]["photo_file_id"], "PHOTO-7")
        self.assertEqual(invite_payload["data"]["notify_message"]["buttons"][0]["text"], "Open")

    def test_save_module_payload_updates_invite_editor_fields(self):
        web_service = importlib.reload(web_service_module)
        saved = {}

        def _save(group_id, cfg):
            saved["group_id"] = group_id
            saved["cfg"] = cfg

        payload = {
            "data": {
                "enabled": True,
                "notify_enabled": True,
                "join_review": False,
                "reward_points": "15",
                "query_command": "/invite",
                "today_rank_command": "today",
                "month_rank_command": "month",
                "total_rank_command": "total",
                "result_format": "text",
                "only_admin_can_query_rank": True,
                "auto_delete_sec": "30",
                "notify_message": {
                    "text": "Hello {userName}",
                    "photo_file_id": "PHOTO-2",
                    "buttons": [
                        {"text": "Docs", "type": "url", "value": "https://example.com", "row": 0},
                        "bad",
                    ],
                },
            }
        }

        with patch.object(web_service, "get_group_config", return_value={"invite_links": {}}), patch.object(
            web_service,
            "save_group_config",
            side_effect=_save,
        ), patch.object(web_service, "load_module_payload", return_value={"ok": True}):
            web_service.save_module_payload(-100123, "invite", payload)

        invite_cfg = saved["cfg"]["invite_links"]
        self.assertEqual(saved["group_id"], -100123)
        self.assertTrue(invite_cfg["enabled"])
        self.assertEqual(invite_cfg["reward_points"], 15)
        self.assertEqual(invite_cfg["query_command"], "/invite")
        self.assertEqual(invite_cfg["auto_delete_sec"], 30)
        self.assertEqual(invite_cfg["notify_text"], "Hello {userName}")
        self.assertEqual(invite_cfg["notify_photo_file_id"], "PHOTO-2")
        self.assertEqual(invite_cfg["notify_buttons"], [{"text": "Docs", "type": "url", "value": "https://example.com", "row": 0}])


class WebServiceOpsEditorRegressionTests(unittest.TestCase):
    def test_load_module_payload_returns_dedicated_editors_for_points_activity_usdt_verified(self):
        web_service = importlib.reload(web_service_module)
        cfg = {
            "points": {
                "enabled": True,
                "chat_points_enabled": True,
                "sign_command": "/sign",
                "query_command": "/points",
                "rank_command": "/points-top",
                "sign_points": 8,
                "chat_points_per_message": 2,
                "min_text_length": 9,
                "admin_adjust_enabled": True,
            },
            "activity": {
                "enabled": False,
                "today_command": "/today-active",
                "month_command": "/month-active",
                "total_command": "/all-active",
            },
            "usdt_price": {
                "enabled": True,
                "tier": "best",
                "show_query_message": False,
                "show_calc_message": True,
                "alias_z": "priceu",
                "alias_w": "cnyu",
                "alias_k": "u2cny",
                "exchanges": ["okx", "htx"],
            },
            "verified_user": {
                "enabled": True,
            },
        }

        with patch.object(web_service, "get_group_config", return_value=cfg):
            points_payload = web_service.load_module_payload(-100123, "points")
            activity_payload = web_service.load_module_payload(-100123, "activity")
            usdt_payload = web_service.load_module_payload(-100123, "usdt")
            verified_payload = web_service.load_module_payload(-100123, "verified")

        self.assertEqual(points_payload["editor"], "points")
        self.assertEqual(points_payload["data"]["sign_points"], 8)
        self.assertEqual(activity_payload["editor"], "activity")
        self.assertEqual(activity_payload["data"]["today_command"], "/today-active")
        self.assertEqual(usdt_payload["editor"], "usdt")
        self.assertEqual(usdt_payload["data"]["exchanges"], ["okx", "htx"])
        self.assertEqual(verified_payload["editor"], "verified")
        self.assertTrue(verified_payload["data"]["enabled"])

    def test_save_module_payload_updates_points_and_usdt_editor_fields(self):
        web_service = importlib.reload(web_service_module)
        saved = {}

        def _save(group_id, cfg):
            saved.setdefault("writes", []).append((group_id, cfg))

        with patch.object(web_service, "get_group_config", return_value={"points": {}, "usdt_price": {}}), patch.object(
            web_service,
            "save_group_config",
            side_effect=_save,
        ), patch.object(web_service, "load_module_payload", return_value={"ok": True}):
            web_service.save_module_payload(
                -100123,
                "points",
                {
                    "data": {
                        "enabled": True,
                        "chat_points_enabled": True,
                        "sign_command": "/sign",
                        "query_command": "/points",
                        "rank_command": "/rank",
                        "sign_points": "9",
                        "chat_points_per_message": "3",
                        "min_text_length": "12",
                        "admin_adjust_enabled": False,
                    }
                },
            )
            web_service.save_module_payload(
                -100123,
                "usdt",
                {
                    "data": {
                        "enabled": True,
                        "tier": "best",
                        "show_query_message": True,
                        "show_calc_message": False,
                        "alias_z": "price",
                        "alias_w": "cny",
                        "alias_k": "usdt",
                        "exchanges": ["okx", "bad", "htx"],
                    }
                },
            )

        points_cfg = saved["writes"][0][1]["points"]
        usdt_cfg = saved["writes"][1][1]["usdt_price"]
        self.assertTrue(points_cfg["enabled"])
        self.assertEqual(points_cfg["sign_points"], 9)
        self.assertEqual(points_cfg["chat_points_per_message"], 3)
        self.assertEqual(points_cfg["min_text_length"], 12)
        self.assertEqual(points_cfg["rank_command"], "/rank")
        self.assertTrue(usdt_cfg["enabled"])
        self.assertFalse(usdt_cfg["show_calc_message"])
        self.assertEqual(usdt_cfg["alias_z"], "price")
        self.assertEqual(usdt_cfg["exchanges"], ["okx", "htx"])


class WebServiceWorkflowEditorRegressionTests(unittest.TestCase):
    def test_schema_exposes_dedicated_editors_for_supported_web_forms(self):
        web_schemas = importlib.reload(web_schemas_module)

        self.assertEqual(web_schemas.get_module("autodelete")["editor"], "autodelete")
        self.assertEqual(web_schemas.get_module("schedule")["editor"], "schedule")
        self.assertEqual(web_schemas.get_module("crypto")["editor"], "crypto")
        self.assertEqual(web_schemas.get_module("fun")["editor"], "fun")
        self.assertEqual(web_schemas.get_module("invite")["editor"], "invite")
        self.assertEqual(web_schemas.get_module("lottery")["editor"], "lottery")

    def test_load_module_payload_returns_dedicated_editors_for_schedule_and_autodelete(self):
        web_service = importlib.reload(web_service_module)
        cfg = {"schedule": {"enabled": False}}
        delete_cfg = {
            "delete_links": False,
            "delete_long": True,
            "long_length": 240,
            "custom_rules": [{"keyword": "spam", "mode": "contains"}],
            "ad_sticker_ids": ["stk-1"],
        }
        schedule_items = [
            {
                "id": 7,
                "text": "Morning update",
                "photo_file_id": "PHOTO-9",
                "buttons": [{"text": "Open", "type": "url", "value": "https://example.com", "row": 0}],
                "interval_sec": 1800,
                "next_at": 123456,
                "enabled": False,
            }
        ]

        with patch.object(web_service, "get_group_config", return_value=cfg), patch.object(
            web_service,
            "get_group_auto_delete",
            return_value=delete_cfg,
        ), patch.object(web_service, "load_schedule_items", return_value=schedule_items):
            autodelete_payload = web_service.load_module_payload(-100123, "autodelete")
            schedule_payload = web_service.load_module_payload(-100123, "schedule")

        self.assertEqual(autodelete_payload["editor"], "autodelete")
        self.assertFalse(autodelete_payload["data"]["delete_links"])
        self.assertEqual(autodelete_payload["data"]["long_length"], 240)
        self.assertEqual(autodelete_payload["data"]["custom_rules"][0]["keyword"], "spam")
        self.assertEqual(schedule_payload["editor"], "schedule")
        self.assertFalse(schedule_payload["data"]["enabled"])
        self.assertEqual(schedule_payload["data"]["items"][0]["id"], 7)
        self.assertEqual(schedule_payload["data"]["items"][0]["photo_file_id"], "PHOTO-9")
        self.assertEqual(schedule_payload["data"]["items"][0]["buttons"][0]["text"], "Open")

    def test_save_module_payload_updates_schedule_and_autodelete_fields(self):
        web_service = importlib.reload(web_service_module)
        saved = {}

        def _save_cfg(group_id, cfg):
            saved["cfg_group_id"] = group_id
            saved["cfg"] = cfg

        def _save_delete(group_id, cfg):
            saved["delete_group_id"] = group_id
            saved["delete_cfg"] = cfg

        def _save_items(group_id, items):
            saved["schedule_group_id"] = group_id
            saved["schedule_items"] = items

        with patch.object(web_service, "get_group_config", return_value={"schedule": {}}), patch.object(
            web_service,
            "get_group_auto_delete",
            return_value={},
        ), patch.object(web_service, "save_group_config", side_effect=_save_cfg), patch.object(
            web_service,
            "save_group_auto_delete",
            side_effect=_save_delete,
        ), patch.object(web_service, "save_schedule_items", side_effect=_save_items), patch.object(
            web_service,
            "load_module_payload",
            return_value={"ok": True},
        ):
            web_service.save_module_payload(
                -100123,
                "autodelete",
                {
                    "data": {
                        "delete_system": False,
                        "delete_links": True,
                        "delete_long": True,
                        "long_length": "210",
                        "exclude_admins": False,
                        "custom_rules": [
                            {"keyword": " spam ", "mode": "contains"},
                            {"keyword": "", "mode": "exact"},
                            "bad",
                        ],
                        "ad_sticker_ids": [" sticker-a ", "", 99],
                    }
                },
            )
            web_service.save_module_payload(
                -100123,
                "schedule",
                {
                    "data": {
                        "enabled": False,
                        "items": [
                            {
                                "id": "7",
                                "text": " Ping ",
                                "photo_file_id": "",
                                "buttons": [
                                    {"text": "Open", "type": "url", "value": "https://example.com", "row": 0},
                                    "bad",
                                ],
                                "interval_sec": "30",
                                "next_at": "111",
                                "enabled": False,
                            },
                            {
                                "id": 0,
                                "text": "",
                                "photo_file_id": "",
                                "buttons": [],
                                "interval_sec": 60,
                                "next_at": 0,
                                "enabled": True,
                            },
                        ],
                    }
                },
            )

        delete_cfg = saved["delete_cfg"]
        schedule_cfg = saved["cfg"]["schedule"]
        schedule_items = saved["schedule_items"]
        self.assertEqual(saved["delete_group_id"], -100123)
        self.assertFalse(delete_cfg["delete_system"])
        self.assertTrue(delete_cfg["delete_links"])
        self.assertEqual(delete_cfg["long_length"], 210)
        self.assertFalse(delete_cfg["exclude_admins"])
        self.assertEqual(delete_cfg["custom_rules"], [{"keyword": "spam", "mode": "contains"}])
        self.assertEqual(delete_cfg["ad_sticker_ids"], ["sticker-a", "99"])
        self.assertEqual(saved["cfg_group_id"], -100123)
        self.assertFalse(schedule_cfg["enabled"])
        self.assertEqual(saved["schedule_group_id"], -100123)
        self.assertEqual(len(schedule_items), 1)
        self.assertEqual(schedule_items[0]["id"], 7)
        self.assertEqual(schedule_items[0]["interval_sec"], 60)
        self.assertEqual(schedule_items[0]["next_at"], 111)
        self.assertFalse(schedule_items[0]["enabled"])
        self.assertEqual(schedule_items[0]["buttons"], [{"text": "Open", "type": "url", "value": "https://example.com", "row": 0}])


class WebServiceModerationEditorRegressionTests(unittest.TestCase):
    def test_schema_exposes_dedicated_editors_for_moderation_forms(self):
        web_schemas = importlib.reload(web_schemas_module)

        self.assertEqual(web_schemas.get_module("ad")["editor"], "ad")
        self.assertEqual(web_schemas.get_module("cmd")["editor"], "cmd")
        self.assertEqual(web_schemas.get_module("member")["editor"], "member")
        self.assertEqual(web_schemas.get_module("antispam")["editor"], "antispam")

    def test_load_module_payload_returns_dedicated_editors_for_moderation_forms(self):
        web_service = importlib.reload(web_service_module)
        cfg = {
            "ad_filter": {
                "nickname_enabled": True,
                "sticker_enabled": False,
                "message_enabled": True,
                "block_channel_mask": True,
            },
            "command_gate": {
                "sign": True,
                "profile": False,
                "warn": True,
                "help": False,
                "config": True,
                "ban": False,
                "kick": True,
                "mute": False,
            },
            "member_watch": {
                "nickname_change_detect": True,
                "nickname_change_notice": False,
            },
        }
        spam_cfg = {
            "enabled": True,
            "action": "ban",
            "mute_seconds": 600,
            "window_sec": 12,
            "threshold": 4,
            "types": ["text", "link", "bad"],
        }

        with patch.object(web_service, "get_group_config", return_value=cfg), patch.object(
            web_service,
            "get_group_anti_spam",
            return_value=spam_cfg,
        ):
            ad_payload = web_service.load_module_payload(-100123, "ad")
            cmd_payload = web_service.load_module_payload(-100123, "cmd")
            member_payload = web_service.load_module_payload(-100123, "member")
            spam_payload = web_service.load_module_payload(-100123, "antispam")

        self.assertEqual(ad_payload["editor"], "ad")
        self.assertTrue(ad_payload["data"]["nickname_enabled"])
        self.assertEqual(cmd_payload["editor"], "cmd")
        self.assertTrue(cmd_payload["data"]["config"])
        self.assertEqual(member_payload["editor"], "member")
        self.assertTrue(member_payload["data"]["nickname_change_detect"])
        self.assertEqual(spam_payload["editor"], "antispam")
        self.assertEqual(spam_payload["data"]["action"], "ban")
        self.assertEqual(spam_payload["data"]["types"], ["text", "link"])

    def test_save_module_payload_updates_moderation_editor_fields(self):
        web_service = importlib.reload(web_service_module)
        saved = {"cfg_writes": []}

        def _save_cfg(group_id, cfg):
            saved["cfg_writes"].append((group_id, deepcopy(cfg)))

        def _save_spam(group_id, cfg):
            saved["spam_group_id"] = group_id
            saved["spam_cfg"] = deepcopy(cfg)

        with patch.object(
            web_service,
            "get_group_config",
            return_value={"ad_filter": {}, "command_gate": {}, "member_watch": {}},
        ), patch.object(web_service, "get_group_anti_spam", return_value={}), patch.object(
            web_service,
            "save_group_config",
            side_effect=_save_cfg,
        ), patch.object(
            web_service,
            "save_group_anti_spam",
            side_effect=_save_spam,
        ), patch.object(
            web_service,
            "load_module_payload",
            return_value={"ok": True},
        ):
            web_service.save_module_payload(
                -100123,
                "ad",
                {
                    "data": {
                        "nickname_enabled": True,
                        "sticker_enabled": False,
                        "message_enabled": True,
                        "block_channel_mask": True,
                    }
                },
            )
            web_service.save_module_payload(
                -100123,
                "cmd",
                {
                    "data": {
                        "sign": True,
                        "profile": False,
                        "warn": True,
                        "help": False,
                        "config": True,
                        "ban": False,
                        "kick": True,
                        "mute": False,
                    }
                },
            )
            web_service.save_module_payload(
                -100123,
                "member",
                {
                    "data": {
                        "nickname_change_detect": True,
                        "nickname_change_notice": True,
                    }
                },
            )
            web_service.save_module_payload(
                -100123,
                "antispam",
                {
                    "data": {
                        "enabled": True,
                        "action": "ban",
                        "mute_seconds": "15",
                        "window_sec": "0",
                        "threshold": "0",
                        "types": ["text", "video", "bad"],
                    }
                },
            )

        ad_cfg = saved["cfg_writes"][0][1]["ad_filter"]
        cmd_cfg = saved["cfg_writes"][1][1]["command_gate"]
        member_cfg = saved["cfg_writes"][2][1]["member_watch"]
        spam_cfg = saved["spam_cfg"]
        self.assertEqual(saved["cfg_writes"][0][0], -100123)
        self.assertTrue(ad_cfg["message_enabled"])
        self.assertTrue(ad_cfg["block_channel_mask"])
        self.assertTrue(cmd_cfg["sign"])
        self.assertTrue(cmd_cfg["config"])
        self.assertTrue(cmd_cfg["kick"])
        self.assertTrue(member_cfg["nickname_change_detect"])
        self.assertTrue(member_cfg["nickname_change_notice"])
        self.assertEqual(saved["spam_group_id"], -100123)
        self.assertTrue(spam_cfg["enabled"])
        self.assertEqual(spam_cfg["action"], "ban")
        self.assertEqual(spam_cfg["mute_seconds"], 15)
        self.assertEqual(spam_cfg["window_sec"], 1)
        self.assertEqual(spam_cfg["threshold"], 1)
        self.assertEqual(spam_cfg["types"], ["text", "video"])

    def test_save_module_payload_rejects_antispam_types_when_not_array(self):
        web_service = importlib.reload(web_service_module)

        with patch.object(web_service, "get_group_config", return_value={}), patch.object(
            web_service,
            "get_group_anti_spam",
            return_value={},
        ):
            with self.assertRaisesRegex(ValueError, "antispam.data.types must be a JSON array"):
                web_service.save_module_payload(-100123, "antispam", {"data": {"types": {}}})


class WebServiceEnforcementEditorRegressionTests(unittest.TestCase):
    def test_schema_exposes_dedicated_editors_for_enforcement_forms(self):
        web_schemas = importlib.reload(web_schemas_module)

        self.assertEqual(web_schemas.get_module("autoban")["editor"], "autoban")
        self.assertEqual(web_schemas.get_module("automute")["editor"], "automute")
        self.assertEqual(web_schemas.get_module("autowarn")["editor"], "autowarn")

    def test_load_module_payload_returns_dedicated_editors_for_enforcement_forms(self):
        web_service = importlib.reload(web_service_module)
        ban_cfg = {"enabled": False, "default_duration_sec": 0, "rules": [{"id": "ab-1", "keyword": "spam", "mode": "regex", "duration_sec": 3600}]}
        mute_cfg = {"default_duration_sec": 120, "rules": [{"id": "am-1", "keyword": "flood", "mode": "contains", "duration_sec": 30}]}
        warn_cfg = {"enabled": True, "warn_limit": 4, "mute_seconds": 600, "action": "kick", "cmd_mute_enabled": True, "warn_text": "Warn {count}/{limit}", "rules": [{"id": "aw-1", "keyword": "bad", "mode": "exact"}]}

        with patch.object(web_service, "get_group_config", return_value={}), patch.object(web_service, "get_group_auto_ban", return_value=ban_cfg), patch.object(web_service, "get_group_auto_mute", return_value=mute_cfg), patch.object(web_service, "get_group_auto_warn", return_value=warn_cfg):
            autoban_payload = web_service.load_module_payload(-100123, "autoban")
            automute_payload = web_service.load_module_payload(-100123, "automute")
            autowarn_payload = web_service.load_module_payload(-100123, "autowarn")

        self.assertEqual(autoban_payload["editor"], "autoban")
        self.assertFalse(autoban_payload["data"]["enabled"])
        self.assertEqual(autoban_payload["data"]["rules"][0]["duration_sec"], 3600)
        self.assertEqual(automute_payload["editor"], "automute")
        self.assertEqual(automute_payload["data"]["default_duration_sec"], 120)
        self.assertEqual(automute_payload["data"]["rules"][0]["keyword"], "flood")
        self.assertEqual(autowarn_payload["editor"], "autowarn")
        self.assertTrue(autowarn_payload["data"]["cmd_mute_enabled"])
        self.assertEqual(autowarn_payload["data"]["action"], "kick")
        self.assertEqual(autowarn_payload["data"]["warn_text"], "Warn {count}/{limit}")

    def test_save_module_payload_updates_enforcement_editor_fields(self):
        web_service = importlib.reload(web_service_module)
        saved = {}

        def _save_ban(group_id, cfg):
            saved["ban_group_id"] = group_id
            saved["ban_cfg"] = deepcopy(cfg)

        def _save_mute(group_id, cfg):
            saved["mute_group_id"] = group_id
            saved["mute_cfg"] = deepcopy(cfg)

        def _save_warn(group_id, cfg):
            saved["warn_group_id"] = group_id
            saved["warn_cfg"] = deepcopy(cfg)

        with patch.object(web_service, "get_group_config", return_value={}), patch.object(web_service, "get_group_auto_ban", return_value={}), patch.object(web_service, "get_group_auto_mute", return_value={}), patch.object(web_service, "get_group_auto_warn", return_value={"warn_photo_file_id": "PHOTO-1", "warn_buttons": [{"text": "Keep"}]}), patch.object(web_service, "save_group_auto_ban", side_effect=_save_ban), patch.object(web_service, "save_group_auto_mute", side_effect=_save_mute), patch.object(web_service, "save_group_auto_warn", side_effect=_save_warn), patch.object(web_service, "load_module_payload", return_value={"ok": True}):
            web_service.save_module_payload(-100123, "autoban", {"data": {"enabled": False, "default_duration_sec": "0", "rules": [{"keyword": " spam ", "mode": "regex", "duration_sec": "3600"}, {"keyword": "", "mode": "contains", "duration_sec": "120"}]}})
            web_service.save_module_payload(-100123, "automute", {"data": {"default_duration_sec": "90", "rules": [{"keyword": " flood ", "mode": "contains", "duration_sec": "15"}, "bad"]}})
            web_service.save_module_payload(-100123, "autowarn", {"data": {"enabled": False, "warn_limit": "0", "mute_seconds": "0", "action": "bad", "cmd_mute_enabled": True, "warn_text": "Stop {count}/{limit}", "rules": [{"keyword": " rude ", "mode": "exact"}, {"keyword": "", "mode": "contains"}]}})

        self.assertEqual(saved["ban_group_id"], -100123)
        self.assertFalse(saved["ban_cfg"]["enabled"])
        self.assertEqual(saved["ban_cfg"]["default_duration_sec"], 0)
        self.assertEqual(len(saved["ban_cfg"]["rules"]), 1)
        self.assertTrue(saved["ban_cfg"]["rules"][0]["id"])
        self.assertEqual(saved["ban_cfg"]["rules"][0]["keyword"], "spam")
        self.assertEqual(saved["ban_cfg"]["rules"][0]["duration_sec"], 3600)
        self.assertEqual(saved["mute_group_id"], -100123)
        self.assertEqual(saved["mute_cfg"]["default_duration_sec"], 90)
        self.assertEqual(saved["mute_cfg"]["rules"][0]["keyword"], "flood")
        self.assertEqual(saved["mute_cfg"]["rules"][0]["duration_sec"], 15)
        self.assertEqual(saved["warn_group_id"], -100123)
        self.assertFalse(saved["warn_cfg"]["enabled"])
        self.assertEqual(saved["warn_cfg"]["warn_limit"], 1)
        self.assertEqual(saved["warn_cfg"]["mute_seconds"], 1)
        self.assertEqual(saved["warn_cfg"]["action"], "mute")
        self.assertTrue(saved["warn_cfg"]["cmd_mute_enabled"])
        self.assertEqual(saved["warn_cfg"]["warn_text"], "Stop {count}/{limit}")
        self.assertEqual(saved["warn_cfg"]["warn_photo_file_id"], "PHOTO-1")
        self.assertEqual(saved["warn_cfg"]["rules"][0]["keyword"], "rude")
        self.assertEqual(saved["warn_cfg"]["rules"][0]["mode"], "exact")

    def test_save_module_payload_rejects_autoban_rules_when_not_array(self):
        web_service = importlib.reload(web_service_module)

        with patch.object(web_service, "get_group_config", return_value={}), patch.object(web_service, "get_group_auto_ban", return_value={}):
            with self.assertRaisesRegex(ValueError, "autoban.data.rules must be a JSON array"):
                web_service.save_module_payload(-100123, "autoban", {"data": {"rules": {}}})


class WebServiceFinalEditorRegressionTests(unittest.TestCase):
    def test_schema_exposes_dedicated_editors_for_final_web_forms(self):
        web_schemas = importlib.reload(web_schemas_module)

        self.assertEqual(web_schemas.get_module("related")["editor"], "related")
        self.assertEqual(web_schemas.get_module("admin_access")["editor"], "admin_access")
        self.assertEqual(web_schemas.get_module("nsfw")["editor"], "nsfw")
        self.assertEqual(web_schemas.get_module("lang")["editor"], "lang")

    def test_load_module_payload_returns_dedicated_editors_for_final_web_forms(self):
        web_service = importlib.reload(web_service_module)
        cfg = {
            "related_channel": {
                "cancel_top_pin": True,
                "occupy_comment": True,
                "occupy_comment_text": "Pinned {group}",
                "occupy_comment_photo_file_id": "PHOTO-3",
                "occupy_comment_buttons": [{"text": "Open", "type": "url", "value": "https://example.com", "row": 0}],
            },
            "admin_access": {"mode": "service_owner"},
            "nsfw": {
                "enabled": True,
                "sensitivity": "high",
                "allow_miss": True,
                "notice_enabled": False,
                "delay_delete_sec": 12,
            },
            "language_whitelist": {"enabled": True, "allowed": ["en-US", "zh-CN", "jp"]},
        }

        with patch.object(web_service, "get_group_config", return_value=cfg):
            related_payload = web_service.load_module_payload(-100123, "related")
            access_payload = web_service.load_module_payload(-100123, "admin_access")
            nsfw_payload = web_service.load_module_payload(-100123, "nsfw")
            lang_payload = web_service.load_module_payload(-100123, "lang")

        self.assertEqual(related_payload["editor"], "related")
        self.assertTrue(related_payload["data"]["cancel_top_pin"])
        self.assertEqual(related_payload["data"]["comment_message"]["photo_file_id"], "PHOTO-3")
        self.assertEqual(access_payload["editor"], "admin_access")
        self.assertEqual(access_payload["data"]["mode"], "service_owner")
        self.assertEqual(nsfw_payload["editor"], "nsfw")
        self.assertEqual(nsfw_payload["data"]["sensitivity"], "high")
        self.assertTrue(nsfw_payload["data"]["allow_miss"])
        self.assertEqual(lang_payload["editor"], "lang")
        self.assertEqual(lang_payload["data"]["allowed"], ["en", "zh", "jp"])

    def test_save_module_payload_updates_final_editor_fields(self):
        web_service = importlib.reload(web_service_module)
        saved = {}

        def _save(group_id, cfg):
            saved.setdefault("writes", []).append((group_id, deepcopy(cfg)))

        with patch.object(web_service, "get_group_config", return_value={"related_channel": {}, "admin_access": {}, "nsfw": {}, "language_whitelist": {}}), patch.object(web_service, "save_group_config", side_effect=_save), patch.object(web_service, "load_module_payload", return_value={"ok": True}):
            web_service.save_module_payload(-100123, "related", {"data": {"cancel_top_pin": True, "occupy_comment": True, "comment_message": {"text": "Pinned {group}", "photo_file_id": "PHOTO-9", "buttons": [{"text": "Open", "type": "url", "value": "https://example.com", "row": 0}, "bad"]}}})
            web_service.save_module_payload(-100123, "admin_access", {"data": {"mode": "service_owner"}})
            web_service.save_module_payload(-100123, "nsfw", {"data": {"enabled": True, "sensitivity": "weird", "allow_miss": True, "notice_enabled": False, "delay_delete_sec": "15"}})
            web_service.save_module_payload(-100123, "lang", {"data": {"enabled": True, "allowed": [" en-US ", "zh-CN", "zh", ""]}})

        related_cfg = saved["writes"][0][1]["related_channel"]
        access_cfg = saved["writes"][1][1]["admin_access"]
        nsfw_cfg = saved["writes"][2][1]["nsfw"]
        lang_cfg = saved["writes"][3][1]["language_whitelist"]
        self.assertEqual(saved["writes"][0][0], -100123)
        self.assertTrue(related_cfg["cancel_top_pin"])
        self.assertTrue(related_cfg["occupy_comment"])
        self.assertEqual(related_cfg["occupy_comment_photo_file_id"], "PHOTO-9")
        self.assertEqual(related_cfg["occupy_comment_buttons"], [{"text": "Open", "type": "url", "value": "https://example.com", "row": 0}])
        self.assertEqual(access_cfg["mode"], "service_owner")
        self.assertTrue(nsfw_cfg["enabled"])
        self.assertEqual(nsfw_cfg["sensitivity"], "medium")
        self.assertTrue(nsfw_cfg["allow_miss"])
        self.assertFalse(nsfw_cfg["notice_enabled"])
        self.assertEqual(nsfw_cfg["delay_delete_sec"], 15)
        self.assertEqual(lang_cfg["allowed"], ["en", "zh"])

    def test_save_module_payload_rejects_lang_allowed_when_not_array(self):
        web_service = importlib.reload(web_service_module)

        with patch.object(web_service, "get_group_config", return_value={"language_whitelist": {}}):
            with self.assertRaisesRegex(ValueError, "lang.data.allowed must be a JSON array"):
                web_service.save_module_payload(-100123, "lang", {"data": {"allowed": {}}})

class AdminAccessRuntimeRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_can_manage_group_service_owner_binds_first_owner(self):
        admin_mod = importlib.reload(admin_module)
        context = SimpleNamespace(bot=SimpleNamespace())

        with patch.object(admin_mod, "get_group_config", return_value={"admin_access": {"mode": "service_owner"}}), patch.object(
            admin_mod,
            "is_admin",
            new=AsyncMock(return_value=True),
        ), patch.object(admin_mod, "group_service_owner_id", return_value=None), patch.object(
            admin_mod,
            "maybe_bind_group_service_owner",
            return_value=42,
        ) as bind_owner:
            allowed = await admin_mod._can_manage_group(context, 42, -100123)

        self.assertTrue(allowed)
        bind_owner.assert_called_once_with(-100123, 42)

    async def test_admin_callback_rejects_stale_group_session(self):
        admin_mod = importlib.reload(admin_module)
        update = SimpleNamespace(
            callback_query=SimpleNamespace(data="adminx:fun:menu", from_user=SimpleNamespace(id=42)),
            effective_user=SimpleNamespace(id=42),
        )
        state = {"active_group_id": -100123, "state": None, "tmp": {}}

        with patch.object(admin_mod, "get_admin_state", return_value=state), patch.object(
            admin_mod,
            "handle_private_home_callback",
            new=AsyncMock(return_value=False),
        ), patch.object(admin_mod, "_can_manage_group", new=AsyncMock(return_value=False)), patch.object(
            admin_mod,
            "_save_state",
        ) as save_state, patch.object(admin_mod, "safe_answer", new=AsyncMock()) as safe_answer, patch.object(
            admin_mod,
            "show_group_select",
            new=AsyncMock(),
        ) as show_group_select, patch.object(
            admin_extra,
            "handle_admin_extra_callback",
            new=AsyncMock(return_value=True),
        ) as extra_callback:
            await admin_mod.admin_callback(update, SimpleNamespace())

        save_state.assert_called_once()
        safe_answer.assert_awaited_once()
        show_group_select.assert_awaited_once()
        extra_callback.assert_not_awaited()


class NsfwRuntimeConfigRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_nsfw_filter_high_sensitivity_matches_soft_keyword(self):
        extra_features = importlib.reload(extra_features_module)
        bot = SimpleNamespace(delete_message=AsyncMock())
        context = SimpleNamespace(bot=bot)
        message = SimpleNamespace(text="adult content drop", caption=None, chat=SimpleNamespace(id=-100123), message_id=34, document=None, sticker=None)
        chat = SimpleNamespace(id=-100123)
        cfg = {"nsfw": {"enabled": True, "sensitivity": "high", "allow_miss": False, "notice_enabled": False, "delay_delete_sec": 0}}

        with patch.object(extra_features, "get_group_config", return_value=cfg):
            handled = await extra_features.handle_nsfw_filter(context, message, chat, False)

        self.assertTrue(handled)
        bot.delete_message.assert_awaited_once_with(chat_id=-100123, message_id=34)

    async def test_handle_nsfw_filter_allow_miss_relaxes_single_keyword_match(self):
        extra_features = importlib.reload(extra_features_module)
        bot = SimpleNamespace(delete_message=AsyncMock())
        context = SimpleNamespace(bot=bot)
        message = SimpleNamespace(text="free porn now", caption=None, chat=SimpleNamespace(id=-100123), message_id=35, document=None, sticker=None)
        chat = SimpleNamespace(id=-100123)
        cfg = {"nsfw": {"enabled": True, "sensitivity": "medium", "allow_miss": True, "notice_enabled": False, "delay_delete_sec": 0}}

        with patch.object(extra_features, "get_group_config", return_value=cfg):
            handled = await extra_features.handle_nsfw_filter(context, message, chat, False)

        self.assertFalse(handled)
        bot.delete_message.assert_not_awaited()

class AdminExtraBridgeRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_admin_extra_callback_updates_admin_access_mode(self):
        extra = importlib.reload(admin_extra)
        update = SimpleNamespace(
            callback_query=SimpleNamespace(data="adminx:admin_access:set:service_owner"),
            effective_user=SimpleNamespace(id=42),
            effective_message=None,
        )
        state = {"active_group_id": -100123, "state": None, "tmp": {}}
        cfg = {"admin_access": {"mode": "all_admins"}, "service_owner_user_id": 0}

        with patch.object(extra, "get_group_config", return_value=cfg), patch.object(
            extra,
            "save_group_config",
        ) as save_cfg, patch.object(extra, "safe_answer", new=AsyncMock()) as safe_answer, patch.object(
            extra,
            "show_admin_access_menu",
            new=AsyncMock(),
        ) as show_menu:
            handled = await extra.handle_admin_extra_callback(update, None, state)

        self.assertTrue(handled)
        self.assertEqual(cfg["admin_access"]["mode"], "service_owner")
        save_cfg.assert_called_once_with(-100123, cfg)
        safe_answer.assert_awaited_once()
        show_menu.assert_awaited_once()

    async def test_handle_admin_extra_callback_cycles_nsfw_sensitivity(self):
        extra = importlib.reload(admin_extra)
        update = SimpleNamespace(
            callback_query=SimpleNamespace(data="adminx:nsfw:cycle:sensitivity"),
            effective_user=SimpleNamespace(id=42),
            effective_message=None,
        )
        state = {"active_group_id": -100123, "state": None, "tmp": {}}
        cfg = {"nsfw": {"enabled": True, "sensitivity": "medium", "allow_miss": False, "notice_enabled": True, "delay_delete_sec": 0}}

        with patch.object(extra, "get_group_config", return_value=cfg), patch.object(
            extra,
            "save_group_config",
        ) as save_cfg, patch.object(extra, "safe_answer", new=AsyncMock()) as safe_answer, patch.object(
            extra,
            "show_nsfw_menu",
            new=AsyncMock(),
        ) as show_menu:
            handled = await extra.handle_admin_extra_callback(update, None, state)

        self.assertTrue(handled)
        self.assertEqual(cfg["nsfw"]["sensitivity"], "high")
        save_cfg.assert_called_once_with(-100123, cfg)
        safe_answer.assert_awaited_once()
        show_menu.assert_awaited_once()


class WebRuntimeBridgeRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_build_module_runtime_reports_admin_access_owner_state(self):
        web_service = importlib.reload(web_service_module)
        cfg = {"group_title": "Test Group", "admin_access": {"mode": "service_owner"}}

        with patch.object(web_service, "get_group_config", return_value=cfg), patch.object(
            web_service,
            "group_service_owner_id",
            return_value=42,
        ), patch.object(web_service, "has_active_membership", return_value=True):
            runtime = await web_service.build_module_runtime(SimpleNamespace(), -100123, "admin_access")

        self.assertEqual(runtime["mode"], "service_owner")
        self.assertEqual(runtime["service_owner_id"], 42)
        self.assertTrue(runtime["service_owner_bound"])
        self.assertTrue(runtime["service_owner_active_membership"])

    async def test_build_module_runtime_reports_nsfw_threshold(self):
        web_service = importlib.reload(web_service_module)
        cfg = {"group_title": "Test Group", "nsfw": {"enabled": True, "sensitivity": "high", "allow_miss": True}}

        with patch.object(web_service, "get_group_config", return_value=cfg):
            runtime = await web_service.build_module_runtime(SimpleNamespace(), -100123, "nsfw")

        self.assertTrue(runtime["enabled"])
        self.assertEqual(runtime["sensitivity"], "high")
        self.assertTrue(runtime["allow_miss"])
        self.assertEqual(runtime["effective_threshold"], 2)
        self.assertEqual(runtime["heuristic_mode"], "keyword_score")
    async def test_build_module_runtime_reports_schedule_queue_state(self):
        web_service = importlib.reload(web_service_module)
        cfg = {"group_title": "Test Group", "schedule": {"enabled": True}}
        items = [
            {"enabled": True, "next_at": 200},
            {"enabled": False, "next_at": 100},
            {"enabled": True, "next_at": 150},
        ]

        with patch.object(web_service, "get_group_config", return_value=cfg), patch.object(
            web_service,
            "load_schedule_items",
            return_value=items,
        ), patch.object(web_service, "schedule_limit_for_group", return_value=5):
            runtime = await web_service.build_module_runtime(SimpleNamespace(), -100123, "schedule")

        self.assertTrue(runtime["module_enabled"])
        self.assertEqual(runtime["item_count"], 3)
        self.assertEqual(runtime["enabled_item_count"], 2)
        self.assertEqual(runtime["item_limit"], 5)
        self.assertEqual(runtime["next_run_at"], 150)

    async def test_build_module_runtime_reports_related_message_state(self):
        web_service = importlib.reload(web_service_module)
        cfg = {
            "group_title": "Test Group",
            "related_channel": {
                "cancel_top_pin": True,
                "occupy_comment": True,
                "occupy_comment_text": "Pinned message",
                "occupy_comment_photo_file_id": "PHOTO-1",
                "occupy_comment_buttons": [{"text": "Open"}, {"text": "Join"}],
            },
        }

        with patch.object(web_service, "get_group_config", return_value=cfg):
            runtime = await web_service.build_module_runtime(SimpleNamespace(), -100123, "related")

        self.assertTrue(runtime["cancel_top_pin"])
        self.assertTrue(runtime["occupy_comment"])
        self.assertTrue(runtime["comment_text_set"])
        self.assertTrue(runtime["comment_photo_set"])
        self.assertEqual(runtime["comment_button_count"], 2)

    async def test_build_module_runtime_reports_language_whitelist_state(self):
        web_service = importlib.reload(web_service_module)
        cfg = {
            "group_title": "Test Group",
            "language_whitelist": {"enabled": True, "allowed": ["en", "zh", "jp"]},
        }

        with patch.object(web_service, "get_group_config", return_value=cfg):
            runtime = await web_service.build_module_runtime(SimpleNamespace(), -100123, "lang")

        self.assertTrue(runtime["enabled"])
        self.assertEqual(runtime["allowed_count"], 3)
        self.assertEqual(runtime["allowed_languages"], ["en", "zh", "jp"])
    async def test_build_module_runtime_reports_points_state(self):
        web_service = importlib.reload(web_service_module)
        cfg = {
            "group_title": "Test Group",
            "points": {
                "enabled": True,
                "chat_points_enabled": True,
                "sign_command": "sign",
                "query_command": "points",
                "rank_command": "points_rank",
                "sign_points": 5,
                "chat_points_per_message": 2,
            },
        }

        with patch.object(web_service, "get_group_config", return_value=cfg), patch.object(
            web_service,
            "kv_get_json",
            side_effect=lambda key, default=None: [1, 2, 3] if key == web_service._points_users_key(-100123) else (default if default is not None else []),
        ):
            runtime = await web_service.build_module_runtime(SimpleNamespace(), -100123, "points")

        self.assertTrue(runtime["enabled"])
        self.assertTrue(runtime["chat_points_enabled"])
        self.assertEqual(runtime["tracked_user_count"], 3)
        self.assertEqual(runtime["sign_command"], "sign")
        self.assertEqual(runtime["chat_points_per_message"], 2)

    async def test_build_module_runtime_reports_activity_state(self):
        web_service = importlib.reload(web_service_module)
        cfg = {
            "group_title": "Test Group",
            "activity": {"enabled": True, "today_command": "today", "month_command": "month", "total_command": "total"},
        }

        with patch.object(web_service, "get_group_config", return_value=cfg), patch.object(
            web_service,
            "kv_get_json",
            side_effect=lambda key, default=None: [11, 22] if key == web_service._activity_users_key(-100123) else (default if default is not None else []),
        ):
            runtime = await web_service.build_module_runtime(SimpleNamespace(), -100123, "activity")

        self.assertTrue(runtime["enabled"])
        self.assertEqual(runtime["tracked_user_count"], 2)
        self.assertEqual(runtime["today_command"], "today")
        self.assertEqual(runtime["month_command"], "month")
        self.assertEqual(runtime["total_command"], "total")

    async def test_build_module_runtime_reports_crypto_state(self):
        web_service = importlib.reload(web_service_module)
        cfg = {
            "group_title": "Test Group",
            "crypto": {
                "wallet_query_enabled": True,
                "price_query_enabled": False,
                "push_enabled": True,
                "default_symbol": "ETH",
                "query_alias": "q",
            },
        }

        with patch.object(web_service, "get_group_config", return_value=cfg):
            runtime = await web_service.build_module_runtime(SimpleNamespace(), -100123, "crypto")

        self.assertTrue(runtime["wallet_query_enabled"])
        self.assertFalse(runtime["price_query_enabled"])
        self.assertTrue(runtime["push_enabled"])
        self.assertEqual(runtime["default_symbol"], "ETH")
        self.assertEqual(runtime["query_alias"], "q")

    async def test_build_module_runtime_reports_invite_state(self):
        web_service = importlib.reload(web_service_module)
        cfg = {
            "group_title": "Test Group",
            "invite_links": {
                "enabled": True,
                "notify_enabled": True,
                "join_review": True,
                "reward_points": 8,
                "notify_buttons": [{"text": "Open"}, {"text": "Rank"}],
            },
        }

        with patch.object(web_service, "get_group_config", return_value=cfg), patch.object(
            web_service,
            "kv_get_json",
            side_effect=lambda key, default=None: [7, 8] if key == web_service._invite_users_key(-100123) else (default if default is not None else []),
        ):
            runtime = await web_service.build_module_runtime(SimpleNamespace(), -100123, "invite")

        self.assertTrue(runtime["enabled"])
        self.assertTrue(runtime["notify_enabled"])
        self.assertTrue(runtime["join_review"])
        self.assertEqual(runtime["reward_points"], 8)
        self.assertEqual(runtime["tracked_inviter_count"], 2)
        self.assertEqual(runtime["notify_button_count"], 2)

    async def test_build_module_runtime_reports_autodelete_state(self):
        web_service = importlib.reload(web_service_module)
        cfg = {"group_title": "Test Group"}
        delete_cfg = {
            "delete_links": True,
            "delete_documents": True,
            "delete_videos": False,
            "delete_other_commands": True,
            "exclude_admins": True,
            "long_length": 240,
            "custom_rules": [{"keyword": "spam"}, {"keyword": "promo"}],
            "ad_sticker_ids": ["A", "B", "C"],
        }

        with patch.object(web_service, "get_group_config", return_value=cfg), patch.object(
            web_service,
            "get_group_auto_delete",
            return_value=delete_cfg,
        ), patch.object(web_service, "_bot_can_manage_group", new=AsyncMock(return_value=True)):
            runtime = await web_service.build_module_runtime(SimpleNamespace(), -100123, "autodelete")

        self.assertTrue(runtime["bot_can_manage_group"])
        self.assertEqual(runtime["active_filter_count"], 3)
        self.assertEqual(runtime["custom_rule_count"], 2)
        self.assertEqual(runtime["ad_sticker_count"], 3)
        self.assertTrue(runtime["exclude_admins"])
        self.assertEqual(runtime["long_length"], 240)

    async def test_build_module_runtime_reports_autoban_state(self):
        web_service = importlib.reload(web_service_module)
        cfg = {"group_title": "Test Group"}
        ban_cfg = {
            "enabled": True,
            "default_duration_sec": 3600,
            "rules": [
                {"keyword": "spam", "mode": "contains"},
                {"keyword": "https?://", "mode": "regex"},
            ],
        }

        with patch.object(web_service, "get_group_config", return_value=cfg), patch.object(
            web_service,
            "get_group_auto_ban",
            return_value=ban_cfg,
        ), patch.object(web_service, "_bot_can_manage_group", new=AsyncMock(return_value=False)):
            runtime = await web_service.build_module_runtime(SimpleNamespace(), -100123, "autoban")

        self.assertFalse(runtime["bot_can_manage_group"])
        self.assertTrue(runtime["enabled"])
        self.assertEqual(runtime["rule_count"], 2)
        self.assertEqual(runtime["regex_rule_count"], 1)
        self.assertEqual(runtime["default_duration_sec"], 3600)

    async def test_build_module_runtime_reports_automute_state(self):
        web_service = importlib.reload(web_service_module)
        cfg = {"group_title": "Test Group"}
        mute_cfg = {
            "default_duration_sec": 90,
            "rules": [
                {"keyword": "flood", "mode": "contains"},
                {"keyword": "[A-Z]{8}", "mode": "regex"},
            ],
        }

        with patch.object(web_service, "get_group_config", return_value=cfg), patch.object(
            web_service,
            "get_group_auto_mute",
            return_value=mute_cfg,
        ), patch.object(web_service, "_bot_can_manage_group", new=AsyncMock(return_value=True)):
            runtime = await web_service.build_module_runtime(SimpleNamespace(), -100123, "automute")

        self.assertTrue(runtime["bot_can_manage_group"])
        self.assertEqual(runtime["rule_count"], 2)
        self.assertEqual(runtime["regex_rule_count"], 1)
        self.assertEqual(runtime["default_duration_sec"], 90)

    async def test_build_module_runtime_reports_autowarn_state(self):
        web_service = importlib.reload(web_service_module)
        cfg = {"group_title": "Test Group"}
        warn_cfg = {
            "enabled": True,
            "warn_limit": 4,
            "action": "kick",
            "cmd_mute_enabled": True,
            "mute_seconds": 600,
            "rules": [{"keyword": "rude", "mode": "contains"}],
        }

        with patch.object(web_service, "get_group_config", return_value=cfg), patch.object(
            web_service,
            "get_group_auto_warn",
            return_value=warn_cfg,
        ), patch.object(web_service, "_bot_can_manage_group", new=AsyncMock(return_value=True)):
            runtime = await web_service.build_module_runtime(SimpleNamespace(), -100123, "autowarn")

        self.assertTrue(runtime["bot_can_manage_group"])
        self.assertTrue(runtime["enabled"])
        self.assertEqual(runtime["rule_count"], 1)
        self.assertEqual(runtime["warn_limit"], 4)
        self.assertEqual(runtime["action"], "kick")
        self.assertTrue(runtime["cmd_mute_enabled"])
        self.assertEqual(runtime["mute_seconds"], 600)

    async def test_build_module_runtime_reports_antispam_state(self):
        web_service = importlib.reload(web_service_module)
        cfg = {"group_title": "Test Group"}
        spam_cfg = {
            "enabled": True,
            "action": "ban",
            "window_sec": 12,
            "threshold": 5,
            "types": ["text", "photo", "link"],
        }

        with patch.object(web_service, "get_group_config", return_value=cfg), patch.object(
            web_service,
            "get_group_anti_spam",
            return_value=spam_cfg,
        ), patch.object(web_service, "_bot_can_manage_group", new=AsyncMock(return_value=True)):
            runtime = await web_service.build_module_runtime(SimpleNamespace(), -100123, "antispam")

        self.assertTrue(runtime["bot_can_manage_group"])
        self.assertTrue(runtime["enabled"])
        self.assertEqual(runtime["action"], "ban")
        self.assertEqual(runtime["window_sec"], 12)
        self.assertEqual(runtime["threshold"], 5)
        self.assertEqual(runtime["type_count"], 3)
        self.assertEqual(runtime["types"], ["text", "photo", "link"])


    async def test_build_module_runtime_reports_autoreply_state(self):
        web_service = importlib.reload(web_service_module)
        cfg = {"group_title": "Test Group"}
        rules = [
            {"enabled": True, "photo_file_id": "PHOTO-1", "buttons": [{"text": "Open"}]},
            {"enabled": False, "photo_file_id": "", "buttons": []},
            {"enabled": True, "photo_file_id": "", "buttons": [{"text": "A"}, {"text": "B"}]},
        ]

        with patch.object(web_service, "get_group_config", return_value=cfg), patch.object(
            web_service,
            "get_group_auto_replies",
            return_value=rules,
        ), patch.object(web_service, "auto_reply_limit_for_group", return_value=10):
            runtime = await web_service.build_module_runtime(SimpleNamespace(), -100123, "autoreply")

        self.assertEqual(runtime["rule_count"], 3)
        self.assertEqual(runtime["enabled_rule_count"], 2)
        self.assertEqual(runtime["photo_rule_count"], 1)
        self.assertEqual(runtime["button_rule_count"], 3)
        self.assertEqual(runtime["rule_limit"], 10)

    async def test_build_module_runtime_reports_ad_state(self):
        web_service = importlib.reload(web_service_module)
        cfg = {
            "group_title": "Test Group",
            "ad_filter": {
                "nickname_enabled": True,
                "sticker_enabled": False,
                "message_enabled": True,
                "block_channel_mask": True,
            },
        }

        with patch.object(web_service, "get_group_config", return_value=cfg):
            runtime = await web_service.build_module_runtime(SimpleNamespace(), -100123, "ad")

        self.assertEqual(runtime["active_filter_count"], 3)
        self.assertTrue(runtime["nickname_enabled"])
        self.assertFalse(runtime["sticker_enabled"])
        self.assertTrue(runtime["message_enabled"])
        self.assertTrue(runtime["block_channel_mask"])

    async def test_build_module_runtime_reports_command_gate_state(self):
        web_service = importlib.reload(web_service_module)
        cfg = {
            "group_title": "Test Group",
            "command_gate": {"sign": True, "profile": False, "warn": True, "mute": True},
        }

        with patch.object(web_service, "get_group_config", return_value=cfg):
            runtime = await web_service.build_module_runtime(SimpleNamespace(), -100123, "cmd")

        self.assertEqual(runtime["blocked_command_count"], 3)
        self.assertEqual(runtime["blocked_commands"], ["sign", "warn", "mute"])

    async def test_build_module_runtime_reports_member_watch_state(self):
        web_service = importlib.reload(web_service_module)
        cfg = {
            "group_title": "Test Group",
            "member_watch": {"nickname_change_detect": True, "nickname_change_notice": False},
        }

        with patch.object(web_service, "get_group_config", return_value=cfg):
            runtime = await web_service.build_module_runtime(SimpleNamespace(), -100123, "member")

        self.assertTrue(runtime["nickname_change_detect"])
        self.assertFalse(runtime["nickname_change_notice"])

    async def test_build_module_runtime_reports_fun_state(self):
        web_service = importlib.reload(web_service_module)
        cfg = {
            "group_title": "Test Group",
            "entertainment": {
                "dice_enabled": True,
                "dice_cost": 12,
                "dice_command": "/roll",
                "gomoku_enabled": True,
                "gomoku_command": "/five",
            },
        }
        active_game = {"status": "playing", "creator_id": 11, "challenger_id": 22}

        with patch.object(web_service, "get_group_config", return_value=cfg), patch.object(
            web_service,
            "get_active_gomoku_game",
            return_value=active_game,
        ):
            runtime = await web_service.build_module_runtime(SimpleNamespace(), -100123, "fun")

        self.assertTrue(runtime["dice_enabled"])
        self.assertEqual(runtime["dice_cost"], 12)
        self.assertEqual(runtime["dice_command"], "/roll")
        self.assertTrue(runtime["gomoku_enabled"])
        self.assertEqual(runtime["gomoku_command"], "/five")
        self.assertTrue(runtime["gomoku_active"])
        self.assertEqual(runtime["gomoku_status"], "playing")
        self.assertEqual(runtime["gomoku_player_count"], 2)

    async def test_build_module_runtime_reports_usdt_state(self):
        web_service = importlib.reload(web_service_module)
        cfg = {
            "group_title": "Test Group",
            "usdt_price": {
                "enabled": True,
                "tier": "best",
                "show_query_message": False,
                "show_calc_message": True,
                "alias_z": "z",
                "alias_w": "w",
                "alias_k": "k",
                "exchanges": ["binance", "okx"],
            },
        }

        with patch.object(web_service, "get_group_config", return_value=cfg):
            runtime = await web_service.build_module_runtime(SimpleNamespace(), -100123, "usdt")

        self.assertTrue(runtime["enabled"])
        self.assertEqual(runtime["tier"], "best")
        self.assertFalse(runtime["show_query_message"])
        self.assertTrue(runtime["show_calc_message"])
        self.assertEqual(runtime["exchange_count"], 2)
        self.assertEqual(runtime["exchanges"], ["binance", "okx"])
        self.assertEqual(runtime["alias_z"], "z")
        self.assertEqual(runtime["alias_w"], "w")
        self.assertEqual(runtime["alias_k"], "k")

    async def test_build_module_runtime_reports_lottery_state(self):
        web_service = importlib.reload(web_service_module)
        cfg = {
            "group_title": "Test Group",
            "lottery": {
                "enabled": True,
                "query_command": "lottery",
                "auto_delete_sec": 30,
                "pin_post": True,
                "pin_result": False,
            },
        }
        active_lottery = {"participants": [1, 2, 3], "winner_count": 2}

        with patch.object(web_service, "get_group_config", return_value=cfg), patch.object(
            web_service,
            "get_active_lottery",
            return_value=active_lottery,
        ):
            runtime = await web_service.build_module_runtime(SimpleNamespace(), -100123, "lottery")

        self.assertTrue(runtime["enabled"])
        self.assertEqual(runtime["query_command"], "lottery")
        self.assertEqual(runtime["auto_delete_sec"], 30)
        self.assertTrue(runtime["pin_post"])
        self.assertFalse(runtime["pin_result"])
        self.assertTrue(runtime["active_lottery"])
        self.assertEqual(runtime["active_participant_count"], 3)
        self.assertEqual(runtime["active_winner_count"], 2)

    async def test_build_module_runtime_reports_verified_state(self):
        web_service = importlib.reload(web_service_module)
        cfg = {"group_title": "Test Group", "verified_user": {"enabled": True}}

        with patch.object(web_service, "get_group_config", return_value=cfg):
            runtime = await web_service.build_module_runtime(SimpleNamespace(), -100123, "verified")

        self.assertTrue(runtime["enabled"])
