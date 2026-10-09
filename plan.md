# Plan

Full design: [Listing Matcher: Design Summary](https://claude.ai/code/artifact/44639852-e4a8-4495-867a-a9302a26f957)
Failure handling: [Listing Matcher: Failure Modes and Handling](https://claude.ai/code/artifact/02861676-cab1-47d7-b843-46f16b5e8e4a)

## Build order

### Setup
- [ ] Repo, `uv init`, dependencies, Playwright Chromium
- [ ] Confirm the Agent SDK runs with the subscription login
- [ ] Confirm runtime agents load no filesystem settings (`CLAUDE.md`, `~/.claude/`)

### Demo 1: keyword matching
- [ ] 20 to 30 hand-copied listings saved as test data
- [ ] Database schema (listings, decisions, runs)
- [ ] Keyword matcher (whole-word, case-insensitive, phrases, matched-keyword snippets)
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

## Open questions

- [ ] Azure VM confirmed?
- [ ] Approved LLM provider for production (Anthropic API, Microsoft Foundry, other)?
- [ ] Email: Microsoft Graph or SMTP? Recipients and frequency?
- [ ] Vision stream text extraction: code (recommended) or screenshots?
- [ ] Threshold trade-off: favor recall over precision?
- [ ] Final name
