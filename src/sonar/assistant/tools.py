"""The assistant's tools: six read-only database queries and a PDF maker, plain code. The assistant has no other
way to reach anything: no browser, shell, email, scan or login, and the database is opened read-only. The PDF
maker only reads the database and writes one PDF file into data/pdfs (see sonar/pdf/); a person's formatting
wishes reach it as validated options, never as code.

Every result is cleaned by safe.py and wrapped as untrusted data. Most tools return short fields (title,
summary, keyword snippets); only read_posting_text returns the stored posting text, in chunks, for questions
the summary cannot answer."""

import inspect
import json
import sqlite3
from collections.abc import Callable
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from sonar.assistant import outbox
from sonar.assistant.safe import clean, clean_text, wrap_untrusted
from sonar.config import Settings
from sonar.db.tables import DecisionRow, ListingRow, RunRow
from sonar.llm import ToolCall
from sonar.pdf.make import PdfError, make_pdf
from sonar.pdf.spec import PdfSpec, load_presets, resolve
from sonar.report.build import checked_text, closing_text
from sonar.sites import note_sites

DEFAULT_LIMIT = 10
MAX_LIMIT = 25  # postings in one answer
MAX_HITS = 10  # keyword hits shown per posting
TEXT_CHUNK = 5000  # characters of posting text per read_posting_text call
NO_COST_NOTE = "the run's total cost is not stored in the database (it is only printed when the run ends)"


def read_only_engine(url: str) -> Engine:
    """Open the SQLite database so that writing is impossible (SQLite's own read-only mode)."""
    if not url.startswith("sqlite:///"):
        raise ValueError("the assistant reads SQLite databases only")
    path = Path(url.removeprefix("sqlite:///")).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"no database at {path}; Sonar has to run once first")
    return create_engine("sqlite://", creator=lambda: sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True))


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")  # an argument we did not define is an error, not ignored


class NoArgs(_Args):
    pass


class ListArgs(_Args):
    limit: int = DEFAULT_LIMIT


class SearchArgs(_Args):
    site: str | None = None
    keyword: str | None = None
    closing_after: date | None = None
    closing_before: date | None = None
    found_since: date | None = None
    limit: int = DEFAULT_LIMIT


class ListingArgs(_Args):
    listing_id: int


class ReadTextArgs(_Args):
    listing_id: int
    offset: int = Field(default=0, ge=0)


class PdfArgs(PdfSpec):
    """make_pdf's arguments: every PdfSpec option, plus a preset to start from (explicit options win)."""

    preset: str | None = Field(default=None, max_length=40)


def _object(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required or [], "additionalProperties": False}


def _tool(name: str, description: str, parameters: dict[str, Any]) -> dict[str, Any]:
    return {"type": "function", "function": {"name": name, "description": description, "parameters": parameters}}


_LIMIT = {"type": "integer", "description": f"How many postings to return (1 to {MAX_LIMIT}, default {DEFAULT_LIMIT})."}
_ID = {"type": "integer", "description": "The posting's listing_id, as returned by the other tools."}
_DATE = "A date as YYYY-MM-DD."

TOOL_SCHEMAS: list[dict[str, Any]] = [
    _tool("list_recent_matches", "The most recently found matching postings, newest first.",
          _object({"limit": _LIMIT})),
    _tool("search_matches",
          "Matching postings filtered by portal, keyword and/or closing date. All filters are optional and combine. "
          "With a closing-date filter the soonest closing comes first; postings with no known closing date are left out.",
          _object({
              "site": {"type": "string", "description": "Portal name, for example canadabuys."},
              "keyword": {"type": "string", "description": "Text to look for in the matched keywords, title or summary."},
              "closing_after": {"type": "string", "description": f"Only postings closing on or after this date. {_DATE}"},
              "closing_before": {"type": "string", "description": f"Only postings closing on or before this date. {_DATE}"},
              "found_since": {"type": "string", "description": f"Only postings Sonar first saw on or after this date. {_DATE}"},
              "limit": _LIMIT,
          })),
    _tool("get_posting", "One posting's summary, key fields and keyword hits.", _object({"listing_id": _ID}, ["listing_id"])),
    _tool("explain_match", "Why a posting did or did not match: each matcher's verdict, score and keyword evidence.",
          _object({"listing_id": _ID}, ["listing_id"])),
    _tool("read_posting_text",
          f"The stored text of a posting, {TEXT_CHUNK} characters at a time, for questions the summary and fields cannot "
          "answer. The result gives total_chars and next_offset; call again with offset=next_offset to read on.",
          _object({"listing_id": _ID, "offset": {"type": "integer", "description": "Character to start from (default 0)."}},
                  ["listing_id"])),
    _tool("last_run_status", "Status, times, notes, counts and known cost of the most recent run.", _object({})),
    _tool("pdf_options",
          "The PDF presets (ready-made formats) and the options that change a PDF. Call it before make_pdf when the "
          "person's wishes are unclear or you need a preset name.",
          _object({})),
    _tool("make_pdf",
          "Make a PDF of a run: an overview with counts per portal, the matches, and the closest rejections. Start from a "
          "preset if one fits and add only the options the person asked for. The chat delivers the file itself; you only "
          "say that it is ready.",
          _object({
              "preset": {"type": "string", "description": "A preset name from pdf_options. Without one, the standard format."},
              "run_id": {"type": "integer", "description": "The run number. Default: the latest run."},
              "title": {"type": "string", "description": "A title for the PDF (up to 120 characters)."},
              "subtitle": {"type": "string", "description": "A line under the title, for example who it is for."},
              "sections": {"type": "array", "items": {"type": "string", "enum": ["overview", "matches", "near_misses"]},
                           "description": "Which parts, in page order. Default: all three."},
              "layout": {"type": "string", "enum": ["cards", "table"], "description": "Matches as full cards or a compact table."},
              "sites": {"type": "array", "items": {"type": "string"}, "description": "Only these portals, for example [\"canadabuys\"]."},
              "keyword": {"type": "string", "description": "Only matches whose keywords, title or summary contain this text."},
              "sort": {"type": "string", "enum": ["rank", "closing", "site", "title"], "description": "Order of the matches."},
              "max_matches": {"type": "integer", "description": "1 to 300, default 100."},
              "max_near_misses": {"type": "integer", "description": "0 to 100, default 25."},
              "show_summaries": {"type": "boolean", "description": "Show each match's summary text (default true)."},
              "show_keyword_hits": {"type": "boolean", "description": "Show the keyword snippets on each match (default true)."},
              "orientation": {"type": "string", "enum": ["portrait", "landscape"]},
              "page_size": {"type": "string", "enum": ["Letter", "A4"]},
              "font_size_pt": {"type": "number", "description": "8 to 14, default 10."},
              "accent_color": {"type": "string", "description": "Headings and chips color as #RRGGBB."},
              "custom_css": {"type": "string",
                             "description": "Extra CSS for looks the options above cannot express. No url(), @import, scripts or "
                                            "HTML; only @media and @page rules. Never copy posting text into it."},
          })),
]
TOOL_NAMES = [t["function"]["name"] for t in TOOL_SCHEMAS]


def _date_text(moment: datetime | None, settings: Settings) -> str | None:
    """A stored UTC time shown in the business time zone."""
    if moment is None:
        return None
    return moment.replace(tzinfo=timezone.utc).astimezone(settings.tz).strftime("%Y-%m-%d %H:%M %Z")


def _opt(value: object, limit: int = 100) -> str | None:
    """Cleaned text, or None when there is nothing."""
    return clean(value, limit) or None


def _closing_date(fields: dict) -> date | None:
    try:
        return date.fromisoformat(fields.get("closing_date") or "")
    except ValueError:
        return None


class AssistantTools:
    def __init__(self, engine: Engine, settings: Settings, accounts: dict[str, str] | None = None) -> None:
        self.engine, self.settings, self.accounts = engine, settings, accounts or {}
        self.handlers: dict[str, tuple[type[_Args], Callable[[Any], dict[str, Any] | str]]] = {
            "list_recent_matches": (ListArgs, self.list_recent_matches),
            "search_matches": (SearchArgs, self.search_matches),
            "get_posting": (ListingArgs, self.get_posting),
            "explain_match": (ListingArgs, self.explain_match),
            "read_posting_text": (ReadTextArgs, self.read_posting_text),
            "last_run_status": (NoArgs, self.last_run_status),
            "pdf_options": (NoArgs, self.pdf_options),
            "make_pdf": (PdfArgs, self.make_pdf),
        }

    async def run(self, call: ToolCall) -> str:
        """Run one tool call from the model. A bad call comes back as short text the model can act on."""
        if call.name not in self.handlers:
            return f"ERROR: there is no tool named '{clean(call.name, 40)}'. The only tools are: {', '.join(TOOL_NAMES)}."
        args_model, handler = self.handlers[call.name]
        try:
            raw = json.loads(call.arguments or "{}")
            if not isinstance(raw, dict):
                raise ValueError("arguments must be a JSON object")
            args = args_model.model_validate(raw)
        except (ValueError, ValidationError) as exc:
            return f"ERROR: bad arguments for {call.name}: {clean(exc, 300)}"
        try:
            result = handler(args)
            if inspect.isawaitable(result):
                result = await result
        except SQLAlchemyError:
            return "ERROR: the database could not be read."
        return result if isinstance(result, str) else wrap_untrusted(result)

    # ---- tools -------------------------------------------------------------------------------------

    def list_recent_matches(self, args: ListArgs) -> dict[str, Any]:
        rows = self._matches()
        limit = self._limit(args.limit)
        return {"total_matches": len(rows), "shown": min(limit, len(rows)), "matches": [self._brief(r) for r in rows[:limit]]}

    def search_matches(self, args: SearchArgs) -> dict[str, Any]:
        rows = self._matches()
        if args.site:
            rows = [r for r in rows if r["site"].lower() == args.site.strip().lower()]
        if args.keyword:
            needle = args.keyword.strip().lower()
            rows = [r for r in rows if needle in r["haystack"]]
        if args.found_since:
            rows = [r for r in rows if self._found_on(r) >= args.found_since]
        payload: dict[str, Any] = {}
        if args.closing_after or args.closing_before:
            dated = [r for r in rows if r["closing"] is not None]
            payload["left_out_no_closing_date"] = len(rows) - len(dated)
            rows = [
                r for r in dated
                if (not args.closing_after or r["closing"] >= args.closing_after)
                and (not args.closing_before or r["closing"] <= args.closing_before)
            ]
            rows.sort(key=lambda r: r["closing"])
        limit = self._limit(args.limit)
        return {"found": len(rows), "shown": min(limit, len(rows)), **payload, "matches": [self._brief(r) for r in rows[:limit]]}

    def get_posting(self, args: ListingArgs) -> dict[str, Any] | str:
        with Session(self.engine) as session:
            listing = session.execute(  # never posting_text or raw_html: the model gets the summary, not the page
                select(ListingRow.id, ListingRow.site, ListingRow.job_id, ListingRow.url, ListingRow.title,
                       ListingRow.fields, ListingRow.first_seen_at).where(ListingRow.id == args.listing_id)
            ).one_or_none()
            decisions = self._decisions(session, args.listing_id)
        if listing is None:
            return f"ERROR: there is no posting with listing_id {args.listing_id}."
        fields = listing.fields or {}
        return {
            "listing_id": listing.id,
            "site": listing.site,
            "account": self.accounts.get(listing.site),
            "job_id": clean(listing.job_id, 100),
            "url": clean(listing.url, 500),
            "title": clean(listing.title or listing.url, 300),
            "first_seen": _date_text(listing.first_seen_at, self.settings),
            "listed_date": _opt(fields.get("listed_date"), 40),
            "posted_date": _opt(fields.get("posted_date"), 40),
            "closing_date": _opt(closing_text(fields), 80),
            "solicitation_number": _opt(checked_text(fields, "solicitation_number", "not found"), 120),
            "supply_arrangement": _opt(checked_text(fields, "supply_arrangement", "N/A"), 120),
            "department": _opt(checked_text(fields, "department", "not found"), 200),
            "summary": _opt(fields.get("summary"), 1500),
            "how_to_apply": _opt(fields.get("how_to_apply"), 600),
            "matched_by": [d.matcher for d in decisions if d.matched],
            "keyword_hits": self._hits(decisions),
        }

    def explain_match(self, args: ListingArgs) -> dict[str, Any] | str:
        with Session(self.engine) as session:
            title = session.scalar(select(ListingRow.title).where(ListingRow.id == args.listing_id))
            exists = session.scalar(select(ListingRow.id).where(ListingRow.id == args.listing_id)) is not None
            decisions = self._decisions(session, args.listing_id)
        if not exists:
            return f"ERROR: there is no posting with listing_id {args.listing_id}."
        return {
            "listing_id": args.listing_id,
            "title": clean(title, 300),
            "decisions": [self._explain(d) for d in decisions] or "no matcher has decided this posting yet",
        }

    def read_posting_text(self, args: ReadTextArgs) -> dict[str, Any] | str:
        with Session(self.engine) as session:
            row = session.execute(  # SQLite cuts the chunk out, so a long posting is never loaded whole
                select(ListingRow.id, ListingRow.title, func.length(ListingRow.posting_text).label("total"),
                       func.substr(ListingRow.posting_text, args.offset + 1, TEXT_CHUNK).label("chunk"))
                .where(ListingRow.id == args.listing_id)
            ).one_or_none()
        if row is None:
            return f"ERROR: there is no posting with listing_id {args.listing_id}."
        end = args.offset + TEXT_CHUNK
        return {
            "listing_id": row.id,
            "title": clean(row.title, 300),
            "total_chars": row.total,
            "offset": args.offset,
            "text": clean_text(row.chunk, TEXT_CHUNK),
            "next_offset": end if end < row.total else None,
        }

    def last_run_status(self, args: NoArgs) -> dict[str, Any]:
        matcher = self.settings.matcher
        with Session(self.engine) as session:
            run = session.scalars(select(RunRow).order_by(RunRow.id.desc()).limit(1)).first()
            if run is None:
                return {"last_run": None, "note": "no run is recorded yet"}
            new_postings = session.scalar(select(func.count()).select_from(ListingRow).where(ListingRow.run_id == run.id))
            decided = session.execute(
                select(DecisionRow.matcher, DecisionRow.matched, DecisionRow.details).where(DecisionRow.run_id == run.id)
            ).all()
        current = [d for d in decided if d.matcher == matcher]
        return {
            "run_id": run.id,
            "status": run.status,
            "started": _date_text(run.started_at, self.settings),
            "finished": _date_text(run.finished_at, self.settings),
            "notes": _opt(run.notes, 600),
            "new_postings_stored": new_postings or 0,
            "matcher": matcher,
            "scored": sum(d.matched is not None for d in current),
            "matches": sum(d.matched is True for d in current),
            "unscored_will_retry": sum(d.matched is None for d in current),
            "matcher_cost_usd": round(sum(float((d.details or {}).get("cost_usd") or 0) for d in decided), 6),
            "total_cost": NO_COST_NOTE,
        }

    def pdf_options(self, args: NoArgs) -> dict[str, Any]:
        defaults = PdfSpec()
        return {
            "presets": {name: preset.description for name, preset in load_presets().items()},
            "defaults": defaults.model_dump(exclude={"run_id", "title", "subtitle", "keyword", "custom_css"}),
            "note": "Options given to make_pdf are added on top of the preset. Near misses need the Jev matcher.",
        }

    async def make_pdf(self, args: PdfArgs) -> dict[str, Any] | str:
        if outbox.full():
            return f"ERROR: at most {outbox.MAX_FILES_PER_QUESTION} PDFs can be made for one question."
        try:
            spec = resolve(args.model_dump(exclude_unset=True, exclude={"preset"}), args.preset, load_presets())
            result = await make_pdf(self.engine, self.settings, spec, note_sites(), self.accounts)
        except (ValueError, PdfError) as exc:
            return f"ERROR: {clean(exc, 300)}"
        outbox.add(result.path)
        return {
            "ready": True,
            "run_id": result.run_id,
            "matches_in_pdf": result.matches,
            "near_misses_in_pdf": result.near_misses,
            "size_kb": max(1, result.size_bytes // 1024),
            "note": "The PDF is ready and the chat delivers the file itself. Tell the person it is ready and what it holds. "
                    "Do not invent a link or a file path.",
        }

    # ---- helpers -----------------------------------------------------------------------------------

    @staticmethod
    def _limit(value: int) -> int:
        return max(1, min(value, MAX_LIMIT))

    @staticmethod
    def _decisions(session: Session, listing_id: int) -> list[DecisionRow]:
        return list(session.scalars(select(DecisionRow).where(DecisionRow.listing_id == listing_id).order_by(DecisionRow.id)))

    def _matches(self) -> list[dict[str, Any]]:
        """Postings the current matcher matched, newest first, as small dicts (no posting text)."""
        query = (
            select(ListingRow.id, ListingRow.site, ListingRow.url, ListingRow.title, ListingRow.fields,
                   ListingRow.first_seen_at, DecisionRow.score, DecisionRow.details)
            .join(DecisionRow, DecisionRow.listing_id == ListingRow.id)
            .where(DecisionRow.matcher == self.settings.matcher, DecisionRow.matched.is_(True))
            .order_by(ListingRow.first_seen_at.desc(), ListingRow.id.desc())
        )
        rows: dict[int, dict[str, Any]] = {}
        with Session(self.engine) as session:
            for r in session.execute(query):
                if r.id in rows:
                    continue
                fields, details = r.fields or {}, r.details or {}
                hit_words = [str(h.get("keyword", "")) for h in details.get("hits", [])]
                rows[r.id] = {
                    "id": r.id, "site": r.site, "url": r.url, "title": r.title, "fields": fields,
                    "first_seen_at": r.first_seen_at, "score": r.score, "needs_review": bool(details.get("needs_review")),
                    "closing": _closing_date(fields),
                    "haystack": " ".join([*hit_words, r.title or "", str(fields.get("summary") or "")]).lower(),
                }
        return list(rows.values())

    def _found_on(self, row: dict[str, Any]) -> date:
        return row["first_seen_at"].replace(tzinfo=timezone.utc).astimezone(self.settings.tz).date()

    def _brief(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "listing_id": row["id"],
            "site": row["site"],
            "account": self.accounts.get(row["site"]),
            "title": clean(row["title"] or row["url"], 200),
            "listed_date": _opt(row["fields"].get("listed_date"), 40),
            "closing_date": _opt(closing_text(row["fields"]), 80),
            "score": None if row["score"] is None else round(row["score"], 2),
            "needs_review": row["needs_review"],
            "url": clean(row["url"], 500),
        }

    @staticmethod
    def _hits(decisions: list[DecisionRow]) -> list[dict[str, Any]]:
        hits = [
            {"matcher": d.matcher, "keyword": clean(h.get("keyword"), 80), "count": h.get("count"),
             "snippet": clean(h.get("snippet"), 250)}
            for d in decisions for h in (d.details or {}).get("hits", [])
        ]
        return hits[:MAX_HITS]

    def _explain(self, decision: DecisionRow) -> dict[str, Any]:
        details = decision.details or {}
        out: dict[str, Any] = {
            "matcher": decision.matcher,
            "decided": _date_text(decision.decided_at, self.settings),
            "matched": decision.matched,
        }
        if decision.matched is None:
            out["reason"] = f"not scored ({clean(details.get('error'), 200) or 'unknown reason'}); the next run tries again"
        elif decision.matcher == "jev":
            s = self.settings
            out["score"] = None if decision.score is None else round(decision.score, 2)
            out["needs_review"] = bool(details.get("needs_review"))
            out["reason"] = (
                f"Jev gives one probability that the posting is relevant, not reasons. A match is {s.jev_match_threshold} "
                f"or higher; from {s.jev_review_threshold} up to that is a match flagged 'needs review'; below is no match."
            )
        else:
            out["reason"] = "keyword matcher: a match means at least one listed keyword appears as a whole word in the posting text"
        out["keyword_hits"] = self._hits([decision])
        return out
