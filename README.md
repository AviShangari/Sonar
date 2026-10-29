# Sonar

AI agents that scan project-bidding sites, match new listings against areas of interest and email a ranked report of the best opportunities.

Each run: open each site in a dedicated browser, collect new listings, skip ones already stored, save the posting text, match it against the client's interests, write a short summary for each match, and email one report.

Design rules and the build history are in [AGENTS.md](AGENTS.md) and [plan.md](plan.md).

## Setup (once per machine)

You need [uv](https://docs.astral.sh/uv/) (it installs the right Python, version pinned in `.python-version`).

```bash
uv sync                                              # install the project's dependencies
uv run playwright install chromium                   # install the browser Sonar drives
uv tool install browser-use==0.13.11 --python 3.12   # the browser CLI the agent uses (pinned; never install from git main)
```

Then create a file named `.env` in the repo root. It is gitignored and is the only place secrets live. Add only what you need:

| Variable | Needed for |
| --- | --- |
| `SONAR_SMTP_PASSWORD` | Sending the email report (a Gmail app password for the default settings) |
| `OPENROUTER_API_KEY` | Every model call: the browsing agent, the summaries and the `jev` matcher |
| `SONAR_<SITE>_USERNAME` / `SONAR_<SITE>_PASSWORD` | Each site that needs a login. `<SITE>` is the site name in capitals: `SONAR_ONTARIOTENDERS_*`, `SONAR_ARIBA_*`, `SONAR_ARIBA2_*`. CanadaBuys needs none. |

There is no Claude-login mode: the agents always use the OpenRouter key, so the key is required for any run.

## Run it

```bash
uv run python -m sonar.main
```

A browser window opens (set `headless: true` in `config/settings.yaml` to hide it). When the run ends, the report is written to `data/reports/report-run-<id>.html` and `.txt`, and emailed to `email_to`.

Options:

| Option | What it does |
| --- | --- |
| `--site NAME` | Only this site. Repeat it for several sites. The default is all sites in `config/sites.yaml`. |
| `--days N` | How far back to look on the first run for a site (used only when nothing is stored yet). The default is 1 day; `first_run_days` in `config/sites.yaml` changes it per site. |
| `--max-new N` | Test cap on new postings per site. A capped run does not move the stop point, so nothing is skipped later. |

A cheap first test: `uv run python -m sonar.main --site canadabuys --days 1 --max-new 3`.

A real run uses AI models and **costs money** (it is billed to the OpenRouter key). `max_run_usd` in `config/settings.yaml` stops a run at that dollar amount. Only one run can go at a time; a second one exits with code 2 and emails `[RUN SKIPPED]`.

## Ask questions about the stored reports

```bash
uv run python -m sonar.assistant
```

A terminal chat with an assistant that answers questions about what Sonar has stored: recent matches, closing dates, why a posting matched, what a posting says, and how the last run went. It only reads the database (opened read-only); it cannot scan, log in, send email or start a run. It uses `assistant_model` and **costs a little per question** (the session cost is shown when you leave). Commands: `/reset` starts a new conversation, `/help`, `/quit`.

## Settings

| File | What it holds |
| --- | --- |
| `config/settings.yaml` | Mode flags, models and limits: `navigation_model`, `summary_model`, `assistant_model`, `navigator`, `matcher` (`keywords` or `jev`), `headless`, cost caps, email addresses and mail server. Every setting is explained in the comments at the top of the file. |
| `config/sites.yaml` | The sites to scan: start address, whether a login is needed, allowed hosts. |
| `config/keywords.yaml` | The client's keywords for the keyword matcher. |
| `config/company.yaml` | What the client does; the text of Jev's yes/no question. |

`email: true` must stay on. To try a report without sending it, use `send_report.py --dry-run` (below).

## Where things end up

Everything a run writes goes under `data/` (gitignored): the database `sonar.db`, `reports/`, `screenshots/` (saved when navigation fails), `route-notes/`, and the browser profiles.

## Helper scripts

Run any of them with `uv run python scripts/<name>.py`. They are for checking and re-doing, not for daily use.

| Script | What it does |
| --- | --- |
| `make_report.py RUN_ID` | Write the report files for a past run again. |
| `send_report.py RUN_ID [--dry-run]` | Email the report of a past run. `--dry-run` prints the text instead of sending. |
| `summarize_run.py RUN_ID [--redo]` | Have the agent write summaries again for a past run's matches. |
| `show_clean_text.py [site] [job-id]` | Show the stored posting text after boilerplate is stripped. |
| `check_login.py SITE` | Live check that our login works on a site. |
| `check_site.py [site]` | Read-only check that a site's start page loads. |
| `check_openrouter.py` | Check the OpenRouter key, the two models and a tool call through our agent loop (a few tiny paid calls; prints each call's cost). |
| `check_jev.py --dry-run` | Preview what the Jev matcher would send. Without `--dry-run` it makes paid calls. |
| `run_dom_navigator.py [site] [--limit N]` | Run only the navigator on one site (stores postings, no matching). |

## Tests

```bash
uv run pytest
```

Some tests start a real headless browser against a local fake site.

## Docker

The image is how Sonar will be deployed. It runs headless as a non-root user.

```bash
docker build -t sonar .
docker run --rm --shm-size=1g --env-file .env -v ./config:/app/config:ro -v ./data:/app/data sonar
```

This runs all the sites. You can add the same options as above after `sonar` (for example `--max-new 3` for a test). `--env-file .env` supplies `OPENROUTER_API_KEY`. Never publish the browser's debug port.

## Safety

The agents only read: they never submit bids, send messages or change account settings. Logins are done by our code, never by an agent, and the browser blocks every request that is not a read. Details are under "Safety rules" in [AGENTS.md](AGENTS.md).
