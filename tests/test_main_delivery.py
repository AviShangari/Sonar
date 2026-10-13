"""A run that crashes must still produce its report and email, so it never looks like a quiet day."""

import asyncio

import pytest

import importlib

main = importlib.import_module("sonar.main")
from sonar.config import Settings
from sonar.db import queries
from sonar.db.tables import RunRow, make_engine
from sonar.sites import SiteConfig

SITE = SiteConfig(name="demo", start_url="https://bids.example.com/", allowed_hosts=["bids.example.com"])


@pytest.fixture()
def wired(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    engine = make_engine("sqlite://")
    settings = Settings(
        database_url="sqlite://", email=True, smtp_host="smtp.example", email_from="a@example.com",
        email_to="b@example.com", report_dir=str(tmp_path), summaries=False, lock_dir=str(tmp_path),
    )
    sent: list = []
    monkeypatch.setattr(main, "load_settings", lambda: settings)
    monkeypatch.setattr(main, "load_sites", lambda: [SITE])
    monkeypatch.setattr(main, "make_engine", lambda url: engine)
    monkeypatch.setattr(main, "send_report", lambda report, s: sent.append(report))
    return engine, sent, tmp_path


def test_a_crashed_run_is_marked_failed_and_still_emailed(wired, monkeypatch) -> None:
    engine, sent, tmp_path = wired

    async def boom(*args, **kwargs):
        raise KeyboardInterrupt  # not an Exception: the case the crash path exists for

    monkeypatch.setattr(main, "run_site", boom)
    with pytest.raises(KeyboardInterrupt):
        asyncio.run(main.run(None, None, None))
    assert len(sent) == 1 and sent[0].status == "failed"
    assert list(tmp_path.glob("report-run-*.txt"))  # the report files were written too
    from sqlalchemy.orm import Session

    with Session(engine) as session:
        assert session.get(RunRow, sent[0].run_id).status == "failed"


def test_a_delivery_problem_does_not_hide_the_original_crash(wired, monkeypatch) -> None:
    engine, sent, _ = wired

    def broken_send(report, settings):
        raise RuntimeError("smtp down")

    async def boom(*args, **kwargs):
        raise ValueError("browser died")

    monkeypatch.setattr(main, "send_report", broken_send)
    monkeypatch.setattr(main, "run_site", boom)
    with pytest.raises(ValueError, match="browser died"):  # not "smtp down"
        asyncio.run(main.run(None, None, None))


def test_a_normal_run_with_no_matches_still_sends_one_email(wired, monkeypatch) -> None:
    engine, sent, _ = wired

    async def quiet(site, settings, engine, matcher, run_id, days, max_new):
        return main.SiteOutcome(site.name, decisions=[])

    monkeypatch.setattr(main, "run_site", quiet)
    assert asyncio.run(main.run(None, None, None)) == 0
    assert len(sent) == 1 and sent[0].status == "ok" and sent[0].matches == []


def test_a_run_is_skipped_with_an_alert_while_another_run_holds_the_lock(wired, monkeypatch) -> None:
    engine, sent, tmp_path = wired
    alerts: list = []
    started: list = []

    async def should_not_run(*args, **kwargs):
        started.append(True)

    monkeypatch.setattr(main, "send_alert", lambda settings, subject, body: alerts.append(subject))
    monkeypatch.setattr(main, "run_site", should_not_run)
    with main.run_lock("run", tmp_path):  # another run is in progress
        assert asyncio.run(main.run(None, None, None)) == 2
    assert started == [] and sent == []  # no site was touched and no report was made
    assert alerts and alerts[0].startswith("[RUN SKIPPED]")
    from sqlalchemy.orm import Session

    with Session(engine) as session:
        assert session.query(RunRow).count() == 0  # a skipped run does not even create a run record
