"""Files the assistant made while answering one question (PDFs), collected so the place that shows the answer can
deliver them: the terminal prints the path, Teams offers the file. The model never decides how a file is delivered
and never sees a path. Each question has its own list (a context variable), so two chats answered at the same
time cannot receive each other's files."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

MAX_FILES_PER_QUESTION = 3

_files: ContextVar[list[Path] | None] = ContextVar("sonar_assistant_outbox", default=None)


@contextmanager
def collecting() -> Iterator[list[Path]]:
    """Everything added while inside this block, for the question being answered."""
    files: list[Path] = []
    token = _files.set(files)
    try:
        yield files
    finally:
        _files.reset(token)


def full() -> bool:
    files = _files.get()
    return files is not None and len(files) >= MAX_FILES_PER_QUESTION


def add(path: Path) -> None:
    files = _files.get()
    if files is not None and path not in files:
        files.append(path)
