import html
import re
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

from ..utils.telegram import safe_answer, safe_edit_message
from .login_flow import approve_web_login_request, get_web_login_request

WEB_LOGIN_START_RE = re.compile(r'^weblogin_([a-f0-9]{16})$')
WEB_LOGIN_CALLBACK_RE = re.compile(r'^weblogin:confirm:([a-f0-9]{16})$')


def parse_web_login_start_arg(value: str | None) -> str | None:
    raw = str(value or '').strip()
    match = WEB_LOGIN_START_RE.fullmatch(raw)
    return match.group(1) if match else None


def _confirm_markup(request_id: str):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('\u786e\u8ba4\u767b\u5f55\u540e\u53f0', callback_data=f'weblogin:confirm:{request_id}')],
    ])


def _request_origin_text(request: dict) -> str:
    origin = str((request or {}).get('origin') or '').strip()
    return origin or '\u5f53\u524d\u7ba1\u7406\u540e\u53f0'


async def show_web_login_prompt(update, context, request_id: str) -> bool:
    del context
    request = get_web_login_request(request_id)
    if not request:
        if update.effective_message:
            await update.effective_message.reply_text('\u8be5\u7f51\u9875\u767b\u5f55\u8bf7\u6c42\u5df2\u8fc7\u671f\uff0c\u8bf7\u56de\u5230\u6d4f\u89c8\u5668\u91cd\u65b0\u53d1\u8d77\u767b\u5f55\u3002')
        return True
    status = str(request.get('status') or 'pending')
    origin_text = _request_origin_text(request)
    if status == 'approved':
        text = '\n'.join([
            '\u8be5\u7f51\u9875\u767b\u5f55\u8bf7\u6c42\u5df2\u7ecf\u786e\u8ba4\u3002',
            f'\u540e\u53f0\u5730\u5740: {origin_text}',
            '\u8bf7\u8fd4\u56de\u6d4f\u89c8\u5668\u7b49\u5f85\u81ea\u52a8\u767b\u5f55\u3002',
        ])
        if update.effective_message:
            await update.effective_message.reply_text(text)
        return True
    expires_at = int(request.get('expires_at') or 0)
    ttl_sec = max(0, expires_at - int(time.time()))
    text = '\n'.join([
        '\u7f51\u9875\u540e\u53f0\u767b\u5f55\u786e\u8ba4',
        f'\u540e\u53f0\u5730\u5740: {origin_text}',
        '\u70b9\u51fb\u4e0b\u65b9\u6309\u94ae\u540e\uff0c\u6d4f\u89c8\u5668\u4f1a\u81ea\u52a8\u767b\u5f55\u5f53\u524d Telegram \u8d26\u53f7\u3002',
        f'\u8bf7\u6c42\u5269\u4f59\u65f6\u95f4: {ttl_sec} \u79d2',
    ])
    if update.effective_message:
        await update.effective_message.reply_text(text, reply_markup=_confirm_markup(request_id))
    return True


async def handle_web_login_callback(update, context) -> bool:
    del context
    query = update.callback_query
    data = str(getattr(query, 'data', '') or '')
    match = WEB_LOGIN_CALLBACK_RE.fullmatch(data)
    if not match:
        return False
    request_id = match.group(1)
    ok, reason, request = approve_web_login_request(request_id, query.from_user)
    if not ok:
        if reason == 'not_found':
            await safe_answer(query, '\u767b\u5f55\u8bf7\u6c42\u5df2\u8fc7\u671f\uff0c\u8bf7\u56de\u5230\u6d4f\u89c8\u5668\u91cd\u65b0\u53d1\u8d77\u3002', show_alert=True)
            await safe_edit_message(query, '\u8be5\u7f51\u9875\u767b\u5f55\u8bf7\u6c42\u5df2\u8fc7\u671f\uff0c\u8bf7\u56de\u5230\u6d4f\u89c8\u5668\u91cd\u65b0\u53d1\u8d77\u767b\u5f55\u3002', reply_markup=None)
            return True
        await safe_answer(query, '\u767b\u5f55\u8bf7\u6c42\u65e0\u6548\uff0c\u8bf7\u91cd\u65b0\u53d1\u8d77\u3002', show_alert=True)
        return True
    origin_text = _request_origin_text(request or {})
    account_name = html.escape(query.from_user.first_name or query.from_user.full_name or str(query.from_user.id))
    text = '\n'.join([
        '\u767b\u5f55\u786e\u8ba4\u5b8c\u6210',
        f'\u5f53\u524d\u8d26\u53f7: {account_name}',
        f'\u540e\u53f0\u5730\u5740: {origin_text}',
        '\u8bf7\u8fd4\u56de\u6d4f\u89c8\u5668\uff0c\u9875\u9762\u4f1a\u81ea\u52a8\u8fdb\u5165\u540e\u53f0\u3002',
    ])
    if reason == 'already_approved':
        await safe_answer(query, '\u8be5\u8bf7\u6c42\u5df2\u7ecf\u786e\u8ba4\u8fc7\u4e86\u3002', show_alert=False)
    else:
        await safe_answer(query, '\u5df2\u786e\u8ba4\u767b\u5f55\uff0c\u8bf7\u8fd4\u56de\u6d4f\u89c8\u5668\u3002', show_alert=False)
    await safe_edit_message(query, text, reply_markup=None, parse_mode=ParseMode.HTML)
    return True
