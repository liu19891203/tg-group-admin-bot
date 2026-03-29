import logging

from telegram.constants import ChatType

from ..storage.config_store import upsert_known_group
from ..services.verify import start_verification_on_join, ensure_user_verified
from ..services.welcome import send_welcome, process_welcome_queue
from ..services.auto_ban import handle_auto_ban
from ..services.auto_delete import handle_auto_delete
from ..services.auto_mute import handle_auto_mute
from ..services.auto_warn import handle_auto_warn
from ..services.anti_spam import handle_anti_spam
from ..services.auto_reply import handle_auto_reply
from ..services.verified_user import handle_verified_user_reply
from ..services.extra_features import (
    handle_ad_filter,
    handle_group_commands,
    handle_invite_join_request,
    handle_language_whitelist,
    handle_new_member_features,
    handle_nsfw_filter,
    handle_related_channel_message,
    process_scheduled_messages,
    record_message_metrics,
    track_member_profile,
)
from ..utils.telegram import is_admin

logger = logging.getLogger(__name__)


def _is_group(chat):
    return chat and chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)


async def on_new_members(update, context):
    chat = update.effective_chat
    if not _is_group(chat):
        return
    upsert_known_group(chat)
    await process_welcome_queue(context, chat.id)
    await process_scheduled_messages(context, chat.id)

    message = update.effective_message
    if not message or not message.new_chat_members:
        return
    for member in message.new_chat_members:
        if member.is_bot:
            continue
        await handle_new_member_features(context, chat, member, message)
        verified = await start_verification_on_join(context, chat, member)
        if verified:
            await send_welcome(context, chat, member)


async def on_group_message(update, context):
    chat = update.effective_chat
    if not _is_group(chat):
        return
    upsert_known_group(chat)
    await process_welcome_queue(context, chat.id)
    await process_scheduled_messages(context, chat.id)

    message = update.effective_message
    if not message:
        return
    if await handle_related_channel_message(context, message, chat):
        return
    if message.new_chat_members or message.left_chat_member:
        return

    if message.sender_chat and not message.from_user:
        await handle_ad_filter(context, message, None, chat, False)
        return

    if not message.from_user or message.from_user.is_bot:
        return

    user = message.from_user
    is_admin_user = await is_admin(context, chat.id, user.id)
    await track_member_profile(context, chat, user)

    if await handle_ad_filter(context, message, user, chat, is_admin_user):
        return

    if not is_admin_user:
        verified = await ensure_user_verified(context, chat, user)
        if not verified:
            return

    if await handle_group_commands(context, message, user, chat, is_admin_user):
        return
    if await handle_language_whitelist(context, message, chat, is_admin_user):
        return
    if await handle_nsfw_filter(context, message, chat, is_admin_user):
        return

    if not is_admin_user:
        if await handle_auto_ban(context, message, user, chat):
            return
        if await handle_auto_delete(context, message, user, chat):
            return
        if await handle_auto_mute(context, message, user, chat):
            return
        if await handle_auto_warn(context, message, user, chat):
            return
        if await handle_anti_spam(context, message, user, chat):
            return

    await record_message_metrics(message, user, chat)

    if await handle_verified_user_reply(context, message, user, chat):
        return

    if not is_admin_user:
        await handle_auto_reply(context, message, user, chat)


async def on_chat_join_request(update, context):
    join_request = getattr(update, "chat_join_request", None)
    if not join_request:
        return
    chat = getattr(join_request, "chat", None)
    if not _is_group(chat):
        return
    upsert_known_group(chat)
    await handle_invite_join_request(context, chat, join_request)


async def on_my_chat_member(update, context):
    chat = update.effective_chat
    if not _is_group(chat):
        return
    upsert_known_group(chat)
    # 保持静默
