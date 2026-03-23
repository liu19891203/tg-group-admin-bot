import os
from pathlib import Path

_ENV_LOADED = False


def _parse_line(line: str):
    text = line.strip()
    if not text or text.startswith("#"):
        return None, None
    if text.startswith("export "):
        text = text[len("export "):].lstrip()
    if "=" not in text:
        return None, None
    key, value = text.split("=", 1)
    key = key.strip()
    value = value.strip()
    if not key:
        return None, None
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return key, value


def load_local_env():
    global _ENV_LOADED
    if _ENV_LOADED:
        return

    project_root = Path(__file__).resolve().parents[2]
    env_candidates = []

    custom_env = os.environ.get("BOT_ENV_FILE", "").strip()
    if custom_env:
        env_candidates.append(Path(custom_env))

    env_candidates.extend(
        [
            project_root / ".env.local",
            project_root / ".env",
        ]
    )

    for env_file in env_candidates:
        if not env_file.is_file():
            continue
        for raw_line in env_file.read_text(encoding="utf-8-sig").splitlines():
            key, value = _parse_line(raw_line)
            if not key or key in os.environ:
                continue
            os.environ[key] = value
        break

    _ENV_LOADED = True
