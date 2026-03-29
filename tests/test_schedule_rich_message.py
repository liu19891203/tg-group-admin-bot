import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, '.')

from tmp_runtime_patch_admin import load_patched_admin

admin_mod = load_patched_admin()
import bot.handlers.admin_extra as admin_extra
import bot.handlers.callbacks as callbacks
import bot.services.extra_features as extra_features


class _MemoryKv:
    def __init__(self):
        self.data = {}

    def get_json(self, key, default=None):
        return self.data.get(key, default)

    def set_json(self, key, value):
        self.data[key] = value


class _FakeBot:
    def __init__(self):
        self.send_photo = AsyncMock(return_value=SimpleNamespace(message_id=101))
        self.send_message = AsyncMock(return_value=SimpleNamespace(message_id=202))


class ScheduleRichMessageTests(unittest.IsolatedAsyncioTestCase):
    async def test_parse_schedule_message_input_supports_json_buttons(self):
        item = extra_features.parse_schedule_message_input(
            '{"text":"定时提醒","interval_minutes":120,"photo_file_id":"FILE-1","buttons":[{"text":"查看","type":"url","value":"https://example.com","row":0}]}'
        )

        self.assertEqual(item["text"], "定时提醒")
        self.assertEqual(item["photo_file_id"], "FILE-1")
        self.assertEqual(item["interval_sec"], 7200)
        self.assertEqual(item["buttons"][0]["text"], "查看")

    async def test_schedule_add_accepts_photo_caption_and_saves_single_item(self):
        memory = _MemoryKv()
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=42),
            effective_message=SimpleNamespace(
                text=None,
                caption='定时提醒 | 120',
                photo=[SimpleNamespace(file_id='PHOTO-1')],
                reply_text=AsyncMock(),
            ),
            callback_query=None,
        )
        context = SimpleNamespace()
        state = {"active_group_id": -100123, "state": "x:schedule:add", "tmp": {}}
        saved_state = {}

        def _capture_state(user_id, value):
            saved_state["user_id"] = user_id
            saved_state["state"] = value

        with patch.object(extra_features, 'kv_get_json', side_effect=memory.get_json),              patch.object(extra_features, 'kv_set_json', side_effect=memory.set_json),              patch.object(admin_extra, '_save_state', side_effect=_capture_state),              patch.object(admin_extra, '_send_or_edit', new=AsyncMock()),              patch.object(admin_extra, 'schedule_limit_for_group', return_value=5):
            handled = await admin_extra.handle_admin_extra_message(update, context, state, update.effective_message.caption)

        self.assertTrue(handled)
        key = extra_features._schedule_key(-100123)
        items = memory.data[key]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["text"], "定时提醒")
        self.assertEqual(items[0]["photo_file_id"], "PHOTO-1")
        self.assertEqual(items[0]["interval_sec"], 7200)
        self.assertEqual(saved_state["user_id"], 42)
        self.assertEqual(saved_state["state"]["state"], None)

    async def test_process_scheduled_messages_sends_one_photo_message_with_buttons(self):
        memory = _MemoryKv()
        bot = _FakeBot()
        item = {
            "id": 12345,
            "text": "定时提醒",
            "photo_file_id": "PHOTO-2",
            "buttons": [{"text": "查看", "type": "callback", "value": "查看详情", "row": 0}],
            "interval_sec": 120,
            "next_at": 1,
            "enabled": True,
        }
        memory.data[extra_features._schedule_key(-100123)] = [item]
        context = SimpleNamespace(bot=bot)

        with patch.object(extra_features, 'kv_get_json', side_effect=memory.get_json),              patch.object(extra_features, 'kv_set_json', side_effect=memory.set_json),              patch('tmp_runtime_patch_admin.time.time', return_value=1000):
            await extra_features.process_scheduled_messages(context, -100123)

        bot.send_photo.assert_awaited_once()
        bot.send_message.assert_not_called()
        kwargs = bot.send_photo.await_args.kwargs
        self.assertEqual(kwargs["chat_id"], -100123)
        self.assertEqual(kwargs["photo"], "PHOTO-2")
        self.assertEqual(kwargs["caption"], "定时提醒")
        self.assertEqual(kwargs["reply_markup"].inline_keyboard[0][0].callback_data, 'smb:12345:-100123:0')
        saved = memory.data[extra_features._schedule_key(-100123)][0]
        self.assertEqual(saved["next_at"], 1120)

    async def test_schedule_button_callback_uses_saved_value(self):
        memory = _MemoryKv()
        memory.data[extra_features._schedule_key(-100123)] = [
            {
                "id": 12345,
                "text": "定时提醒",
                "photo_file_id": "",
                "buttons": [{"text": "查看", "type": "callback", "value": "查看详情", "row": 0}],
                "interval_sec": 120,
                "next_at": 1,
                "enabled": True,
            }
        ]
        query = SimpleNamespace(data='smb:12345:-100123:0', answer=AsyncMock(), from_user=SimpleNamespace(id=42))
        update = SimpleNamespace(callback_query=query)
        context = SimpleNamespace()

        with patch.object(extra_features, 'kv_get_json', side_effect=memory.get_json),              patch.object(extra_features, 'kv_set_json', side_effect=memory.set_json):
            await callbacks.callback_router(update, context)

        query.answer.assert_awaited_once_with('查看详情', show_alert=True)


if __name__ == '__main__':
    unittest.main()
