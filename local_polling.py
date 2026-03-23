import asyncio
import os

from telegram import Update

from bot.app import build_app
from bot.models.config import BOT_TOKEN, WEBHOOK_SECRET
from bot.services.extra_features import scheduled_message_worker
from bot.storage.kv import KV_ENABLED, LOCAL_KV_PATH
from bot.utils.process_lock import ProcessLockError, acquire_process_lock


async def _restore_webhook(app, webhook_url: str):
    if not webhook_url:
        return
    kwargs = {
        "url": webhook_url,
        "drop_pending_updates": False,
        "allowed_updates": Update.ALL_TYPES,
    }
    if WEBHOOK_SECRET:
        kwargs["secret_token"] = WEBHOOK_SECRET
    await app.bot.set_webhook(**kwargs)


async def run_polling(
    *,
    restore_webhook_on_exit: bool,
    owner: str,
    timeout_sec: int | None = None,
):
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is required")
    if not KV_ENABLED:
        print(
            f"WARNING: KV is not configured; falling back to local storage at {LOCAL_KV_PATH}. "
            "Deployed admin-web and bot-worker will not share config without KV_REST_API_URL and KV_REST_API_TOKEN.",
            flush=True,
        )

    lock_path = acquire_process_lock(owner=owner)
    print(f"Bot lock acquired ({owner}): {lock_path}", flush=True)

    app = build_app()
    await app.initialize()

    webhook = await app.bot.get_webhook_info()
    webhook_url = webhook.url or ""
    wait_timeout = timeout_sec
    if wait_timeout is None:
        wait_timeout = max(0, int(os.environ.get("POLL_TIMEOUT_SEC", "0") or 0))

    if webhook_url:
        print(f"Disabling webhook before polling: {webhook_url}", flush=True)
        await app.bot.delete_webhook(drop_pending_updates=False)

    worker_task = None
    try:
        await app.start()
        worker_task = asyncio.create_task(scheduled_message_worker(app), name="scheduled-message-worker")
        await app.updater.start_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=False)
        print(f"Polling started ({owner}).", flush=True)
        if wait_timeout > 0:
            print(f"Polling timeout: {wait_timeout}s", flush=True)
            await asyncio.sleep(wait_timeout)
        else:
            await asyncio.Event().wait()
    finally:
        if app.updater:
            try:
                await app.updater.stop()
            except RuntimeError:
                pass
        if worker_task:
            worker_task.cancel()
            try:
                await worker_task
            except asyncio.CancelledError:
                pass
        try:
            await app.stop()
        except RuntimeError:
            pass
        if restore_webhook_on_exit:
            await _restore_webhook(app, webhook_url)
        elif webhook_url:
            print("Skipping webhook restore for persistent polling runtime.", flush=True)
        await app.shutdown()


async def main():
    await run_polling(restore_webhook_on_exit=True, owner="local_polling")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except ProcessLockError as exc:
        print(str(exc), flush=True)
    except KeyboardInterrupt:
        pass
