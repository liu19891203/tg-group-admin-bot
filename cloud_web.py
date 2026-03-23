from urllib.parse import urlparse

from api.web import handler as AdminWebHandler
from bot.storage.kv import KV_ENABLED


def build_health_payload() -> dict:
    return {
        "ok": True,
        "service": "admin-web",
        "kv_enabled": KV_ENABLED,
    }


class handler(AdminWebHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path in {"/healthz", "/api/web/health"}:
            self._send_json(200, build_health_payload())
            return
        super().do_GET()
