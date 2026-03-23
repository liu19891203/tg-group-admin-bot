import atexit
import json
import os
from pathlib import Path


class ProcessLockError(RuntimeError):
    pass


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def acquire_process_lock(lock_name: str = ".bot_instance.lock", owner: str = "bot"):
    lock_path = Path(lock_name).resolve()
    payload = {"pid": os.getpid(), "owner": owner}

    while True:
        try:
            with lock_path.open("x", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False)
            break
        except FileExistsError:
            try:
                current = json.loads(lock_path.read_text(encoding="utf-8") or "{}")
            except Exception:
                current = {}
            existing_pid = int(current.get("pid") or 0)
            existing_owner = current.get("owner") or "unknown"
            if _pid_exists(existing_pid):
                raise ProcessLockError(f"Another local bot instance is already running: pid={existing_pid} owner={existing_owner}")
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass

    def _cleanup():
        try:
            current = json.loads(lock_path.read_text(encoding="utf-8") or "{}")
        except Exception:
            current = {}
        if int(current.get("pid") or 0) == os.getpid():
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass

    atexit.register(_cleanup)
    return lock_path
