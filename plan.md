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
- **Not yet built in the DOM stream:** the title + client + date fallback ID, concurrent workers, retry of a failed posting, `--limit` in the live script is only a test cap (the main pipeline's `--max-new` is now safe, see the watermark below).
- Extraction cleanup merged (PR #8): generic boilerplate stripping, `posted_date`, stale-run cleanup.
- `feat/pipeline-matching` built (not yet merged): `python -m sonar.main` runs navigate, dedup, store, then keyword matching on cleaned text and stores a `decisions` row per listing. New: `config/settings.yaml` + `config.py`, `pipeline.py`, `main.py`, `navigation/factory.py`, `matching/{factory,run}.py`. Live run: 4 new stored, 13 known, 17 scored, 2 matches. Matching raw text would have flagged 16 of 17 (SAP appears in CanadaBuys boilerplate), so cleaning is essential.
- Keyword-in-boilerplate warnings are logged each run (CanadaBuys: the 'register in SAP Ariba' help sentence, found in 16 of 17 postings, is the one stripped line with a keyword). Known weak spot: one match ("SAP Business Network event") is the SAP platform named in tender instructions, not an SAP project. Keywords alone cannot tell the difference; this is the kind of case Jev should separate in demo 2.
- Pipeline matching (PR #9) and report files (PR #10) merged. Each run writes `data/reports/report-run-<id>.html/.txt`.
- `feat/summaries` (PR #11, merged): an agent with NO tools writes a 2 to 4 sentence summary for each match, from the cleaned stored text, saved in `listings.fields.summary`; shown in the HTML and text reports. Live: 12 summaries for run 7 in 63 s one at a time, 24 s with 4 agents at once (`summary_workers: 4`). A failed summary is skipped and never fails the run. Setting: `summaries: true`.
- Report rough edges to fix later: snippets can start or end mid-word, titles keep the site's suffix ("- Tender Notice | CanadaBuys"); many "SAP" matches are SAP Ariba / Business Network platform mentions, not SAP projects.
- Developer's email requirement: after the searches, send a PLAIN-TEXT body to one specific address, listing for each matched posting its name, summary, original link and number of keyword matches. SMTP assumed (developer's guess); details and recipient still to come.
- `feat/email-send` built (PR #12, merged): `report/send.py` (stdlib `smtplib`, STARTTLS on 587 or TLS on 465), `templates/email.txt`, `secrets.py` (password from `SONAR_SMTP_PASSWORD`), settings `email`, `smtp_host`, `smtp_port`, `smtp_username`, `email_from`, `email_to`, `email_snippets`. `main.py` sends after the report files when `email: true`; a failed send is printed and never fails the run. `scripts/send_report.py RUN_ID` emails a past run for testing. Unit tests use a fake SMTP server. First real test: Gmail to the developer's own address with an app password (not yet done).
- Email send works live (Gmail, app password in the gitignored `.env`). Email format revised after the first live email: one snippet, link under the title, only the summary indented, `Date posted` / `Date due` above the keyword counts, and `How to apply` from the posting. The same summary agent call now also reads the closing date and how to apply (`summarize/summaries.py`, `PostingInfo`); `scripts/summarize_run.py RUN_ID` reads an older run again. Live on run 7: 12 read; 3 closing dates certain, 9 marked unsure (the posting often shows several dates), which may be too cautious and needs tuning.
- **Headless fixed** (branch `fix/headless-canadabuys`): CanadaBuys's 403 came from the `HeadlessChrome` tag in the user agent, not from old vs new headless mode (both 403 with the tag, both 200 without). `navigation/session.py` now reads the installed Chromium's own user agent and drops "Headless" when `headless: true`. Live check: headless start page gives 200; a test covers the agent string. Full headless run on Windows passed (2026-10-12, scratch database, `--days 3 --max-new 3`): agent collected, 3 postings stored, 3 scored, 3 summarized, reports written. Linux/VM still untested.
- **Safe cutoff built** (PR #16, merged): the stop point is now a per-site watermark (new table `site_watermarks`), moved forward only by a clean collection. Not clean: a crash, the `--max-new` cap actually cutting collection short, a posting that failed to store or open, or the page cap ending collection before the cutoff. A run that is not clean leaves the watermark where it was; the next run re-reads down to it and dedup skips what is stored. An empty watermark means the `first_run_days` window. Tests: `tests/test_watermark.py`.
- **Always-send email built** (PR #17, merged): a run that crashes or is stopped now still writes its report and sends the email (status failed) before the error is re-raised; delivery problems never hide the original error. Subjects start with `[SOME SITES FAILED]` or `[RUN FAILED]` when a run is not ok. Not covered: a run that never starts at all (needs the "no successful run in about 26 hours" alert), and a hard kill of the process.
- **Run lock built** (PR #18, merged): `runlock.py` takes an operating-system lock on `data/run.lock` (`msvcrt` on Windows, `fcntl` on Linux, no new dependency) before anything else in a run. A second run exits with code 2, creates no run record, and emails `[RUN SKIPPED]` when email is on. The OS frees the lock when a process dies, so a crash cannot leave it stuck (tested with a real second process that is killed). Stale-run cleanup now happens inside the lock. The lock has a name, so per-site locks can be added for parallel sites.
- **Failure screenshots built** (branch `feat/failure-screenshot`, not yet merged): `navigation/screenshots.py` (`FailureShots`) saves a PNG to `data/screenshots/` (setting `screenshot_dir`, gitignored) when (a) a posting will not open (HTTP error or timeout; taken in `dom.py` before the tab closes), (b) a posting opened but could not be extracted or stored, (c) the site as a whole fails (login or list pages; the agent's last tab is captured, then the error still propagates). File name: `UTC time-site-step-jobid-counter.png`. Max 10 per site per run. Taking a screenshot never raises. Screenshots are for people only: not in prompts, reports or emails (a logged-in page can show private data). Not covered: the DOM agent's own failed clicks inside a list step (it only reads), and cleaning up old screenshots (add a retention rule at deployment). Tests: `tests/test_screenshots.py`.
- **Email table built** (branch `feat/email-table`, not yet merged; done before the cost cap at the developer's request): the summary agent now also reads solicitation number, supply arrangement (TBIPS, THS ...) and issuing department (`summarize/summaries.py`; the description is now 2 to 4 sentences, as a statement of work). The email is an HTML table plus a plain-text copy (`templates/email.html`, `email.txt`, multipart message in `report/send.py`) with columns: Date posted, Supply arrangement, Title (hyperlink to the posting), Solicitation number, Description, Issuing department, Due date. Snippets, keyword counts and "How to apply" are no longer in the email (ranking by keywords still orders the rows; `how_to_apply` is still read and shown in the report files). The `email_snippets` setting is removed. Listings read before this change have no new fields (cells show "not available"); `scripts/summarize_run.py RUN_ID` reads them again. Not changed: the HTML/text report files in `data/reports/` do not show the new fields yet. Tests: `tests/test_email.py`, `tests/test_summaries.py`.
- **Time zone** (same branch, `feat/email-table`): new setting `timezone: America/Toronto` (validated at startup). The report's run time is shown in Toronto time and written out in full (`October 13, 2026, 09:00 EDT`, daylight saving handled; this is the email heading and the report files) instead of UTC, and the first-run date window uses today's date in Toronto instead of the machine's clock (a UTC VM would otherwise get the wrong day in the evening). The database still stores UTC. Added the `tzdata` dependency (Windows has no time zone database). Screenshot file names also use Toronto time. Not changed: listing dates from sites are taken as shown (they are dates, not times); `scripts/run_dom_navigator.py` (a dev script) still uses the machine's date.
- **API key auth built and live-checked** (branch `feat/openrouter-auth`, not yet merged; moved ahead of the cost cap at the developer's request, because the cost cap can then use dollars): new `llm.py` (the only place auth and models are set), setting `auth: subscription | openrouter`, `OPENROUTER_API_KEY` read in `secrets.py`, per-role models in settings (`navigation_model: openai/gpt-6.1-sol` with `navigation_effort: medium`; `summary_model: anthropic/claude-sonnet-5.5`). `agent_runner.isolated_options` takes the agent's role and gets model, effort and auth environment from `llm.py`; isolation (no settings, no tools, no connectors) is unchanged and tested. `main` stops at startup if `auth: openrouter` and the key is missing. `settings.yaml` stays on `subscription` until the live check passes. **Live results (2026-10-13):** `scripts/check_openrouter.py` passed (both models answer; GPT-6.1 Sol made a tool call and used the result; under half a cent). A real headless run on a scratch database with `auth: openrouter` (`--days 3 --max-new 3`) passed: the GPT-6.1 Sol agent read two list pages (50 rows each) through `browser(code)`, 3 postings stored, 3 summaries by Sonnet 5.5 with the new fields filled and none flagged unsure. Harmless noise: the Claude Code CLI logs `unrecognized_model` when it titles a session with a model name it does not know. Not yet known: whether `navigation_effort` really reaches GPT; cost per run (check the OpenRouter dashboard). Open question kept: whether to keep the Agent SDK/Claude Code CLI at all (see decision log); developer chose to check first. Summaries as a plain API call (no agent) is still recommended. Tests: `tests/test_llm.py`.
- **Next steps (in this order), then scheduler:** (1) done; (2) done; (3) done; (4) API key auth (built, live check pending; see above); (5) cost cap per run (counts of agent calls, postings and summaries; with the key, a dollar cap per agent call is also possible through the SDK's `max_budget_usd`); (6) scheduler last (it must refuse to start unless `auth: openrouter`), because it depends on where the app runs (laptop: Task Scheduler; VM: cron; Docker: the host starts it; ask which). Also needed later: a "no successful run in about 26 hours" alert that works even when a run crashes or never starts, and the parallel-sites step. Second site (ideally with a login) is on hold (developer's account problems). Vision stream on hold. Later: company SMTP (config change only) or Microsoft Graph.

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
- Cost cap per run; run lock per site; screenshot on navigation failure (built).

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
- [x] Report files: `report/{build,render}.py` + `templates/report.{html,txt}`; written to `data/reports/report-run-<id>.html/.txt` after every run (`python -m sonar.main`, or `scripts/make_report.py RUN_ID` for a past run)
- [x] Sending: plain-text email to one address after each run (SMTP, `feat/email-send`); Teams or Graph later if needed. Needs SMTP host, sender, recipient (see Open questions)

### Demo 2: Jev matching
- [ ] Jev matcher behind the matcher interface
- [x] Summaries for matches only (`summarize/summaries.py`, tool-less agent via `agent_runner.run_text_agent`)
- [ ] Labeled set (50 to 100 listings, labeled by decision-makers)
- [ ] Compare keyword vs Jev: precision and recall

### Navigation
- [x] Navigator interface + guards + session handling
- [x] DOM stream on one site (CanadaBuys, first page only; see "Not yet built" above)
- [x] DOM stream: pagination with an early stop by posted date (list-row date stored in `fields.listed_date`; code decides, agent only reads rows and clicks "load more")
- [x] Extraction cleanup: generic boilerplate stripping (`extraction/boilerplate.py`: lines in at least 90% of a site's postings, needs 15+ postings; full text stays stored, cleaned text computed on read), posting's own `posted_date` in `fields`, stale `running` runs marked failed (`fail_stale_runs`, 2 hours)
- [ ] **Second site, ideally with a login (ON HOLD: account problems; was priority ahead of scheduler and Azure work):** prove the generic navigator, extraction and summaries on a site other than CanadaBuys. Needs: login step done by our code (Playwright fills the form, saves and reuses the session; agent never types), per-site credentials in `.env` via `secrets.py`, session-expiry handling (re-login once, then stop the site and alert), stop-and-alert on 2FA or CAPTCHA, the site's hosts added to the guard allowlist. First a dry run on the real site, then build the login around what it actually does, then an end-to-end run to the email
- [ ] Vision stream on the same site
- [ ] Compare streams: listings found, duplicate clicks, misclicks, time and cost per listing

### Hardening and deployment
- [x] Safe cutoff: per-site watermark moved only by a clean collection (`fix/safe-cutoff`)
- [x] Always send the email (even with zero matches), failed sites at the top (`feat/always-email`: zero matches and failed sites were already handled; added the missing crash path via `main.deliver`, and clearer subjects `[SOME SITES FAILED]` / `[RUN FAILED]`)
- [x] Run lock (`feat/run-lock`): one whole-run lock, `runlock.py`
- [x] Screenshot on navigation failure (`feat/failure-screenshot`)
- [ ] Cost cap per run
- [ ] **Parallel sites** (several sites at once in one run; needs: a separate browser, CDP port and profile folder per site; SQLite write contention handled (wait time or one writer), moving to a bigger database later; `asyncio.gather` over sites with failures kept independent; summaries and the email still after all sites; per-site lock names; limits on total browsing agents and summarizers for subscription rate limits and site politeness, 2 to 3 workers per site)
- [ ] Monitoring and failure handling (see failure modes doc), including a "no successful run in ~26 hours" alert
- [ ] Scheduler (after the above; depends on where the app runs)
- [ ] Switch auth to an API key in `llm.py` (OpenRouter; built on `feat/openrouter-auth`, live check pending)
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
| 2026-10-09 | Report: built from the database by plain code (no agent), rendered with Jinja2 to a self-contained HTML file and a plain-text file, one pair per run. Ranking: more different keywords, then more total mentions, then newest. Each match shows its first two snippets. Posting text is untrusted: HTML is escaped, only http(s) links are emitted, highlighting works on escaped text. Delivery is separate from the report so email and Teams can be added without touching it |
| 2026-10-11 | Email requirement: plain-text body to a specific address after each run; per posting: name, summary, original link, number of keyword matches. SMTP (stdlib `smtplib`, no new dependency) with host, port, sender and recipient in `config/settings.yaml` and the password only from an environment variable via `secrets.py` |
| 2026-10-11 | Summaries: written only for matches, from the boilerplate-stripped stored text (max 12,000 chars sent), by an agent with no tools (`setting_sources=[]`, no built-in tools, no claude.ai connectors, cwd outside the repo). Posting text is treated as data in the prompt; the answer is whitespace-collapsed and capped at 700 characters, stored in `fields.summary`, shown as plain text or escaped HTML. A failure leaves the listing without a summary and the report falls back to the keyword snippets |
| 2026-10-11 | Summary length: as long as the posting needs (prompt says usually 3 to 8 sentences, concise), safety cap 1,800 characters. Summaries run with up to `summary_workers` (default 4) agents at once through an asyncio semaphore; each agent is its own process, so raise the number carefully (memory, subscription rate limits). Two of twelve live summaries reached the cap, so the cap may need raising |
| 2026-10-11 | Email content per matched posting: title, summary, original link, keyword counts shown both ways (total mentions and number of different keywords, plus the keywords by name with their counts, e.g. `SAP (3), Project Management (2)`), and ONE keyword snippet (changed from 2 after the first live email) followed by "(+N more)"; link directly under the title, everything flush left except the indented summary. Setting `email_snippets: true` turns the snippets off. Falls back to the snippets alone when a posting has no summary |
| 2026-10-11 | Email test setup: Gmail (`smtp.gmail.com:587`, app password) from the developer's own address to itself. Moving to the company server later is a settings change only. The password lives only in `SONAR_SMTP_PASSWORD`, set in the gitignored `.env` (or the environment), read in `secrets.py`. `email` is off by default; a failed send never fails a run |
| 2026-10-11 | Closing date and how-to-apply are read by the summary agent, not by fixed field names, because every site words them differently. Safeguards in code: the agent must quote the exact words stating the date; the date is trusted only if it is a valid date, the agent says it is certain, and the quote really appears in the posting and contains the year. Otherwise the email shows `<date> (unsure, please verify)`, or `not found, please verify` when there is none. Listings summarized before this change are read again by `scripts/summarize_run.py` (the skip test is the stored `closing_unsure` field). Found on the Azure posting: the body kept the original closing date (2026-09-29) while the header showed the amended one (2026/10/27); the prompt now says the top summary block overrides older body dates, and never to use the publication, amendment or start date |
| 2026-10-12 | Second site put on hold (developer's user account has problems). Headless fix done first: the 403 was the user agent's "HeadlessChrome" tag, so headless launches now use the browser's own user agent minus "Headless" (no hardcoded version). Probe results: old and new headless both 403 with the tag, both 200 without |
| 2026-10-13 | Safe cutoff: the collection stop point is a stored per-site watermark (`site_watermarks`), not the newest stored `listed_date`. It advances (to the newest stored date, never backward) only after a clean collection: no crash, not cut by `--max-new`, no failed store or open, and the cutoff was reached rather than the page cap. Unclean runs still keep what they stored; the next run re-reads down to the old watermark and dedup skips known IDs. Matching is not part of "clean" (it catches up on the next run). No seeding of existing databases: with no watermark the `first_run_days` window applies once. Supersedes the 2026-10-09 "cutoff stays the newest stored site date" row |
| 2026-10-13 | Order for making daily runs reliable: safe cutoff, always-send email, run lock, screenshot on failure, cost cap, API key, then the scheduler last (it depends on the hosting choice and is a thin timer around `python -m sonar.main`) |
| 2026-10-13 | Run lock: an OS file lock, not a database row, because the OS releases it when the process dies (a database row would block the next run until it went stale). One lock for the whole run for now, since summaries and the email work on the whole run; the helper takes a name so per-site locks fit the planned parallel sites. The lock is taken before any run record is made, so a skipped run leaves no trace except the alert email |
| 2026-10-13 | Future architecture: several sites in parallel in one run. The whole-run lock still works (one process holds it). Parallel sites need their own step (see Build order > Hardening): browser/port/profile per site, SQLite write handling, concurrency limits |
| 2026-10-13 | Failure screenshots: taken by a small helper that never raises (a failed screenshot must not hide the real error), capped at 10 per site per run, written to gitignored `data/screenshots/`. Three trigger points: posting will not open, posting will not store, whole-site failure. The visible part of the page only (not full page, to keep files small and fast). Never shown to agents or put in reports/emails because logged-in pages can hold private data |
| 2026-10-13 | Email format changed from plain text to an HTML table with a plain-text fallback (developer's request; a plain-text table wraps badly). Supersedes the 2026-10-11 plain-text, keyword-counts and snippet rows. New summary fields are checked against the posting like the closing date: solicitation number and department must appear verbatim in the posting text, the supply arrangement needs a quoted phrase that appears in it; a failed check keeps the value and shows "(unsure, please verify)". "None/N/A" arrangements show "N/A". Listings count as read in the current format when `fields.unverified` exists |
| 2026-10-13 | Time zone: America/Toronto, a setting; the database keeps UTC and only display and "today" use it. `tzdata` added as a dependency (approved by the developer) because `zoneinfo` needs it on Windows and slim Docker images. The scheduler must also fire in Toronto time (decide with the scheduler step) |
| 2026-10-13 | Auth: OpenRouter API key (developer's choice) instead of Anthropic or Foundry, behind `auth: subscription | openrouter` so development can keep using the subscription. Models pinned per role: GPT-6.1 Sol (medium effort) for navigation, Claude Sonnet 5.5 for summaries. The risk of a non-Claude model inside the Claude Code CLI harness was checked live and passed (see Current status). Page text now passes through OpenRouter and the model providers, so postings are only public tender text (no login pages) until the data terms are checked. Reordered: API key before the cost cap. Previously the model was unpinned (SDK default, found to be Opus 5.5) |
| 2026-10-13 | Agent SDK question: with an API key the original reason for the Agent SDK (use the subscription login) is gone; it still provides the agent loop, `max_turns` and the tested isolation. Alternative: direct OpenRouter calls (plain call for summaries, a small own loop for navigation). Developer chose to run the live check first; it passed, so the SDK stays for navigation for now. Revisit if navigation proves flaky or the Claude Code CLI overhead matters | 
| 2026-10-11 | Priority: before scheduling or deploying, prove the agent on a second site, preferably one with a login (login by our code only, session expiry and 2FA handling as in Failure handling). CanadaBuys alone does not show the navigator is generic. Check each site's terms allow automated read-only access before building |

## Notes for agents on this machine (Windows)

- `uv` and `gh` are not on PATH. Run Python with `.\.venv\Scripts\python.exe` (or activate `.venv`); `PYTHONPATH` is not needed because the project is installed editable in `.venv`.
- Long bash heredocs failed to parse; create files with the Write/Edit tools.
- The developer merges PRs on GitHub themselves. Commit and push only when asked.

## Branching

- `main` is always working code.
- One branch per build step, named by type: `feat/keyword-matcher`, `feat/dom-navigator`, `fix/session-expiry`, `docs/readme`.
- Merge to `main` through a pull request once tests pass, then delete the branch.
- Mark demo-ready versions with tags on `main`: `demo-1-keywords`, `demo-2-jev`.
- No long-lived branch per version: keyword vs Jev and vision vs DOM are config switches, not separate code.

## Open questions

- [x] Cutoff safe against capped or failed runs: done with a watermark (see decision log, 2026-10-13)
- [x] CanadaBuys returned 403 to headless Chromium: fixed by removing the "Headless" tag from the user agent. Full headless run passed on Windows; still to confirm on the VM (Linux)
- [ ] Second site: which one, does it need a login, do we have an account and permission to automate it (terms of use), does it use 2FA or a CAPTCHA?
- [ ] Azure VM confirmed?
- [ ] Approved LLM provider for production (Anthropic API, Microsoft Foundry, other)?
- [ ] Sending: company addresses are `@xyzitgroup.com`. SMTP or Microsoft Graph depends on the company's Microsoft 365 setup (SMTP sign-in is often disabled by IT); to confirm. Recipients (a person, or an agent/bot in Microsoft Teams) and frequency: developer will say later; Teams delivery would be a separate channel
- [ ] Vision stream text extraction: code (recommended) or screenshots?
- [ ] Threshold trade-off: favor recall over precision?
- [ ] Final name
