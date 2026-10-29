"""Cleaning for text from postings. Titles, summaries, snippets and run notes all come from web pages,
so they are untrusted data: they may hold instructions meant for a model, terminal escape codes or
invisible characters. Everything the model is shown or the terminal prints goes through here."""

import json
import unicodedata
from typing import Any

OPEN_TAG = "<untrusted_data>"
CLOSE_TAG = "</untrusted_data>"
MAX_RESULT_CHARS = 12_000  # one tool result


def clean(value: object, limit: int = 300) -> str:
    """One line of plain text: control, invisible and formatting characters (escape codes, zero-width and
    bidi marks, tag characters) become spaces, whitespace is collapsed, and the length is capped."""
    text = "" if value is None else str(value)
    text = "".join(" " if unicodedata.category(c)[0] == "C" or c in "  " else c for c in text)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def clean_text(value: object, limit: int) -> str:
    """Like clean(), but line breaks stay (at most one blank line in a row), so a posting's lists and
    paragraphs are still readable."""
    text = ("" if value is None else str(value)).replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(
        c if c == "\n" else (" " if unicodedata.category(c)[0] == "C" or c in "  " else c) for c in text
    )
    lines: list[str] = []
    for line in (" ".join(raw.split()) for raw in text.split("\n")):
        if line or (lines and lines[-1]):
            lines.append(line)
    text = "\n".join(lines).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def for_terminal(text: str) -> str:
    """The model's answer as printed: line breaks stay, every other control character (escape codes
    included) is removed, so nothing a posting contained can drive the terminal."""
    return "".join(
        c if c == "\n" else (" " if unicodedata.category(c)[0] == "C" else c) for c in text.replace("\r", "")
    )


def wrap_untrusted(payload: dict[str, Any]) -> str:
    """A tool result as the model sees it: JSON between <untrusted_data> tags. Every "<" is written as
    \\u003c (still valid JSON), so no text inside can contain a closing tag and end the block early."""
    text = json.dumps(payload, ensure_ascii=False, default=str).replace("<", "\\u003c")
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + " ...[result cut: too long]"
    return f"{OPEN_TAG}\n{text}\n{CLOSE_TAG}"
