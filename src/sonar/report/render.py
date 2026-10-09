"""Renders a Report to HTML and plain text with Jinja2, and writes the files.

Everything from a posting (title, snippets) is untrusted text: the HTML template escapes it,
links are only emitted for http(s) addresses, and keyword highlighting is done on escaped text.
"""

import re
import textwrap
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape

from sonar.matching.keywords import compile_keyword
from sonar.models import Report

TEMPLATES = Path(__file__).resolve().parent / "templates"
TEXT_WIDTH = 78


def highlight(snippet: str, keyword: str) -> Markup:
    """The snippet with each occurrence of the keyword wrapped in <mark>. The snippet is escaped first."""
    pieces, last = [], 0
    for match in compile_keyword(keyword).finditer(snippet):
        pieces += [escape(snippet[last:match.start()]), Markup("<mark>"), escape(match.group()), Markup("</mark>")]
        last = match.end()
    pieces.append(escape(snippet[last:]))
    return Markup("").join(pieces)


def safe_url(url: str) -> str:
    return url if re.match(r"https?://", url, re.IGNORECASE) else "#"


def wrap(text: str, indent: str = "   ") -> str:
    return textwrap.fill(text, TEXT_WIDTH, initial_indent=indent, subsequent_indent=indent, break_long_words=False)


def _environment() -> Environment:
    env = Environment(
        loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]), trim_blocks=True, lstrip_blocks=True
    )
    env.filters.update(highlight=highlight, safe_url=safe_url, wrap=wrap)
    return env


def render_html(report: Report) -> str:
    return _environment().get_template("report.html").render(report=report)


def render_text(report: Report) -> str:
    return _environment().get_template("report.txt").render(report=report, width=TEXT_WIDTH)


def write_report(report: Report, out_dir: Path) -> list[Path]:
    """Write report-run-<id>.html and .txt into out_dir and return the paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for suffix, content in (("html", render_html(report)), ("txt", render_text(report))):
        path = out_dir / f"report-run-{report.run_id}.{suffix}"
        path.write_text(content, encoding="utf-8")
        paths.append(path)
    return paths
