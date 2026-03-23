import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import api.web as api_web
from bot.handlers import callbacks as callbacks_mod
from bot.web import login_bot, login_flow


class WebLoginFlowTests(unittest.TestCase):
    def test_create_request_normalizes_origin_and_group(self):
        store = {}

        def fake_set(key, value):
            store[key] = value
            return True

        with patch.object(login_flow.secrets, 'token_hex', return_value='abc123def4567890'), \
             patch.object(login_flow.secrets, 'token_urlsafe', return_value='browser-token'), \
             patch.object(login_flow.time, 'time', return_value=1000), \
             patch.object(login_flow, 'kv_set_json', side_effect=fake_set):
            request = login_flow.create_web_login_request(-10042, 'https://example.com/web?group_id=-10042')

        self.assertEqual(request['request_id'], 'abc123def4567890')
        self.assertEqual(request['browser_token'], 'browser-token')
        self.assertEqual(request['status'], 'pending')
        self.assertEqual(request['requested_group_id'], -10042)
        self.assertEqual(request['origin'], 'https://example.com')
        self.assertEqual(store['web_login:abc123def4567890']['expires_at'], 1000 + login_flow.WEB_LOGIN_TTL_SEC)

    def test_approve_and_consume_request(self):
        request = {
            'request_id': 'abc123def4567890',
            'browser_token': 'browser-token',
            'status': 'pending',
            'created_at': 1000,
            'expires_at': 1600,
            'requested_group_id': -10042,
            'origin': 'https://example.com',
            'approved_at': 0,
            'user': None,
        }
        store = {'web_login:abc123def4567890': dict(request)}

        def fake_get(key, default=None):
            value = store.get(key)
            if value is None:
                return default
            return dict(value)

        def fake_set(key, value):
            store[key] = dict(value)
            return True

        def fake_del(key):
            store.pop(key, None)
            return True

        user = SimpleNamespace(id=42, username='alice', first_name='Alice', last_name='Admin')
        with patch.object(login_flow.time, 'time', return_value=1100), \
             patch.object(login_flow, 'kv_get_json', side_effect=fake_get), \
             patch.object(login_flow, 'kv_set_json', side_effect=fake_set), \
             patch.object(login_flow, 'kv_del', side_effect=fake_del):
            ok, reason, approved = login_flow.approve_web_login_request('abc123def4567890', user)
            consumed = login_flow.consume_web_login_request('abc123def4567890', 'browser-token')

        self.assertTrue(ok)
        self.assertEqual(reason, 'approved')
        self.assertEqual(approved['user']['id'], 42)
        self.assertEqual(consumed['status'], 'approved')
        self.assertEqual(consumed['user']['username'], 'alice')
        self.assertEqual(consumed['requested_group_id'], -10042)
        self.assertNotIn('web_login:abc123def4567890', store)

    def test_read_status_rejects_wrong_browser_token(self):
        request = {
            'request_id': 'abc123def4567890',
            'browser_token': 'browser-token',
            'status': 'pending',
            'created_at': 1000,
            'expires_at': 1600,
            'requested_group_id': None,
            'origin': 'https://example.com',
            'approved_at': 0,
            'user': None,
        }
        with patch.object(login_flow.time, 'time', return_value=1100), \
             patch.object(login_flow, 'kv_get_json', return_value=request):
            result = login_flow.read_web_login_status('abc123def4567890', 'wrong-token')

        self.assertEqual(result['status'], 'forbidden')

    def test_parse_start_arg_accepts_login_token(self):
        self.assertEqual(login_bot.parse_web_login_start_arg('weblogin_abc123def4567890'), 'abc123def4567890')
        self.assertIsNone(login_bot.parse_web_login_start_arg('start'))

    def test_bootstrap_payload_includes_web_login_settings(self):
        fake_bot = SimpleNamespace(get_me=AsyncMock(return_value=SimpleNamespace(username='test_bot', id=123456)))

        async def fake_with_bot(action):
            return await action(fake_bot)

        with patch.object(api_web, '_with_bot', side_effect=fake_with_bot), \
             patch.object(api_web, 'local_debug_login_settings', return_value={'enabled': False}):
            payload = api_web.asyncio.run(api_web._bootstrap_payload())

        self.assertEqual(payload['bot_username'], 'test_bot')
        self.assertIn('web_login', payload)
        self.assertEqual(payload['web_login']['mode'], 'bot_deep_link')


class WebLoginBotTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_login_callback_approves_request(self):
        query = SimpleNamespace(
            data='weblogin:confirm:abc123def4567890',
            from_user=SimpleNamespace(id=42, first_name='Alice', full_name='Alice Admin'),
        )
        update = SimpleNamespace(callback_query=query)
        context = SimpleNamespace()
        request = {'origin': 'https://example.com'}

        with patch.object(login_bot, 'approve_web_login_request', return_value=(True, 'approved', request)), \
             patch.object(login_bot, 'safe_answer', new=AsyncMock()) as safe_answer_mock, \
             patch.object(login_bot, 'safe_edit_message', new=AsyncMock()) as safe_edit_mock:
            handled = await login_bot.handle_web_login_callback(update, context)

        self.assertTrue(handled)
        safe_answer_mock.assert_awaited_once()
        safe_edit_mock.assert_awaited_once()
        self.assertIn('\u767b\u5f55\u786e\u8ba4\u5b8c\u6210', safe_edit_mock.await_args.args[1])

    async def test_callback_router_prioritizes_web_login_callbacks(self):
        update = SimpleNamespace(callback_query=SimpleNamespace(data='weblogin:confirm:abc123def4567890'))
        context = SimpleNamespace()

        with patch.object(callbacks_mod, 'handle_web_login_callback', new=AsyncMock(return_value=True)) as login_mock, \
             patch.object(callbacks_mod, 'admin_callback', new=AsyncMock()) as admin_mock:
            await callbacks_mod.callback_router(update, context)

        login_mock.assert_awaited_once_with(update, context)
        admin_mock.assert_not_called()


if __name__ == '__main__':
    unittest.main()
