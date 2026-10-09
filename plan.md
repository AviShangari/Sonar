# Plan

Full design: [Listing Matcher: Design Summary](https://claude.ai/code/artifact/44639852-e4a8-4495-867a-a9302a26f957)
Failure handling: [Listing Matcher: Failure Modes and Handling](https://claude.ai/code/artifact/02861676-cab1-47d7-b843-46f16b5e8e4a)

These links are private claude.ai pages that coding agents cannot open. Everything an agent needs is in this file and `AGENTS.md`.

## Current status

- Repo created, `uv init` done, dependencies installed, Chromium installed, context files committed.
- SDK verified on subscription login; isolation settings proven by `scripts/check_sdk.py` (merged to `main`, PR #1).
- Keyword matcher and fixtures done on branch `feat/test-listings` (PR pending).
- **Next step:** Demo 1, database schema (listings, decisions, runs).

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
- [ ] Database schema (listings, decisions, runs)
- [x] Keyword matcher (whole-word, case-insensitive, phrases, matched-keyword snippets)
- [ ] Email report template and sending

### Demo 2: Jev matching
- [ ] Jev matcher behind the matcher interface
- [ ] Summaries for matches only
- [ ] Labeled set (50 to 100 listings, labeled by decision-makers)
- [ ] Compare keyword vs Jev: precision and recall

### Navigation
- [ ] Navigator interface + guards + session handling
- [ ] DOM stream on one site
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
| 2026-10-09 | Runtime agents: `setting_sources=[]`, `strict-mcp-config` + `ENABLE_CLAUDEAI_MCP_SERVERS=false` (claude.ai connectors like Gmail otherwise attach), cwd in a temp dir. Built-in skills/plugins still appear; they ship with the CLI |

## Branching

- `main` is always working code.
- One branch per build step, named by type: `feat/keyword-matcher`, `feat/dom-navigator`, `fix/session-expiry`, `docs/readme`.
- Merge to `main` through a pull request once tests pass, then delete the branch.
- Mark demo-ready versions with tags on `main`: `demo-1-keywords`, `demo-2-jev`.
- No long-lived branch per version: keyword vs Jev and vision vs DOM are config switches, not separate code.

## Open questions

- [ ] Azure VM confirmed?
- [ ] Approved LLM provider for production (Anthropic API, Microsoft Foundry, other)?
- [ ] Email: Microsoft Graph or SMTP? Recipients and frequency?
- [ ] Vision stream text extraction: code (recommended) or screenshots?
- [ ] Threshold trade-off: favor recall over precision?
- [ ] Final name
