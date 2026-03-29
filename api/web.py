import asyncio
import hmac
import json
import os
import re
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

from bot.app import build_app
from bot.models.config import SUPER_ADMIN_ID
from bot.web.auth import (
    build_local_debug_login,
    cookie_header_for_logout,
    cookie_header_for_session,
    is_loopback_client,
    issue_session,
    read_session_from_cookie_header,
    verify_telegram_login,
)
from bot.web.login_flow import consume_web_login_request, create_web_login_request, web_login_settings
from bot.web.permissions import ensure_group_access, get_manageable_groups
from bot.web.schemas import list_modules
from bot.web.service import build_group_summary, build_module_runtime, load_module_payload, render_preview, save_module_payload

WEB_ROOT = Path(__file__).resolve().parent.parent / 'web'
STATIC_TYPES = {
    '.html': 'text/html; charset=utf-8',
    '.js': 'application/javascript; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
}
GROUP_SUMMARY_RE = re.compile(r'^/api/web/groups/(-?\d+)/summary$')
GROUP_MODULE_RE = re.compile(r'^/api/web/groups/(-?\d+)/module/([a-z_]+)$')


def local_debug_login_settings() -> dict:
    enabled_flag = os.environ.get('WEB_LOCAL_DEBUG_LOGIN_ENABLED', '0').strip() == '1'
    secret = os.environ.get('WEB_LOCAL_DEBUG_LOGIN_SECRET', '').strip()
    return {
        'enabled': bool(enabled_flag and int(SUPER_ADMIN_ID or 0) > 0 and secret),
        'requires_secret': True,
        'loopback_only': True,
    }


def is_authorized_local_debug_secret(provided_secret: str | None) -> bool:
    expected = os.environ.get('WEB_LOCAL_DEBUG_LOGIN_SECRET', '').strip()
    if not expected:
        return False
    candidate = str(provided_secret or '').strip()
    return hmac.compare_digest(candidate, expected)


async def _with_bot(action):
    app = build_app()
    await app.initialize()
    try:
        return await action(app.bot)
    finally:
        await app.shutdown()


async def _bootstrap_payload(local_debug_login: dict | None = None):
    async def _action(bot):
        me = await bot.get_me()
        return {
            'bot_username': me.username or '',
            'bot_id': int(me.id),
            'modules': list_modules(),
            'local_debug_login': dict(local_debug_login or local_debug_login_settings()),
            'web_login': web_login_settings(),
        }

    return await _with_bot(_action)


async def _session_payload(session: dict):
    user_id = int(session['id'])

    async def _action(bot):
        me = await bot.get_me()
        groups = await get_manageable_groups(bot, user_id)
        return {
            'bot_username': me.username or '',
            'user': {
                'id': user_id,
                'username': session.get('username') or '',
                'first_name': session.get('first_name') or '',
                'last_name': session.get('last_name') or '',
            },
            'groups': groups,
            'modules': list_modules(),
        }

    return await _with_bot(_action)


async def _require_group_payload(session: dict, group_id: int, module_key: str | None = None, save_payload: dict | None = None):
    user_id = int(session['id'])

    async def _action(bot):
        if not await ensure_group_access(bot, user_id, group_id):
            return {'error': 'forbidden'}
        if save_payload is not None and module_key:
            payload = save_module_payload(group_id, module_key, save_payload)
        elif module_key:
            payload = load_module_payload(group_id, module_key)
        else:
            payload = build_group_summary(group_id, include_runtime=False)
        if module_key in {'verify', 'welcome', 'admin_access', 'nsfw', 'schedule', 'related', 'lang', 'points', 'activity', 'crypto', 'invite', 'autodelete', 'autoban', 'automute', 'autowarn', 'antispam', 'autoreply', 'ad', 'cmd', 'member', 'fun', 'usdt', 'lottery', 'verified'} and isinstance(payload, dict):
            payload['runtime'] = await build_module_runtime(bot, group_id, module_key)
        return {'ok': True, 'payload': payload}

    return await _with_bot(_action)


class handler(BaseHTTPRequestHandler):
    def _allow_local_debug_login(self) -> bool:
        settings = local_debug_login_settings()
        host = self.client_address[0] if self.client_address else ''
        return bool(settings.get('enabled')) and is_loopback_client(host)

    def _send_json(self, status: int, data: dict, cookie_header: str | None = None):
        raw = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        if cookie_header:
            self.send_header('Set-Cookie', cookie_header)
        self.end_headers()
        self.wfile.write(raw)

    def _send_text(self, status: int, body: str, content_type: str = 'text/plain; charset=utf-8'):
        raw = body.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _read_json(self) -> dict:
        length = int(self.headers.get('Content-Length', '0') or 0)
        raw = self.rfile.read(length) if length > 0 else b'{}'
        return json.loads(raw.decode('utf-8') or '{}')

    def _session(self):
        return read_session_from_cookie_header(self.headers.get('Cookie'))

    def _serve_static(self, path: str) -> bool:
        if path in {'/', '/web', '/web/'}:
            file_path = WEB_ROOT / 'index.html'
        elif path.startswith('/web/'):
            relative = path[len('/web/'):]
            file_path = (WEB_ROOT / relative).resolve()
            if not str(file_path).startswith(str(WEB_ROOT.resolve())):
                self._send_text(404, 'not found')
                return True
        else:
            return False
        if not file_path.exists() or not file_path.is_file():
            self._send_text(404, 'not found')
            return True
        content_type = STATIC_TYPES.get(file_path.suffix.lower(), 'application/octet-stream')
        self._send_text(200, file_path.read_text(encoding='utf-8'), content_type)
        return True

    def do_GET(self):
        parsed = urlparse(self.path)
        if self._serve_static(parsed.path):
            return
        if parsed.path == '/api/web/bootstrap':
            self._send_json(200, asyncio.run(_bootstrap_payload(local_debug_login_settings())))
            return
        if parsed.path == '/api/web/me':
            session = self._session()
            if not session:
                self._send_json(401, {'error': 'unauthorized'})
                return
            self._send_json(200, asyncio.run(_session_payload(session)))
            return
        match = GROUP_SUMMARY_RE.match(parsed.path)
        if match:
            session = self._session()
            if not session:
                self._send_json(401, {'error': 'unauthorized'})
                return
            result = asyncio.run(_require_group_payload(session, int(match.group(1))))
            if result.get('error'):
                self._send_json(403, result)
                return
            self._send_json(200, result['payload'])
            return
        match = GROUP_MODULE_RE.match(parsed.path)
        if match:
            session = self._session()
            if not session:
                self._send_json(401, {'error': 'unauthorized'})
                return
            group_id = int(match.group(1))
            module_key = match.group(2)
            try:
                result = asyncio.run(_require_group_payload(session, group_id, module_key=module_key))
            except KeyError:
                self._send_json(404, {'error': 'not_found'})
                return
            if result.get('error'):
                self._send_json(403, result)
                return
            self._send_json(200, result['payload'])
            return
        self._send_text(404, 'not found')

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == '/api/web/auth/telegram':
            try:
                payload = self._read_json()
            except Exception:
                self._send_json(400, {'error': 'bad_request'})
                return
            if not verify_telegram_login(payload):
                self._send_json(401, {'error': 'invalid_telegram_login'})
                return
            token = issue_session(payload)
            self._send_json(200, {'ok': True}, cookie_header=cookie_header_for_session(token))
            return
        if parsed.path == '/api/web/auth/request-login':
            try:
                payload = self._read_json()
            except Exception:
                self._send_json(400, {'error': 'bad_request'})
                return
            request = create_web_login_request(
                requested_group_id=payload.get('requested_group_id'),
                origin=payload.get('origin'),
            )
            self._send_json(200, {
                'ok': True,
                'request_id': request['request_id'],
                'browser_token': request['browser_token'],
                'expires_at': request['expires_at'],
                'poll_interval_ms': web_login_settings()['poll_interval_ms'],
            })
            return
        if parsed.path == '/api/web/auth/poll-login':
            try:
                payload = self._read_json()
            except Exception:
                self._send_json(400, {'error': 'bad_request'})
                return
            result = consume_web_login_request(
                str(payload.get('request_id') or ''),
                str(payload.get('browser_token') or ''),
            )
            status = str(result.get('status') or 'pending')
            if status == 'approved':
                token = issue_session(result['user'])
                self._send_json(
                    200,
                    {
                        'ok': True,
                        'status': 'approved',
                        'requested_group_id': result.get('requested_group_id'),
                    },
                    cookie_header=cookie_header_for_session(token),
                )
                return
            if status == 'forbidden':
                self._send_json(403, {'error': 'forbidden'})
                return
            self._send_json(200, result)
            return
        if parsed.path == '/api/web/auth/local-debug':
            try:
                payload = self._read_json()
            except Exception:
                self._send_json(400, {'error': 'bad_request'})
                return
            if not self._allow_local_debug_login():
                self._send_json(403, {'error': 'forbidden'})
                return
            if not is_authorized_local_debug_secret(payload.get('secret')):
                self._send_json(401, {'error': 'invalid_local_debug_secret'})
                return
            token = issue_session(build_local_debug_login(SUPER_ADMIN_ID))
            self._send_json(200, {'ok': True, 'mode': 'local_debug'}, cookie_header=cookie_header_for_session(token))
            return
        if parsed.path == '/api/web/auth/logout':
            self._send_json(200, {'ok': True}, cookie_header=cookie_header_for_logout())
            return
        if parsed.path == '/api/web/render-preview':
            try:
                payload = self._read_json()
                rendered = render_preview(payload.get('message') or {}, payload.get('preview_context') or {})
            except ValueError as exc:
                self._send_json(400, {'error': 'bad_request', 'detail': str(exc)})
                return
            except Exception:
                self._send_json(400, {'error': 'bad_request'})
                return
            self._send_json(200, rendered)
            return
        match = GROUP_MODULE_RE.match(parsed.path)
        if match:
            session = self._session()
            if not session:
                self._send_json(401, {'error': 'unauthorized'})
                return
            try:
                payload = self._read_json()
            except Exception:
                self._send_json(400, {'error': 'bad_request'})
                return
            group_id = int(match.group(1))
            module_key = match.group(2)
            try:
                result = asyncio.run(_require_group_payload(session, group_id, module_key=module_key, save_payload=payload))
            except KeyError:
                self._send_json(404, {'error': 'not_found'})
                return
            except ValueError as exc:
                self._send_json(400, {'error': 'bad_request', 'detail': str(exc)})
                return
            if result.get('error'):
                self._send_json(403, result)
                return
            self._send_json(200, result['payload'])
            return
        self._send_text(404, 'not found')
