"""Shared helpers for newsletter WAT tools.

This is the only place that knows about:
  - the project root layout (.env, .tmp/runs/<ts>/)
  - logging format
  - the simple retry helper used by HTTP-touching tools
"""

from __future__ import annotations

import functools
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, TypeVar

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUNS_ROOT = PROJECT_ROOT / ".tmp" / "runs"

T = TypeVar("T")


def load_env() -> None:
    """Load .env from project root. Safe to call multiple times."""
    load_dotenv(PROJECT_ROOT / ".env")


def require_env(name: str) -> str:
    """Return an environment variable or exit with a clear message."""
    value = os.environ.get(name, "").strip()
    if not value:
        sys.stderr.write(
            f"[fatal] Required environment variable {name!r} is missing.\n"
            f"        Set it in {PROJECT_ROOT / '.env'} (see .env.example).\n"
        )
        sys.exit(2)
    return value


def setup_logging(name: str) -> logging.Logger:
    """Configure root + return a named child logger.

    Idempotent: repeated calls do not stack handlers.
    """
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-5s %(name)s | %(message)s",
                              datefmt="%H:%M:%S")
        )
        root.addHandler(handler)
        root.setLevel(logging.INFO)
    return logging.getLogger(name)


def new_run_dir() -> Path:
    """Create and return a fresh timestamped run directory."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = RUNS_ROOT / ts
    path.mkdir(parents=True, exist_ok=True)
    (path / "images").mkdir(exist_ok=True)
    return path


def resolve_run_dir(value: str | None) -> Path:
    """Resolve a run dir from CLI input.

    Accepts:
      - None        -> create a fresh run dir
      - "<ts>"      -> .tmp/runs/<ts>
      - absolute or relative path -> as-is
    """
    if value is None:
        return new_run_dir()
    p = Path(value)
    if not p.is_absolute() and not p.exists():
        # Treat as a timestamp shorthand.
        candidate = RUNS_ROOT / value
        if candidate.exists():
            return candidate
    p.mkdir(parents=True, exist_ok=True)
    (p / "images").mkdir(exist_ok=True)
    return p


def retry(
    *,
    attempts: int = 3,
    base_delay: float = 1.0,
    exceptions: tuple[type[BaseException], ...] = (Exception,),
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Lightweight exponential-backoff retry decorator.

    Used for transient HTTP/extraction failures. The final attempt re-raises.
    """

    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        log = logging.getLogger(fn.__module__)

        @functools.wraps(fn)
        def wrapper(*args, **kwargs) -> T:
            last: BaseException | None = None
            for attempt in range(1, attempts + 1):
                try:
                    return fn(*args, **kwargs)
                except exceptions as exc:
                    last = exc
                    if attempt == attempts:
                        raise
                    sleep_for = base_delay * (2 ** (attempt - 1))
                    log.warning(
                        "%s failed (attempt %d/%d): %s — retrying in %.1fs",
                        fn.__name__, attempt, attempts, exc, sleep_for,
                    )
                    time.sleep(sleep_for)
            assert last is not None  # pragma: no cover
            raise last

        return wrapper

    return decorator
