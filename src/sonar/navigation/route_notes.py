"""Route notes: after a clean run, the agent's one or two sentences on how it reached the list.

They are an early warning for people, not memory for the agent. A note is written from page
content, so it is untrusted: it is cleaned and capped here, saved to a gitignored file, shown to a
human, and NEVER put into a prompt or applied to sites.yaml by code. The agent still rediscovers
the route from scratch every run.

File: <route_notes_dir>/<site>.txt, one line per CHANGE of route: "<date> | <note>". The last line is the
current note. A run that finds the same route as before writes nothing.
"""

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

MAX_NOTE_CHARS = 300
_URL_RE = re.compile(r"https?://\S+")  # addresses can carry session tokens, so they never reach the file
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


@dataclass
class NoteResult:
    status: str  # "first" | "unchanged" | "changed" | "skipped"
    note: str = ""
    previous: str | None = None
    previous_date: str | None = None

    def describe(self) -> str | None:
        """A line for the run summary, or None when there is nothing to tell."""
        if self.status == "first":
            return f"route note saved: {self.note}"
        if self.status == "changed":
            return f"route changed since {self.previous_date}: was \"{self.previous}\", now \"{self.note}\""
        return None


def clean_note(raw: str | None) -> str:
    """One short plain-text line: no control characters, no web addresses, capped. Empty if nothing usable."""
    text = _CONTROL_RE.sub(" ", raw or "")
    text = _URL_RE.sub("[address removed]", text)
    text = " ".join(text.split())
    if len(text) > MAX_NOTE_CHARS:
        text = text[:MAX_NOTE_CHARS].rsplit(" ", 1)[0].rstrip(" ,;:") + "..."
    return text


def _same(a: str, b: str) -> bool:
    return " ".join(a.casefold().split()) == " ".join(b.casefold().split())


def _last_entry(path: Path) -> tuple[str, str] | None:
    if not path.exists():
        return None
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if " | " in line]
    if not lines:
        return None
    day, _, note = lines[-1].partition(" | ")
    return day, note


def save_route_note(directory: Path, site: str, raw_note: str | None, today: date) -> NoteResult:
    """Save the note if it is new or changed. Never raises: a note is a courtesy, not part of the run."""
    note = clean_note(raw_note)
    if not note:
        return NoteResult("skipped")
    try:
        safe_site = re.sub(r"[^A-Za-z0-9._-]+", "_", site)
        path = directory / f"{safe_site}.txt"
        last = _last_entry(path)
        if last and _same(last[1], note):
            return NoteResult("unchanged", note, last[1], last[0])
        directory.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{today.isoformat()} | {note}\n")
        if last is None:
            return NoteResult("first", note)
        return NoteResult("changed", note, last[1], last[0])
    except OSError:
        return NoteResult("skipped")
