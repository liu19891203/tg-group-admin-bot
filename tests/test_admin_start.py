import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.models.config import STATE_NONE
from tmp_runtime_patch_admin import load_patched_admin

admin_mod = load_patched_admin()
admin_start = admin_mod.admin_start
show_group_select = admin_mod.show_group_select
show_autodelete_menu = admin_mod.show_autodelete_menu
show_main_menu = admin_mod.show_main_menu


class _FakeMessage:
    def __init__(self):
        self.calls = []

    async def reply_text(self, text, **kwargs):
        self.calls.append({"text": text, "kwargs": kwargs})
        return SimpleNamespace()


class _FakeBot:
    id = 123456

    async def get_me(self):
        return SimpleNamespace(username="test_bot")


class AdminStartTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_in_group_is_silent(self):
        message = _FakeMessage()
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=42),
            effective_chat=SimpleNamespace(id=-100123, type="supergroup"),
            effective_message=message,
            callback_query=None,
        )
        context = SimpleNamespace(bot=_FakeBot())

        with patch.object(admin_mod, "_save_state") as save_state:
            await admin_start(update, context)

        save_state.assert_not_called()
        self.assertEqual(message.calls, [])

    async def test_start_resets_admin_state_and_sends_private_home(self):
        message = _FakeMessage()
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=42),
            effective_chat=SimpleNamespace(id=42, type="private"),
            effective_message=message,
            callback_query=None,
        )
        context = SimpleNamespace(bot=_FakeBot())
        saved = {}

        def _capture_save(user_id, state):
            saved["user_id"] = user_id
            saved["state"] = state

        with patch.object(
            admin_mod,
            "get_admin_state",
            return_value={"active_group_id": -100123, "state": "broken", "tmp": {"mode": "verify"}},
        ), patch.object(admin_mod, "_save_state", side_effect=_capture_save):
            await admin_start(update, context)

        self.assertEqual(saved["user_id"], 42)
        self.assertEqual(
            saved["state"],
            {"active_group_id": None, "state": STATE_NONE, "tmp": {}},
        )
        self.assertEqual(len(message.calls), 1)
        self.assertIn("@test_bot", message.calls[0]["text"])

    async def test_show_group_select_renders_group_and_web_buttons(self):
        message = _FakeMessage()
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=42),
            effective_chat=SimpleNamespace(id=42, type="private"),
            effective_message=message,
            callback_query=None,
        )
        context = SimpleNamespace(bot=_FakeBot())

        with patch.object(
            admin_mod,
            "_manageable_groups",
            new=AsyncMock(return_value=[{"id": -100123, "title": "\u7eb5\u6a2a\u96c6\u56e2\u4e00\u7fa4"}]),
        ), patch.object(
            admin_mod,
            "_web_admin_base_url",
            new=AsyncMock(return_value="https://admin.example/web/"),
        ), patch.object(
            admin_mod,
            "create_bot_entry_login_request",
            return_value={"request_id": "bot-login-1"},
        ):
            await show_group_select(update, context, {})

        self.assertEqual(len(message.calls), 1)
        markup = message.calls[0]["kwargs"]["reply_markup"]
        first_row = markup.inline_keyboard[0]
        self.assertEqual(first_row[0].text, "\u7eb5\u6a2a\u96c6\u56e2\u4e00\u7fa4")
        self.assertEqual(first_row[0].callback_data, "admin:select_group:-100123")
        self.assertEqual(first_row[1].text, "\U0001f310 \u8fdb\u5165Web")
        self.assertEqual(first_row[1].url, "https://admin.example/web/?group_id=-100123&bot_login=bot-login-1")


    async def test_show_autodelete_menu_explains_check_and_cross(self):
        message = _FakeMessage()
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=42),
            effective_chat=SimpleNamespace(id=42, type="private"),
            effective_message=message,
            callback_query=None,
        )
        context = SimpleNamespace(bot=_FakeBot())
        state = {"active_group_id": -100123, "state": None, "tmp": {}}
        cfg = {
            "delete_system": True,
            "delete_channel_mask": False,
            "delete_links": True,
            "delete_long": True,
            "delete_videos": True,
            "delete_stickers": False,
            "delete_forwarded": False,
            "delete_ad_stickers": False,
            "delete_archives": False,
            "delete_executables": False,
            "delete_notice_text": True,
            "delete_documents": True,
            "delete_mentions": True,
            "delete_other_commands": False,
            "delete_qr": False,
            "delete_edited": False,
            "delete_member_emoji": False,
            "delete_member_emoji_only": False,
            "delete_external_reply": False,
            "delete_shared_contact": False,
            "exclude_admins": True,
        }

        with patch.object(admin_mod, "get_group_auto_delete", return_value=cfg):
            await show_autodelete_menu(update, context, state)

        self.assertEqual(len(message.calls), 1)
        text = message.calls[0]["text"]
        self.assertIn("✅ = 开启删除", text)
        self.assertIn("❌ = 关闭删除", text)


    async def test_show_main_menu_includes_new_adminx_entries(self):
        message = _FakeMessage()
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=42),
            effective_chat=SimpleNamespace(id=42, type="private"),
            effective_message=message,
            callback_query=None,
        )
        context = SimpleNamespace(bot=_FakeBot())
        state = {"active_group_id": -100123, "state": None, "tmp": {}}

        with patch.object(admin_mod, "get_group_config", return_value={"group_title": "Test Group"}):
            await show_main_menu(update, context, state)

        self.assertEqual(len(message.calls), 1)
        buttons = [button.callback_data for row in message.calls[0]["kwargs"]["reply_markup"].inline_keyboard for button in row if getattr(button, "callback_data", None)]
        self.assertIn("adminx:fun:menu", buttons)
        self.assertIn("adminx:lottery:menu", buttons)
        self.assertIn("adminx:verified:menu", buttons)


if __name__ == "__main__":
    unittest.main()
