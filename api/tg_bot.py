import asyncio
import json
import logging
import time
from http.server import BaseHTTPRequestHandler

from telegram import Update

from bot.app import build_app
from bot.models.config import WEBHOOK_SECRET
from bot.services.extra_features import sweep_known_group_maintenance

logging.basicConfig(level=logging.INFO)

logger = logging.getLogger(__name__)


def is_authorized_webhook_secret(provided_secret: str | None) -> bool:
    if not WEBHOOK_SECRET:
        return True
    value = str(provided_secret or "").strip()
    return bool(value) and value == WEBHOOK_SECRET


async def process_update(data: dict):
    print("process_update_enter", flush=True)
    app = build_app()
    await app.initialize()
    try:
        update = Update.de_json(data, app.bot)
        print("update_dejson_ok", flush=True)
        msg_text = None
        try:
            if update.effective_message:
                msg_text = update.effective_message.text or update.effective_message.caption
        except Exception:
            msg_text = None
        logger.info(
            "update_summary chat_type=%s chat_id=%s user_id=%s text=%s",
            getattr(update.effective_chat, "type", None),
            getattr(update.effective_chat, "id", None),
            getattr(update.effective_user, "id", None),
            msg_text,
        )
        print(
            "update_summary",
            getattr(update.effective_chat, "type", None),
            getattr(update.effective_chat, "id", None),
            getattr(update.effective_user, "id", None),
            msg_text,
            flush=True,
        )
        await app.process_update(update)
        await sweep_known_group_maintenance(app, sweep_tick=int(time.time()))
        print("process_update_done", flush=True)
    finally:
        await app.shutdown()
        print("process_update_shutdown", flush=True)


class handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: str = "ok"):
        self.send_response(status)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def do_GET(self):
        self._send(200, "ok")

    def do_POST(self):
        secret = self.headers.get("X-Telegram-Bot-Api-Secret-Token")
        if not is_authorized_webhook_secret(secret):
            self._send(401, "unauthorized")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            data = json.loads(raw.decode("utf-8") or "{}")
            logger.info("payload_keys: %s", list(data.keys()))
            print("payload_keys", list(data.keys()), flush=True)
        except Exception:
            self._send(400, "bad request")
            return
        try:
            asyncio.run(process_update(data))
        except Exception as exc:
            logger.exception("handler_error")
            print("handler_error", repr(exc), flush=True)
            self._send(500, "handler_error")
            return
        self._send(200, "ok")


def log_startup_state():
    logger.info("webhook_ready")