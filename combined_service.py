import asyncio
import os
import threading
from http.server import HTTPServer

from api.web import handler as web_handler
from bot.storage.kv import KV_ENABLED, LOCAL_KV_PATH
from local_polling import main as polling_main


def _run_web_server() -> None:
    port = int(os.environ.get("PORT", "8000"))
    if not KV_ENABLED:
        print(
            f"WARNING: KV is not configured; falling back to local storage at {LOCAL_KV_PATH}. "
            "Web admin and polling worker will not share config without KV_REST_API_URL and KV_REST_API_TOKEN.",
            flush=True,
        )
    server = HTTPServer(("0.0.0.0", port), web_handler)
    print(f"Combined web listening on http://0.0.0.0:{port}", flush=True)
    server.serve_forever()


def main() -> None:
    web_thread = threading.Thread(target=_run_web_server, name="web-server", daemon=True)
    web_thread.start()
    asyncio.run(polling_main())


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
