"""PDF reports: the options, the data, the base template, the locked-down printing, and the assistant tool.
Most tests use a small real SQLite file and no browser; two print a real PDF (skipped if Chromium is missing).
No network, no model calls, no cost."""

import asyncio
import http.server
import json
import threading
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from sonar import agent_runner, budget, llm
from sonar.assistant import outbox
from sonar.assistant.agent import Answer, Assistant
from sonar.assistant.safe import OPEN_TAG
from sonar.assistant.tools import PdfArgs, AssistantTools, read_only_engine
from sonar.config import Settings
from sonar.db import queries
from sonar.db.tables import make_engine
from sonar.llm import ChatReply, ToolCall
from sonar.models import Decision, KeywordHit, Listing
from sonar.pdf import make as pdf_make
from sonar.pdf.css import check_css
from sonar.pdf.data import NO_NEAR_MISSES_FOR_KEYWORDS, collect, latest_run_id
from sonar.pdf.make import PdfError, make_pdf
from sonar.pdf.render import html_to_pdf, render_html
from sonar.pdf.spec import PdfSpec, load_presets, resolve

EVIL = '<script>alert(1)</script> "quoted" & <b>bold</b>'


@pytest.fixture(autouse=True)
def fresh_budget():
    budget.configure(budget.RunBudget())
    yield
    budget.configure(budget.RunBudget())


def add(engine, run, site, job, title, matched, score=None, closing=None, hits=(), needs_review=False, summary=None, url=None):
    fields = {"listed_date": "2026-10-20", "summary": summary or f"About {title}"}
    if closing:
        fields |= {"closing_date": closing, "closing_unsure": ""}  # "" = sure (the real code stores text here)
    listing = queries.add_listing(
        engine,
        Listing(site=site, job_id=job, url=url or f"https://x.example/{job}", title=title, posting_text="text", text_hash=job, fields=fields),
        run,
    )
    details = {"hits": [h.model_dump() for h in hits], "needs_review": needs_review}
    queries.add_decision(engine, Decision(listing_id=listing, run_id=run, matcher="jev", matched=matched, score=score, details=details))


@pytest.fixture()
def engine(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'sonar.db'}")
    older = queries.start_run(engine)  # run 1: only an older run, so "latest" has to pick run 2
    queries.finish_run(engine, older, "ok", None)
    run = queries.start_run(engine)
    add(engine, run, "canadabuys", "C1", "SAP migration RFP", True, 0.9, closing="2026-12-01", hits=[KeywordHit(keyword="SAP", count=2, snippet="an SAP migration")])
    add(engine, run, "canadabuys", "C2", "Cloud staffing", True, 0.45, closing="2026-11-05", needs_review=True)
    add(engine, run, "merx", "M1", "Azure data platform", True, 0.7, closing="2026-11-20")
    add(engine, run, "canadabuys", "C3", "Road paving", False, 0.12)
    add(engine, run, "canadabuys", "C4", "Office chairs", False, 0.03)
    add(engine, run, "merx", "M2", "Bridge repair", False, 0.2)
    add(engine, run, "merx", "M3", "Not scored yet", None)
    queries.finish_run(engine, run, "ok", None)
    return engine


def settings(tmp_path, **overrides) -> Settings:
    return Settings(matcher="jev", pdf_dir=str(tmp_path / "pdfs"), **overrides)


# ---- custom styling -----------------------------------------------------------------------------------

def test_plain_styling_is_allowed():
    css = "h2 { color: #336699; } @media print { .card { padding: 4pt; } } @page { margin: 10mm }"
    assert check_css(css) == css


@pytest.mark.parametrize("css", [
    "body { background: url(http://evil.example/x.png) }",
    "body { background: URL( 'x' ) }",
    "@import 'http://evil.example/x.css';",
    "@font-face { font-family: x; src: local(Arial); }",
    "p::before { content: '\\75rl(x)' }",
    "body { width: expression(alert(1)) }",
    "</style><script>alert(1)</script>",
    "a { background: image-set('x' 1x) }",
    "a { behavior: x }",
    "x" * 4001,
    "a { color: red; \x00 }",
])
def test_styling_that_could_fetch_or_run_something_is_refused(css):
    with pytest.raises(ValueError):
        check_css(css)


# ---- options and presets ------------------------------------------------------------------------------

def test_the_defaults_are_valid_and_unknown_options_are_errors():
    assert PdfSpec().sections == ["overview", "matches", "near_misses"]
    with pytest.raises(ValidationError):
        PdfSpec(output_path="C:/anything.pdf")  # a person cannot choose where a file goes
    with pytest.raises(ValidationError):
        PdfSpec(template="other.html")


@pytest.mark.parametrize("bad", [
    {"max_matches": 0}, {"max_matches": 301}, {"max_near_misses": 101}, {"font_size_pt": 40}, {"accent_color": "red"},
    {"accent_color": "#12345"}, {"sections": []}, {"sections": ["matches", "secrets"]}, {"layout": "grid"}, {"title": "x" * 121},
    {"page_size": "A0"}, {"custom_css": "a { background: url(x) }"},
])
def test_options_out_of_range_are_refused(bad):
    with pytest.raises(ValidationError):
        PdfSpec(**bad)


def test_sections_keep_their_order_without_repeats_and_text_is_cleaned():
    spec = PdfSpec(sections=["matches", "overview", "matches"], title="Q4 \x1b[31mbrief\n", sites=["CanadaBuys", "  "])
    assert spec.sections == ["matches", "overview"] and spec.title == "Q4 [31mbrief" and spec.sites == ["canadabuys"]


def test_the_presets_in_the_repo_are_all_valid():
    presets = load_presets()
    assert {"standard", "brief", "full", "closing-soon"} <= set(presets)
    for preset in presets.values():
        PdfSpec(**preset.spec)


def test_a_preset_with_a_typo_stops_loading_and_names_the_preset(tmp_path):
    path = tmp_path / "pdf.yaml"
    path.write_text(yaml.safe_dump({"presets": {"oops": {"description": "d", "spec": {"layuot": "table"}}}}), encoding="utf-8")
    with pytest.raises(ValueError, match="oops"):
        load_presets(path)


def test_a_persons_options_win_over_the_preset():
    presets = load_presets()
    spec = resolve({"orientation": "landscape", "max_matches": 5}, "brief", presets)
    assert spec.layout == "table" and spec.sections == ["overview", "matches"]  # from the preset
    assert spec.orientation == "landscape" and spec.max_matches == 5  # from the person
    with pytest.raises(ValueError, match="no preset named"):
        resolve({}, "nope", presets)


# ---- the data -----------------------------------------------------------------------------------------

def test_the_latest_run_is_used_by_default(engine, tmp_path):
    assert latest_run_id(engine) == 2
    assert collect(engine, settings(tmp_path), PdfSpec()).report.run_id == 2


def test_counts_per_portal_add_up_the_way_the_run_went(engine, tmp_path):
    data = collect(engine, settings(tmp_path), PdfSpec())
    counts = {c.site: c for c in data.site_counts}
    assert (counts["canadabuys"].new_postings, counts["canadabuys"].matched, counts["canadabuys"].needs_review, counts["canadabuys"].rejected) == (4, 2, 1, 2)
    assert (counts["merx"].new_postings, counts["merx"].matched, counts["merx"].rejected, counts["merx"].unscored) == (3, 1, 1, 1)
    assert data.rejected_total == 3 and data.matches_in_run == 3


def test_near_misses_are_the_highest_scoring_rejections(engine, tmp_path):
    data = collect(engine, settings(tmp_path), PdfSpec())
    assert [(n.title, n.score) for n in data.near_misses] == [("Bridge repair", 0.2), ("Road paving", 0.12), ("Office chairs", 0.03)]
    assert [n.title for n in collect(engine, settings(tmp_path), PdfSpec(max_near_misses=1)).near_misses] == ["Bridge repair"]
    assert [n.title for n in collect(engine, settings(tmp_path), PdfSpec(sites=["canadabuys"])).near_misses] == ["Road paving", "Office chairs"]


def test_the_keyword_matcher_has_no_scores_so_no_near_misses(engine, tmp_path):
    data = collect(engine, Settings(matcher="keywords", pdf_dir=str(tmp_path)), PdfSpec())
    assert data.near_misses == [] and data.near_miss_note == NO_NEAR_MISSES_FOR_KEYWORDS


def test_matches_can_be_filtered_sorted_and_capped(engine, tmp_path):
    s = settings(tmp_path)
    titles = lambda spec: [m.title for m in collect(engine, s, spec).report.matches]
    assert titles(PdfSpec(sites=["merx"])) == ["Azure data platform"]
    assert titles(PdfSpec(sites=["CanadaBuys"])) == ["SAP migration RFP", "Cloud staffing"]  # the portal's display name works too
    assert titles(PdfSpec(keyword="sap")) == ["SAP migration RFP"]
    assert titles(PdfSpec(sort="closing")) == ["Cloud staffing", "Azure data platform", "SAP migration RFP"]
    assert titles(PdfSpec(sort="title")) == ["Azure data platform", "Cloud staffing", "SAP migration RFP"]
    capped = collect(engine, s, PdfSpec(max_matches=2))
    assert len(capped.report.matches) == 2 and capped.matches_after_filter == 3 and capped.matches_in_run == 3


def test_a_missing_run_is_an_error_with_a_plain_message(engine, tmp_path):
    with pytest.raises(ValueError, match="no run 99"):
        collect(engine, settings(tmp_path), PdfSpec(run_id=99))
    with pytest.raises(ValueError, match="no run is recorded"):
        latest_run_id(make_engine(f"sqlite:///{tmp_path / 'empty.db'}"))


# ---- the base template --------------------------------------------------------------------------------

@pytest.fixture()
def hostile(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'hostile.db'}")
    run = queries.start_run(engine)
    add(engine, run, "canadabuys", "H1", EVIL, True, 0.9, closing="2026-12-01", summary=EVIL, url="javascript:alert(1)",
        hits=[KeywordHit(keyword="SAP", count=1, snippet=f"{EVIL} SAP")])
    add(engine, run, "canadabuys", "H2", EVIL + " near miss", False, 0.1, url="javascript:alert(2)")
    queries.finish_run(engine, run, "ok", None)
    return engine


def test_text_from_postings_is_escaped_and_bad_links_are_dead(hostile, tmp_path):
    spec = PdfSpec(title=EVIL.replace("\n", " "))
    html = render_html(collect(hostile, settings(tmp_path), spec), spec)
    assert "<script" not in html.lower() and "<b>bold</b>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "javascript:" not in html  # both the match's and the near miss's link
    assert 'href="#"' in html


def test_custom_styling_comes_after_the_base_styling(engine, tmp_path):
    spec = PdfSpec(custom_css="h2 { color: #aa0000; }", accent_color="#112233", font_size_pt=12)
    html = render_html(collect(engine, settings(tmp_path), spec), spec)
    assert html.index("h2 { color: #aa0000; }") > html.index("--accent: #112233") and "12.0pt" in html


def test_sections_appear_in_the_order_asked_and_only_those(engine, tmp_path):
    spec = PdfSpec(sections=["near_misses", "overview"])
    html = render_html(collect(engine, settings(tmp_path), spec), spec)
    assert html.index("Closest rejections") < html.index("Overview") and "<h2>Matches</h2>" not in html


def test_the_table_layout_and_the_card_layout_both_show_every_match(engine, tmp_path):
    for layout in ("cards", "table"):
        spec = PdfSpec(layout=layout, sections=["matches"])
        html = render_html(collect(engine, settings(tmp_path), spec), spec)
        assert all(t in html for t in ("SAP migration RFP", "Cloud staffing", "Azure data platform")), layout
    assert 'class="card"' not in html and "<table>" in html


def test_a_filtered_pdf_says_so_and_an_empty_one_explains(engine, tmp_path):
    spec = PdfSpec(sites=["merx"], keyword="nothing-matches-this", sections=["matches"])
    html = render_html(collect(engine, settings(tmp_path), spec), spec)
    assert "No matches fit these filters" in html and 'containing "nothing-matches-this"' in html


# ---- printing: needs Chromium -------------------------------------------------------------------------

def chromium_or_skip(html: str, spec: PdfSpec = PdfSpec()) -> bytes:
    try:
        return asyncio.run(html_to_pdf(html, spec, 1))
    except Exception as exc:
        if "Executable doesn't exist" in str(exc) or "playwright install" in str(exc):
            pytest.skip("Chromium is not installed")
        raise


def test_a_real_pdf_is_printed(engine, tmp_path):
    spec = PdfSpec(orientation="landscape")
    pdf = chromium_or_skip(render_html(collect(engine, settings(tmp_path), spec), spec), spec)
    assert pdf.startswith(b"%PDF-") and pdf.rstrip().endswith(b"%%EOF") and len(pdf) > 3000


def test_printing_fetches_nothing_and_runs_no_script():
    """A page that tries every way of reaching the network or running code. The local server must see no request."""
    seen: list[str] = []

    class Spy(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            seen.append(self.path)
            self.send_response(404)
            self.end_headers()

        do_POST = do_GET  # noqa: N815

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Spy)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    html = f"""<!doctype html><html><head>
      <link rel="stylesheet" href="{base}/sheet.css"><style>@import url({base}/import.css); body {{ background: url({base}/bg.png) }}</style>
      <script src="{base}/script.js"></script><script>new Image().src = "{base}/js-ran";</script></head>
      <body onload="fetch('{base}/onload')"><img src="{base}/pic.png"><iframe src="{base}/frame"></iframe>
      <a href="{base}/link">a link</a><p>hello</p></body></html>"""
    try:
        pdf = chromium_or_skip(html)
    finally:
        server.shutdown()
    assert pdf.startswith(b"%PDF-")
    assert seen == []


# ---- the entry point ----------------------------------------------------------------------------------

def test_make_pdf_writes_a_file_named_by_us(engine, tmp_path, monkeypatch):
    async def fake_print(html, spec, run_id):
        return b"%PDF-fake " + str(run_id).encode()

    monkeypatch.setattr(pdf_make, "html_to_pdf", fake_print)
    s = settings(tmp_path)
    result = asyncio.run(make_pdf(engine, s, PdfSpec(title="../../evil name")))
    assert result.path.parent == s.pdf_path and result.path.name.startswith("sonar-run-2-") and result.path.suffix == ".pdf"
    assert result.path.read_bytes() == b"%PDF-fake 2" and (result.matches, result.near_misses) == (3, 3)
    only_overview = asyncio.run(make_pdf(engine, s, PdfSpec(sections=["overview"])))
    assert (only_overview.matches, only_overview.near_misses) == (0, 0)


def test_a_browser_failure_is_a_plain_error(engine, tmp_path, monkeypatch):
    async def broken(html, spec, run_id):
        raise OSError("chromium exploded with /secret/path in it")

    monkeypatch.setattr(pdf_make, "html_to_pdf", broken)
    with pytest.raises(PdfError) as caught:
        asyncio.run(make_pdf(engine, settings(tmp_path), PdfSpec()))
    assert "/secret/path" not in str(caught.value) and "playwright install" in str(caught.value)


# ---- the assistant's tool -----------------------------------------------------------------------------

@pytest.fixture()
def tools(engine, tmp_path, monkeypatch):
    async def fake_print(html, spec, run_id):
        return b"%PDF-fake"

    monkeypatch.setattr(pdf_make, "html_to_pdf", fake_print)
    return AssistantTools(read_only_engine(f"sqlite:///{tmp_path / 'sonar.db'}"), settings(tmp_path), {})


def call(tools: AssistantTools, name: str, **args) -> str:
    return asyncio.run(tools.run(ToolCall(id="1", name=name, arguments=json.dumps(args))))


def test_pdf_options_lists_the_presets(tools):
    result = call(tools, "pdf_options")
    assert result.startswith(OPEN_TAG) and "brief" in result and "closing-soon" in result


def test_make_pdf_makes_the_file_and_puts_it_in_the_outbox(tools):
    with outbox.collecting() as files:
        result = call(tools, "make_pdf", preset="brief", sites=["merx"], sort="closing")
    assert '"ready": true' in result and "Do not invent a link" in result and str(files[0]) not in result  # the model never sees a path
    assert len(files) == 1 and files[0].read_bytes() == b"%PDF-fake"


@pytest.mark.parametrize("arguments, expected", [
    ({"custom_css": "a { background: url(http://evil.example) }"}, "url("),
    ({"output_path": "C:/Windows/x.pdf"}, "output_path"),
    ({"preset": "nope"}, "no preset named"),
    ({"run_id": 99}, "no run 99"),
    ({"max_matches": 100000}, "max_matches"),
])
def test_a_bad_request_comes_back_as_a_short_error_and_makes_no_file(tools, arguments, expected):
    with outbox.collecting() as files:
        result = call(tools, "make_pdf", **arguments)
    assert result.startswith("ERROR:") and expected in result and files == []


def test_only_a_few_pdfs_can_be_made_for_one_question(tools):
    with outbox.collecting() as files:
        results = [call(tools, "make_pdf") for _ in range(outbox.MAX_FILES_PER_QUESTION + 1)]
    assert len(files) == outbox.MAX_FILES_PER_QUESTION and results[-1].startswith("ERROR: at most")


def test_outside_a_question_nothing_is_collected_but_the_pdf_is_still_made(tools):
    assert '"ready": true' in call(tools, "make_pdf")  # no outbox is open: the file exists, nobody is handed it


def test_the_tool_arguments_are_the_spec_plus_a_preset():
    assert set(PdfArgs.model_fields) == set(PdfSpec.model_fields) | {"preset"}


class Script:
    """Replaces llm.chat with scripted replies."""

    def __init__(self, monkeypatch, *replies: ChatReply) -> None:
        self.replies = list(replies)
        monkeypatch.setattr(llm, "chat", self.chat)

    async def chat(self, role, messages, tools=None, client=None):
        return self.replies.pop(0)


def test_ask_full_returns_the_pdf_made_while_answering(tools, monkeypatch):
    call_ = {"id": "c1", "type": "function", "function": {"name": "make_pdf", "arguments": json.dumps({"preset": "brief"})}}
    Script(
        monkeypatch,
        ChatReply(message={"role": "assistant", "content": "", "tool_calls": [call_]}, content="", cost_usd=0.001,
                  tool_calls=[ToolCall(id="c1", name="make_pdf", arguments=json.dumps({"preset": "brief"}))]),
        ChatReply(message={"role": "assistant", "content": "Your brief is ready."}, content="Your brief is ready.", tool_calls=[], cost_usd=0.001),
    )
    answer = asyncio.run(Assistant(tools, ["canadabuys"]).ask_full("make me a brief"))
    assert isinstance(answer, Answer) and answer.text == "Your brief is ready."
    assert len(answer.files) == 1 and answer.files[0].suffix == ".pdf"


def test_two_questions_answered_at_once_never_share_files(tools, monkeypatch):
    async def go():
        async def one() -> list[Path]:
            with outbox.collecting() as files:
                await asyncio.sleep(0.01)
                await tools.run(ToolCall(id="1", name="make_pdf", arguments="{}"))
                await asyncio.sleep(0.01)
            return files

        return await asyncio.gather(one(), one())

    first, second = asyncio.run(go())
    assert len(first) == 1 and len(second) == 1 and first != second


def test_the_terminal_chat_says_where_the_pdf_was_saved(tools, monkeypatch):
    from sonar.assistant import chat

    arguments = json.dumps({"preset": "brief"})
    Script(
        monkeypatch,
        ChatReply(message={"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "make_pdf", "arguments": arguments}}]},
                  content="", cost_usd=0.001, tool_calls=[ToolCall(id="c1", name="make_pdf", arguments=arguments)]),
        ChatReply(message={"role": "assistant", "content": "Your brief is ready."}, content="Your brief is ready.", tool_calls=[], cost_usd=0.001),
    )
    lines = iter(["make me a brief", "/quit"])
    printed: list[str] = []
    chat.repl(Assistant(tools, []), read=lambda prompt: next(lines), write=printed.append)
    saved = [p for p in printed if "PDF saved:" in p]
    assert len(saved) == 1 and saved[0].strip().endswith(".pdf") and "Your brief is ready." in "\n".join(printed)
