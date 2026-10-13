"""Route notes: the agent's description of how it reached the list. For people only: untrusted,
cleaned, saved after clean runs, shown when the route changes, never fed back into a prompt."""

import asyncio
import json
import pathlib
from datetime import date

from sonar import pipeline
from sonar.config import Settings
from sonar.db import queries
from sonar.db.tables import make_engine
from sonar.navigation import dom
from sonar.navigation.route_notes import MAX_NOTE_CHARS, clean_note, save_route_note

# the fixtures of the watermark tests: a fake browser, extraction and navigator
from test_watermark import SITE, FakeNavigator, engine, wire  # noqa: F401  (fixtures)

DAY1, DAY2 = date(2026, 10, 13), date(2026, 10, 14)


def test_a_note_is_cleaned_of_addresses_control_characters_and_length() -> None:
    raw = "Clicked 'Opportunities'\x00 then\nsee https://x.example.com/list?token=SECRET123 and  a table."
    cleaned = clean_note(raw)
    assert "SECRET123" not in cleaned and "https" not in cleaned and "\n" not in cleaned and "\x00" not in cleaned
    assert cleaned == "Clicked 'Opportunities' then see [address removed] and a table."
    long = clean_note("word " * 200)
    assert len(long) <= MAX_NOTE_CHARS + 3 and long.endswith("...")
    assert clean_note(None) == clean_note("   ") == ""


def test_the_first_note_is_saved_a_same_note_is_not_repeated_and_a_change_is_reported(tmp_path) -> None:
    first = save_route_note(tmp_path, "demo", "Menu 'Open bids', a table of 58 rows.", DAY1)
    assert first.status == "first" and "route note saved" in first.describe()
    same = save_route_note(tmp_path, "demo", "menu 'open bids',  a TABLE of 58 rows.", DAY2)  # spacing and case do not count
    assert same.status == "unchanged" and same.describe() is None
    changed = save_route_note(tmp_path, "demo", "Menu 'Tenders', then sort by date; cards, not a table.", DAY2)
    assert changed.status == "changed"
    assert "route changed since 2026-10-13" in changed.describe() and "Open bids" in changed.describe()
    lines = (tmp_path / "demo.txt").read_text(encoding="utf-8").splitlines()
    assert lines == ["2026-10-13 | Menu 'Open bids', a table of 58 rows.", "2026-10-14 | Menu 'Tenders', then sort by date; cards, not a table."]


def test_an_empty_note_writes_nothing_and_a_broken_folder_never_raises(tmp_path) -> None:
    assert save_route_note(tmp_path, "demo", "  ", DAY1).status == "skipped"
    assert not (tmp_path / "demo.txt").exists()
    blocker = tmp_path / "a-file"
    blocker.write_text("not a folder")
    assert save_route_note(blocker, "demo", "A note", DAY1).status == "skipped"  # cannot make the folder: courtesy only


def test_the_site_name_cannot_escape_the_folder(tmp_path) -> None:
    save_route_note(tmp_path, "../../evil", "note", DAY1)
    assert [p.name for p in tmp_path.iterdir()] == [".._.._evil.txt"] or all(p.parent == tmp_path for p in tmp_path.iterdir())
    assert not (tmp_path.parent / "evil.txt").exists()


def test_the_agents_first_answer_may_carry_a_note_and_it_is_optional() -> None:
    with_note = dom.parse_agent_page(json.dumps({"links": [], "has_more": False, "route_note": "Menu, then a table."}))
    assert with_note.route_note == "Menu, then a table."
    assert dom.parse_agent_page(json.dumps({"links": []})).route_note is None


def test_the_prompt_asks_for_a_note_without_addresses_or_names() -> None:
    prompt = dom.SYSTEM_PROMPT
    assert "route_note" in prompt and "FIRST step only" in prompt and "names of people" in prompt


def test_the_note_is_never_used_to_build_a_prompt() -> None:
    """The note is for people. The code that builds the agent's task and options must not read it."""
    import inspect

    from sonar import agent_runner

    assert "route_note" not in inspect.getsource(dom.agent_collector)  # builds every step's task
    assert "route_note" not in inspect.getsource(agent_runner)  # runs the agent


class NotedNavigator(FakeNavigator):
    route_note = "Menu 'Open bids', a table of 2 rows."


def collect(engine, tmp_path, navigator):
    settings = Settings(route_notes_dir=str(tmp_path))
    run_id = queries.start_run(engine)
    return asyncio.run(pipeline.collect_site(SITE, settings, engine, run_id)), settings


def test_a_clean_run_keeps_its_route_note(engine, wire, tmp_path) -> None:  # noqa: F811
    wire["navigator"] = NotedNavigator([("a", "2026-10-09"), ("b", "2026-10-08")])
    outcome, settings = collect(engine, tmp_path, wire["navigator"])
    assert outcome.stored == 2 and "route note saved" in (outcome.route_message or "")
    assert (settings.route_notes_path / "demo.txt").exists()


def test_a_run_that_was_not_clean_keeps_no_route_note(engine, wire, tmp_path) -> None:  # noqa: F811
    wire["navigator"] = NotedNavigator([("a", "2026-10-09")], reached_cutoff=False)  # the page cap ended the run
    outcome, settings = collect(engine, tmp_path, wire["navigator"])
    assert outcome.route_message is None
    assert not (settings.route_notes_path / "demo.txt").exists()


def test_a_navigator_without_a_note_is_fine(engine, wire, tmp_path) -> None:  # noqa: F811
    wire["navigator"] = FakeNavigator([("a", "2026-10-09")])
    outcome, settings = collect(engine, tmp_path, wire["navigator"])
    assert outcome.stored == 1 and outcome.route_message is None
