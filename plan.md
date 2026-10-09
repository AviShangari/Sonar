# Plan

Full design: [Listing Matcher: Design Summary](https://claude.ai/code/artifact/44639852-e4a8-4495-867a-a9302a26f957)
Failure handling: [Listing Matcher: Failure Modes and Handling](https://claude.ai/code/artifact/02861676-cab1-47d7-b843-46f16b5e8e4a)

These links are private claude.ai pages that coding agents cannot open. Everything an agent needs is in this file and `AGENTS.md`.

## Current status

- Repo created, `uv init` done, dependencies installed, Chromium installed, context files committed.
- SDK verified on subscription login; isolation settings proven by `scripts/check_sdk.py` (merged to `main`, PR #1).
- Keyword matcher and fixtures merged to `main` (PR #2).
- Browser Use CLI spike merged to `main` (PR #3); design documented in `AGENTS.md`.
- Database schema merged to `main` (PR #4): `db/tables.py`, `db/queries.py`, tests in `tests/test_db.py`.
- Navigator base merged to `main`: `config/sites.yaml`, `sites.py`, `navigation/{base,guards,urls,session}.py`, `scripts/check_site.py`. `base.py` is only the interface; no navigator implements it yet. Login and session-expiry handling are not built (CanadaBuys needs no login).
- First real site: CanadaBuys (see `config/sites.yaml`). Email report is deferred until collection and extraction work.
- DOM navigator built on `feat/dom-navigator` (not yet merged): `agent_runner.py`, `navigation/dom.py`, `extraction/posting.py`, `scripts/run_dom_navigator.py`, tests in `tests/test_dom_navigator.py`. Live on CanadaBuys: the agent reads the list (50 rows with dates), clicks "load more" when code asks, and code stops at the first row dated before the cutoff (newest stored `listed_date`, or today minus `first_run_days` on an empty database). New postings are opened, extracted and stored; known IDs are skipped unopened.
- **Not yet built in the DOM stream:** the title + client + date fallback ID, concurrent workers, screenshot on failure, retry of a failed posting, headless mode on CanadaBuys (it returns 403, see Open questions). `--limit` in the live script is only a test cap: a capped run still moves the cutoff to the newest stored date, so older unseen postings would be skipped on the next run. Do not use `--limit` for real runs.
- Extraction cleanup merged (PR #8): generic boilerplate stripping, `posted_date`, stale-run cleanup.
- `feat/pipeline-matching` built (not yet merged): `python -m sonar.main` runs navigate, dedup, store, then keyword matching on cleaned text and stores a `decisions` row per listing. New: `config/settings.yaml` + `config.py`, `pipeline.py`, `main.py`, `navigation/factory.py`, `matching/{factory,run}.py`. Live run: 4 new stored, 13 known, 17 scored, 2 matches. Matching raw text would have flagged 16 of 17 (SAP appears in CanadaBuys boilerplate), so cleaning is essential.
- Keyword-in-boilerplate warnings are logged each run (CanadaBuys: the 'register in SAP Ariba' help sentence, found in 16 of 17 postings, is the one stripped line with a keyword). Known weak spot: one match ("SAP Business Network event") is the SAP platform named in tender instructions, not an SAP project. Keywords alone cannot tell the difference; this is the kind of case Jev should separate in demo 2.
- **Next step:** merge `feat/pipeline-matching`, then `feat/email-report` (vision stream on hold by the developer's choice).

## How we work

- One build step at a time; the developer is learning agent building, so explain choices briefly.
- `main` always works. Each build step is a short-lived branch merged back to `main` (see Branching below).
- Demos are tagged on `main` (`demo-1-keywords`, `demo-2-jev`). Both versions live in one codebase, switched by `config/settings.yaml`.

## Demo plan

1. **Demo 1, keywords:** client-provided keyword list; every keyword is checked; the report shows which keywords matched with snippets.
2. **Demo 2, Jev:** Jev decides fit with a probability; an LLM summarizes matches. Show keyword vs Jev precision and recall on the same labeled listings.
3. Stakeholders want to watch human-like browsing in demos: use the vision stream, headed browser, on the developer's laptop. Daily runs use the DOM stream, headless.

## Evaluation (summary)

- **Collection:** coverage against a hand count on one site; duplicate clicks; misclicks; run success rate. Compare both streams on the same window.
- **Extraction:** field accuracy on 20 to 30 listings checked against the live page.
- **Matching:** 50 to 100 listings labeled pursue/skip by decision-makers (30 double-labeled). Measure recall, precision, calibration, review-band size. Choose the threshold from the curve, biased toward recall (to confirm).
- **Summaries:** unsupported claims vs raw text, target zero.
- **Operations:** cost and time per listing and per run; failure rate by type.
- The labeled set doubles as a regression test before changing prompts, keywords, Jev questions or model versions.

## Failure handling (essentials)

- Every failure ends in: retry, skip and log, or stop the site and alert. Never a silently empty or wrong report.
- State lives in the database; steps are idempotent; a crashed run resumes.
- Session expired (redirect to login): re-login once, re-save, retry; then stop the site.
- 2FA or CAPTCHA: stop and alert a human. Account warning or restriction: stop the site immediately.
- A site returning zero listings is treated as a failure, not a quiet day.
- Rate limits: back off and lower concurrency. Site down: retry 3 times, then skip and note it in the report.
- Vision: seen ID after a click means close the tab; duplicate titles are disambiguated with a second visible detail; misclick retries once.
- DOM: normalize URLs; no ID in URL means fall back to title + client + posted date.
- Extraction: empty text means fall back to body text and flag the site; expand "Read more" first; store missing fields as null.
- Jev unavailable: mark listing unscored, never a non-match. Low confidence goes to a "needs review" section.
- Always send the email, even with zero matches, and list failed sites at the top.
- Cost cap per run; run lock per site; screenshot on navigation failure.

## Build order

### Setup
- [ ] Repo, `uv init`, dependencies, Playwright Chromium
- [x] Confirm the Agent SDK runs with the subscription login (`scripts/check_sdk.py`)
- [x] Confirm runtime agents load no filesystem settings (`CLAUDE.md`, `~/.claude/`)

### Demo 1: keyword matching
- [x] Test data: 13 synthetic fixture listings in `tests/fixtures/listings.yaml` (real labeled set comes later from the navigator)
- [x] Database schema (listings, decisions, runs)
- [x] Keyword matcher (whole-word, case-insensitive, phrases, matched-keyword snippets)
- [x] Pipeline wiring: one run stores postings and keyword decisions (`python -m sonar.main`)
- [ ] Email report template and sending

### Demo 2: Jev matching
- [ ] Jev matcher behind the matcher interface
- [ ] Summaries for matches only
- [ ] Labeled set (50 to 100 listings, labeled by decision-makers)
- [ ] Compare keyword vs Jev: precision and recall

### Navigation
- [x] Navigator interface + guards + session handling
- [x] DOM stream on one site (CanadaBuys, first page only; see "Not yet built" above)
- [x] DOM stream: pagination with an early stop by posted date (list-row date stored in `fields.listed_date`; code decides, agent only reads rows and clicks "load more")
- [x] Extraction cleanup: generic boilerplate stripping (`extraction/boilerplate.py`: lines in at least 90% of a site's postings, needs 15+ postings; full text stays stored, cleaned text computed on read), posting's own `posted_date` in `fields`, stale `running` runs marked failed (`fail_stale_runs`, 2 hours)
- [ ] Vision stream on the same site
- [ ] Compare streams: listings found, duplicate clicks, misclicks, time and cost per listing

### Hardening and deployment
- [ ] Monitoring and failure handling (see failure modes doc)
- [ ] Switch auth to API key (or Microsoft Foundry) in `llm.py`
- [ ] Deploy to Azure VM (to be confirmed); supervised first run
- [ ] More sites; optimize memory, tokens and cost

### Later
- [ ] Feedback endpoint and email vote links (needs a public URL)
- [ ] Far future, optional: CanadaBuys open data as a fallback source

## Decision log

| Date | Decision |
| --- | --- |
| 2026-10-08 | Two navigation streams (vision for demo, DOM for daily runs) behind one interface |
| 2026-10-08 | Jev for match decisions; LLM summaries for matches only; matching on raw text |
| 2026-10-08 | Database stores raw data for every listing; dedup by (site, job ID) |
| 2026-10-09 | CanadaBuys open data moved to far future, optional |
| 2026-10-09 | Build locally first, deploy to an Azure VM later (VM to be confirmed) |
| 2026-10-09 | Feedback links deferred until deployment |
| 2026-10-09 | Client requirement: keyword matching demoed first, Jev second; every keyword checked |
| 2026-10-09 | Python + Claude Agent SDK with custom Playwright tools |
| 2026-10-09 | Subscription login for development; API key before deployment |
| 2026-10-09 | No hand-copied listings: synthetic fixtures test matcher rules; the real labeled set comes from the navigator's stored listings |
| 2026-10-09 | Keyword rules: phrase words may be separated by any whitespace; overlapping keywords (Microsoft / Microsoft Dynamics) are both reported; "Robotic Process Automation (RPA)" split into two keywords |
| 2026-10-09 | DOM stream uses the Browser Use CLI (pinned 0.13.11, separate `uv tool`) attached to a Playwright-launched dedicated browser over CDP; agent's only tool is a validated `browser(code)`, no Bash. The CLI runs arbitrary Python as the user, so the code filter is best effort; browser-level guards (non-GET block, URL allowlist) and OS isolation are the firm limits. Telemetry/update checks off; login done by our code. Proven by `spike/browser-use-cli` |
| 2026-10-09 | Runtime agents: `setting_sources=[]`, `strict-mcp-config` + `ENABLE_CLAUDEAI_MCP_SERVERS=false` (claude.ai connectors like Gmail otherwise attach), cwd in a temp dir. Built-in skills/plugins still appear; they ship with the CLI |
| 2026-10-09 | Schema: `listings` unique on (site, job_id); `decisions` are separate rows per matcher so keywords and Jev can be compared on the same listing; `matched = NULL` means unscored, never a non-match; `fields` and `details` are JSON columns so they can grow without migrations |
| 2026-10-09 | Priority change: navigation and extraction before the email report; email deferred |
| 2026-10-09 | Navigation is generic: the agent works out each site's layout itself. `sites.yaml` holds only name, start URL, allowed hosts, optional hint, login flag. No per-site selectors. Extraction and the job-ID rule (`urls.job_id_from_url`, fallback title + client + posted date) are generic code |
| 2026-10-09 | First site is CanadaBuys with a status/50-per-page filtered URL. Finding: a generic "ID-like link" rule also catches links outside the results list (award notices on the earlier URL; 52 ID-like links vs 50 results on the current one), so the agent must choose the result links |
| 2026-10-09 | The guard blocks a harmless ad-tracker frame (demdex.net) on CanadaBuys; expected, no action |
| 2026-10-09 | DOM agent only returns links: its final answer is JSON `{"links":[{title,url}]}`, treated as untrusted (http(s) only, allowed hosts only, normalized, deduped). Our code opens each posting in a new tab, so the agent never touches posting text. Zero usable links raises an error (not a quiet day) |
| 2026-10-09 | The `browser(code)` tool lives inside `agent_runner.py` (the only SDK/CLI place), so `navigation/tools.py` from the layout is not needed |
| 2026-10-09 | Real job IDs on CanadaBuys look like `cb-428-37324676` and `ws5897300910-doc5897312809` (last path segment); the generic rule works. The guard also blocks CanadaBuys' POST download counters on posting pages; harmless |
| 2026-10-09 | Async tests use `asyncio.run` inside plain tests; no `pytest-asyncio` dependency added |
| 2026-10-09 | Collection should stop by reaching already-stored postings, not by a fixed count. Safer than one anchor posting: stop at a posted date older than our newest stored one (decided: use dates), since a single anchor posting may close or be removed; fall back to N seen IDs in a row if a list page shows no date. Needs newest-first sorting. Planned under Navigation |
| 2026-10-09 | Stop rule built: cutoff = newest stored `fields.listed_date` for the site, else today minus `first_run_days`. Rows on the cutoff day are kept (dedup skips known ones); the first older row ends collection. Dates come from the list row (CanadaBuys shows the open/amendment date, day only, sorted newest first with `order=pub_ifnot_amended&sort=desc`). A list that is not newest first is an error; with no dates at all, collection stops at a page of only known IDs; `max_list_pages` (default 10) caps "load more" steps |
| 2026-10-09 | CanadaBuys paging is a "load more" link that appends rows to the same page; opening its URL directly returns 403, so the agent must click it. The code asks for one step at a time (open and read, or click once and return rows after the first N) |
| 2026-10-09 | The agent prompt includes a worked reading recipe, kept as code in `dom.py` and checked by a test against `code_check`, so the prompt cannot teach code the filter rejects. Without it the agent used 7+ calls and missed the all-caps "LOAD ..." link |
| 2026-10-09 | Cutoff stays the newest stored site date (`fields.listed_date`), not our own `first_seen_at`: the stop rule must compare against the dates shown on the list page, and a late first run or capped run would move a found-date cutoff past unseen postings. Considered and deferred: cutoff = earliest site date of the last fully completed run (uses `runs.status`), so a capped or failed run cannot advance it. Revisit before real scheduled runs |
| 2026-10-09 | Boilerplate in stored text is removed by a generic cross-posting rule (lines found in nearly all of a site's postings), not per-site selectors; raw HTML stays untouched. Done right after the DOM navigator merges |
| 2026-10-09 | Boilerplate settings: a line is boilerplate at 90% of a site's postings, and only when the site has 15+ stored postings (below that nothing is stripped; raised from 5, see the next row). Raw `posting_text` is never modified; matching reads the cleaned view. Two dates are kept per listing: `listed_date` (list row, drives the stop rule) and `posted_date` (read from the posting's own text, generic regex). Runs still `running` after 2 hours are marked failed at the next run start; a stopped script (including Ctrl+C) marks its own run failed |
| 2026-10-09 | Matching runs AFTER all of a site's new postings are stored, on every listing that has no decision from the current matcher (not only this run's). So boilerplate is measured on the freshest data, a first run works, a crash between storing and matching is finished by the next run, and a new matcher scores old listings. Runs end `ok`, `partial` (some sites failed) or `failed`, with failed sites listed in `runs.notes` for the report. Matchers and navigators are chosen by `config/settings.yaml` through two factories (`matching/factory.py`, `navigation/factory.py`) |
| 2026-10-09 | Guarding against lost keywords: stripping needs 15+ stored postings per site (was 5), so a small or lopsided sample cannot hide real content; early on we accept extra matches over lost ones. Every run also logs a WARNING for each stripped boilerplate line that contains a client keyword (keyword, how many postings, the line) so a human can confirm it really is boilerplate. Not done: letting the report show these warnings, and a way to exclude a line from stripping; add both if a real keyword is ever hidden |
| 2026-10-09 | Priority: vision stream on hold; email report next |

## Notes for agents on this machine (Windows)

- `uv` and `gh` are not on PATH. Run Python with `.\.venv\Scripts\python.exe`; for scripts set `$env:PYTHONPATH="src"` first.
- Long bash heredocs failed to parse; create files with the Write/Edit tools.
- The developer merges PRs on GitHub themselves. Commit and push only when asked.

## Branching

- `main` is always working code.
- One branch per build step, named by type: `feat/keyword-matcher`, `feat/dom-navigator`, `fix/session-expiry`, `docs/readme`.
- Merge to `main` through a pull request once tests pass, then delete the branch.
- Mark demo-ready versions with tags on `main`: `demo-1-keywords`, `demo-2-jev`.
- No long-lived branch per version: keyword vs Jev and vision vs DOM are config switches, not separate code.

## Open questions

- [ ] Before real scheduled runs: make the cutoff safe against capped or failed runs (earliest site date of the last completed run); see decision log
- [ ] CanadaBuys returns 403 to headless Chromium (headed works). Daily runs are meant to be headless on the VM: try Chromium's new headless mode / a normal user agent, or run headed under a virtual display
- [ ] Azure VM confirmed?
- [ ] Approved LLM provider for production (Anthropic API, Microsoft Foundry, other)?
- [ ] Email: Microsoft Graph or SMTP? Recipients and frequency?
- [ ] Vision stream text extraction: code (recommended) or screenshots?
- [ ] Threshold trade-off: favor recall over precision?
- [ ] Final name
