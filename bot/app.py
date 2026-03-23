import logging
import json

from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ChatJoinRequestHandler,
    ChatMemberHandler,
    filters,
)

from .models.config import BOT_TOKEN
from .handlers.admin import admin_start, admin_message, admin_photo
from .handlers.callbacks import callback_router
from .handlers.group import on_chat_join_request, on_new_members, on_group_message, on_my_chat_member

logger = logging.getLogger(__name__)


def build_app():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is required")
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", admin_start))
    app.add_handler(CallbackQueryHandler(callback_router))

    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND & ~filters.FORWARDED, admin_message))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.FORWARDED, admin_message))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.PHOTO, admin_photo))

    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, on_new_members))
    app.add_handler(MessageHandler(filters.ChatType.GROUPS, on_group_message))
    app.add_handler(ChatJoinRequestHandler(on_chat_join_request))
    app.add_handler(ChatMemberHandler(on_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))

    async def on_error(update, context):
        logger.exception("handler_error")

    app.add_error_handler(on_error)
    return app
