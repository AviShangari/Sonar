"""The terminal chat. Start it with:  uv run python -m sonar.assistant

Read-only: it opens the database in read-only mode and the assistant has only its six lookup tools.
It never starts a run. Each question calls the model (OpenRouter, `assistant_model`), which costs a little."""

import asyncio
import re
import shutil
import sys
import textwrap
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from sqlalchemy import select
from sqlalchemy.orm import Session

from sonar import budget, llm
from sonar.agent_runner import AgentError
from sonar.assistant.agent import THINKING, Assistant
from sonar.assistant.safe import for_terminal
from sonar.assistant.tools import AssistantTools, read_only_engine
from sonar.config import load_settings
from sonar.db.tables import ListingRow
from sonar.sites import site_accounts

HELP = "Ask about matches, closing dates, why something matched, or the last run."
COMMANDS = "/reset  start a new conversation     /help  show this     /quit  leave"
BULLET = re.compile(r"^(\s*(?:-|\d+[.)])\s+)")
ITEM = re.compile(r"^(?:-|\d+[.)])\s+")  # a list item at the left margin (the first line of one posting)


def format_answer(text: str, width: int) -> str:
    """The model's answer as shown: no control characters, no leftover markdown marks, bullets as dashes,
    lines wrapped to the terminal (a long link is never split)."""
    text = for_terminal(text)
    text = re.sub(r"\*\*|__|`", "", text)  # bold and code marks
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", text)  # headings
    text = re.sub(r"(?m)^(\s*)[*•]\s+", r"\1- ", text)  # bullets
    lines: list[str] = []
    previous = "blank"
    for raw in text.split("\n"):
        line = raw.rstrip()
        kind = "blank" if not line.strip() else "item" if ITEM.match(line) else "detail" if line.startswith("  ") else "text"
        if kind in ("item", "text") and previous in ("item", "detail"):
            lines.append("")  # a blank line between list items, and between a list and what follows it
        lines.append(wrap_line(line, width))
        previous = kind
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def wrap_line(line: str, width: int) -> str:
    """Wrap one line to the width; wrapped lines hang under the text of a list item. A line that is one
    long word (a link) is left alone."""
    if len(line) <= width or " " not in line.strip():
        return line
    bullet = BULLET.match(line)
    hang = " " * len(bullet.group(1)) if bullet else " " * (len(line) - len(line.lstrip()))
    return textwrap.fill(line, width, subsequent_indent=hang, break_long_words=False, break_on_hyphens=False)


def terminal_width() -> int:
    return max(40, min(shutil.get_terminal_size((100, 24)).columns - 4, 100))


class Status:
    """What the assistant is doing right now; the animated line reads it."""

    def __init__(self) -> None:
        self.label = THINKING


@contextmanager
def thinking(status: Status) -> Iterator[None]:
    """An animated "Thinking… 7s" line while the assistant works. Only on a real terminal: piped output and
    tests get nothing. The line is erased when the answer is ready."""
    if not sys.stdout.isatty():
        yield
        return
    stop, began = threading.Event(), time.monotonic()

    def spin() -> None:
        frames, n, longest = "|/-\\", 0, 0
        while not stop.wait(0.15):
            line = f"  {frames[n % 4]} {status.label}… {int(time.monotonic() - began)}s"
            sys.stdout.write("\r" + line.ljust(longest))  # pad: a shorter label must wipe the tail of a longer one
            sys.stdout.flush()
            longest, n = max(longest, len(line)), n + 1

    worker = threading.Thread(target=spin, daemon=True)
    worker.start()
    try:
        yield
    finally:
        stop.set()
        worker.join()
        sys.stdout.write("\r" + " " * 70 + "\r")  # erase the line
        sys.stdout.flush()


def repl(assistant: Assistant, read: Callable[[str], str] = input, write: Callable[[str], None] = print) -> None:
    session_start, questions = budget.current.reported_cost_usd, 0
    status = Status()
    assistant.on_step = lambda label: setattr(status, "label", label)

    def session_cost() -> None:
        """Shown only when a conversation ends (/quit, Ctrl-C, /reset), never after each answer."""
        nonlocal session_start, questions
        if questions:
            spent = budget.current.reported_cost_usd - session_start
            write(f"Session cost: ${spent:.4f} ({questions} question{'s' if questions != 1 else ''})")
        session_start, questions = budget.current.reported_cost_usd, 0

    write(f"\nSonar assistant  ·  {llm.model_for('assistant')}\n{HELP}\n{COMMANDS}\n{'─' * 60}")
    while True:
        try:
            line = read("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            write("")
            session_cost()
            return
        if not line:
            continue
        if line in ("/quit", "/exit"):
            session_cost()
            return
        if line == "/reset":
            assistant.reset()
            session_cost()
            write("Started a new conversation.")
            continue
        if line == "/help":
            write(f"{HELP}\n{COMMANDS}")
            continue
        try:
            with thinking(status):
                answer = asyncio.run(assistant.ask(line))
        except AgentError as exc:
            write(f"\nSorry, I couldn't answer that: {for_terminal(str(exc))}")
            continue
        except KeyboardInterrupt:
            write("")
            session_cost()
            return
        questions += 1
        write(f"\nSonar:\n{format_answer(answer, terminal_width())}")


def main() -> int:
    settings = load_settings()
    try:
        llm.configure(settings)  # stops here, with a clear message, if OPENROUTER_API_KEY is missing
        engine = read_only_engine(settings.database_url)
    except (RuntimeError, ValueError, FileNotFoundError) as exc:
        print(exc, file=sys.stderr)
        return 1
    with Session(engine) as session:
        sites = list(session.scalars(select(ListingRow.site).distinct().order_by(ListingRow.site)))
    tools = AssistantTools(engine, settings, site_accounts())
    repl(Assistant(tools, sites))
    return 0
