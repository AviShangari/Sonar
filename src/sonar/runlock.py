"""A named lock so two runs cannot overlap.

It takes an exclusive operating-system lock on a file. The OS gives it to one process at a time and
releases it when the process ends for any reason (crash, kill, power loss), so a dead run can never
leave a stuck lock. The name lets us lock "run" now and per-site names later.
"""

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import IO

if sys.platform == "win32":
    import msvcrt

    def _try_lock(handle: IO[str]) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)  # raises OSError if another process holds it

    def _unlock(handle: IO[str]) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _try_lock(handle: IO[str]) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # raises OSError if another process holds it

    def _unlock(handle: IO[str]) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class RunLocked(RuntimeError):
    """Another run holds this lock."""


@contextmanager
def run_lock(name: str, directory: Path) -> Iterator[None]:
    """Hold the lock `name` while the block runs. Raises RunLocked at once (no waiting) if it is taken."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.lock"
    handle = open(path, "a+", encoding="utf-8")  # the file stays; only the OS lock on it matters
    try:
        try:
            _try_lock(handle)
        except OSError as exc:
            raise RunLocked(f"another run holds the '{name}' lock ({path})") from exc
        try:
            yield
        finally:
            _unlock(handle)
    finally:
        handle.close()
