from __future__ import annotations

import sys
from importlib.machinery import SourcelessFileLoader
from pathlib import Path


_MIN_RECOVERABLE_PYC_SIZE = 1024
_SENTINEL_KEY = "_pyc_recovery_in_progress"


def exec_from_matching_pyc(module_globals: dict) -> None:
    if module_globals.get(_SENTINEL_KEY):
        raise ImportError(f"Recursive pyc recovery detected for {module_globals['__name__']}")

    source_file = Path(module_globals["__file__"])
    pycache_dir = source_file.with_name("__pycache__")
    stem = source_file.stem
    cache_tag = getattr(sys.implementation, "cache_tag", "")

    candidates: list[Path] = []
    if cache_tag:
        candidates.append(pycache_dir / f"{stem}.{cache_tag}.pyc")
    candidates.extend(sorted(pycache_dir.glob(f"{stem}.*.pyc")))

    seen: set[Path] = set()
    module_globals[_SENTINEL_KEY] = True
    try:
        for pyc_path in candidates:
            if pyc_path in seen or not pyc_path.exists():
                continue
            seen.add(pyc_path)
            if pyc_path.stat().st_size < _MIN_RECOVERABLE_PYC_SIZE:
                continue
            loader = SourcelessFileLoader(module_globals["__name__"], str(pyc_path))
            code = loader.get_code(module_globals["__name__"])
            if code is None:
                continue
            exec(code, module_globals)
            return
    finally:
        module_globals.pop(_SENTINEL_KEY, None)

    searched = ", ".join(str(path) for path in candidates) or str(pycache_dir / f"{stem}.*.pyc")
    raise ImportError(f"Unable to recover {module_globals['__name__']} from pyc. Looked for: {searched}")
