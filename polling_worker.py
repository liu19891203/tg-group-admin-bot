import asyncio

from bot.utils.process_lock import ProcessLockError
from local_polling import run_polling


async def main():
    await run_polling(restore_webhook_on_exit=False, owner="polling_worker")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except ProcessLockError as exc:
        print(str(exc), flush=True)
    except KeyboardInterrupt:
        pass
