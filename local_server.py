import os
from http.server import HTTPServer

from api.tg_bot import handler, log_startup_state
from bot.app import build_app
from bot.utils.process_lock import ProcessLockError, acquire_process_lock


def main():
    port = int(os.environ.get("PORT", "8000"))
    lock_path = acquire_process_lock(owner="local_server")
    build_app()
    log_startup_state()
    server = HTTPServer(("0.0.0.0", port), handler)
    print(f"Local bot lock acquired: {lock_path}", flush=True)
    print(f"Local webhook server listening on http://127.0.0.1:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    try:
        main()
    except ProcessLockError as exc:
        print(str(exc), flush=True)
