import os
from http.server import ThreadingHTTPServer

from bot.app import build_app
from bot.storage.kv import KV_ENABLED, LOCAL_KV_PATH
from cloud_web import handler


def main():
    port = int(os.environ.get("PORT", "8000"))
    build_app()
    if not KV_ENABLED:
        print(
            f"WARNING: KV is not configured; falling back to local storage at {LOCAL_KV_PATH}. "
            "Deployed admin-web and bot-worker will not share config without KV_REST_API_URL and KV_REST_API_TOKEN.",
            flush=True,
        )
    server = ThreadingHTTPServer(("0.0.0.0", port), handler)
    print(f"Admin web listening on http://0.0.0.0:{port} (health: /healthz)", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
