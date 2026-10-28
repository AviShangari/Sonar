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
from sonar.models import Report, ReportMatch

TEMPLATES = Path(__file__).resolve().parent / "templates"
TEXT_WIDTH = 78
MAX_EMAIL_BYTES = 95_000  # Gmail clips an HTML email at about 102 KB


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


# How each portal is named in the email. A site not listed here is shown as its key in title case.
PORTAL_NAMES = {"canadabuys": "CanadaBuys", "ontariotenders": "Ontario Tenders", "ariba": "SAP Ariba"}


def portal_name(site: str) -> str:
    """The portal a site belongs to. Two logins on one portal (ariba, ariba2) share one name."""
    base = site.rstrip("0123456789") or site
    return PORTAL_NAMES.get(base, base.replace("_", " ").replace("-", " ").title())


def by_portal(matches: list[ReportMatch]) -> list[tuple[str, list[ReportMatch]]]:
    """The matches grouped by portal: the portal with the most matches first, then by name.
    Inside a group the report's own ranking is kept."""
    groups: dict[str, list[ReportMatch]] = {}
    for match in matches:
        groups.setdefault(portal_name(match.site), []).append(match)
    return sorted(groups.items(), key=lambda group: (-len(group[1]), group[0]))


def partial_note(report: Report) -> str | None:
    """The one calm line for a partial run, for readers who are not the developer. The technical errors stay in the
    database and the console. None for any other status (a failed run gets its own banner)."""
    if report.status != "partial":
        return None
    names = report.failed_portals
    if not names:
        return "Today's run stopped early, so this list may be incomplete. Please try again later."
    who = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    return f"We couldn't check {who} today, so this list may be incomplete. Please try again later."


def wrap(text: str, indent: str = "   ") -> str:
    return textwrap.fill(text, TEXT_WIDTH, initial_indent=indent, subsequent_indent=indent, break_long_words=False)


def _environment() -> Environment:
    env = Environment(
        loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]), trim_blocks=True, lstrip_blocks=True
    )
    env.filters.update(highlight=highlight, safe_url=safe_url, wrap=wrap, by_portal=by_portal)
    env.globals["partial_note"] = partial_note
    return env


def render_html(report: Report) -> str:
    return _environment().get_template("report.html").render(report=report)


def render_text(report: Report) -> str:
    return _environment().get_template("report.txt").render(report=report, width=TEXT_WIDTH)


def render_email_text(report: Report) -> str:
    """The plain-text copy of the email: per portal, per match the card's fields as labelled lines, plus the link."""
    return _environment().get_template("email.txt").render(report=report)


def render_email_html(report: Report) -> str:
    """The HTML email: one compact table per portal, one row per match. Styles are inline; the small <style> block
    only restacks the rows on phones (mail apps that drop it still show a normal table)."""
    template = _environment().get_template("email.html")
    html = template.render(report=report, short=False)
    if len(html.encode()) > MAX_EMAIL_BYTES:  # Gmail cuts a long email off with "View entire message": shorten the summaries
        html = template.render(report=report, short=True)
    return html


def write_report(report: Report, out_dir: Path) -> list[Path]:
    """Write report-run-<id>.html and .txt into out_dir and return the paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for suffix, content in (("html", render_html(report)), ("txt", render_text(report))):
        path = out_dir / f"report-run-{report.run_id}.{suffix}"
        path.write_text(content, encoding="utf-8")
        paths.append(path)
    return paths
