# Hermes spike: measurements and observations (2026-10-27 and 2026-10-28)

Kept in case we do proper metrics later. Everything here is from one developer laptop (Windows 11), one run per row unless
stated, so treat single figures as indicative. The decisions that came out of it are in `plan.md` (the Hermes sections).
`learning_runs.jsonl` is the script's own raw log of the server-mode runs (34 lines, no model field: runs before the model pin were
on glm-5.3, see section 3).

## 1. Setup

| Item | Value |
| --- | --- |
| Hermes Agent | v0.22.4 (stable, commit 4d04303818b6), test home `C:\hh` (short path on purpose) |
| Model (Sonar's choice) | `openai/gpt-6.1-sol` through OpenRouter; original agent at `navigation_effort: medium`; Hermes at its default effort (not set) |
| Browser | our Playwright Chromium with our request guards, Hermes attached over `browser.cdp_url`; Hermes tools: `browser_exec` only (backend `browser-use`) plus memory and skills in the learning runs |
| Safety in force | guard plugin (allowlist; the old `code_check.py` on every `browser_exec` snippet; skills: create and patch only), preflight before every run, whitelisted environment (no site or SMTP passwords) |
| Sites | CanadaBuys (public), Ontario Tenders (login), Ariba account 1 and 2 (login, same start address), MERX (public, not in `sites.yaml`, terms of use NOT read) |
| Costs | exact: per-call `GET /api/v1/generation?id=...` on OpenRouter, or the cost OpenRouter reports in each reply (original agent). The key counter lags and under-reports. |
| Task | read the first list page: rows with title, address, date (Hermes capped at 50 rows; MERX shows 25; Ariba 4 to 6 events) |

Scripts (outside the repo, `C:\hh\spike\`): `run_hermes_any.py` (one-shot), `run_hermes_learn.py` (one-shot with memory and skills),
`run_hermes_gateway.py` (server mode: `--label`, `--reflect`, `--hint`, `--report-stale`, `--fix-stale`, `--capture`, `--no-extras`),
`run_old_any.py` (original agent), `preflight.py`, `check_child_env*.py`, `where.py`-style per-call breakdowns. Guard plugin:
`C:\hh\plugins\sonar_guard\`.

## 2. One-shot runs (`hermes chat --oneshot`), GPT-6.1 Sol, `browser_exec` + old code filter

| Site | Hint | Rows | With address | Calls | Time | Cost |
| --- | --- | --- | --- | --- | --- | --- |
| MERX | none | 25 | 25 | 7 | 51 s | $0.0676 |
| CanadaBuys | none | 50 | 50 | 12 | 93 s | $0.1812 |
| CanadaBuys | hand-written | 50 | 50 | 9 | 74 s | $0.1208 |
| Ontario | none | 50 | 0 (dates 50) | 17 | 91 s | $0.2619 |
| Ontario | hand-written | 50 | 50 | 15 | 85 s | $0.1406 |
| Ariba 1 | none | 0 events | n/a | 25 | 100 s | $0.1574 |
| Ariba 1 | hand-written | 4 | 4 | 10 | 74 s | $0.1115 |
| Ariba 2 | hand-written | 6 | 6 | 11 | 71 s | $0.1012 |

Named browser tools only (five tools, no code): CanadaBuys $0.0924 and Ariba $0.0648, both with 0 addresses (the named tools cannot read link addresses).
Original agent, CanadaBuys page 1, earlier measurement: 2 calls, 41 s, $0.0441, 7,221 input tokens (Hermes one-shot: 144,000 to 288,000).

Learning attempt in one-shot mode (2026-10-28 morning, Ontario, no hint): setup mistake (wrong backend) 5 calls $0.0065; task silent about learning
25 calls (turn limit) 139 s $0.2438, 20 rows each with a distinct `rfqId` address, no dates; task asks to save what it learned 19 calls 117 s $0.2988,
50 rows with dates, no addresses. Nothing saved, zero memory or skill calls: one-shot mode hides `skill_manage` and tells the model not to create skills
(`agent/oneshot_footprint.py`). The same prompt gave different output from one run to the next.

## 3. Server mode ("gateway") on the WRONG model, glm-5.3 (do not compare these costs with GPT figures)

The server never got `-m`, so it used Hermes's default `z-ai/glm-5.3`. Findings about learning still hold; costs do not carry over.

| Run | Calls | Time | Cost | Result |
| --- | --- | --- | --- | --- |
| Ontario, 30-call cap, asks to save | 30 | 119 s | $0.0518 | 30 rows, no addresses, nothing saved (cap) |
| Ontario learn (cap 50) | 49 | 211 s | $0.1036 | 48 rows with addresses; skill saved |
| Ontario reuse | 22 | 99 s | $0.1483 | 50 rows with addresses; patched the skill |
| CanadaBuys learn / reuse | 37 / 6 | 135 / 47 s | $0.1402 / $0.0110 | 50 rows both |
| MERX learn / reuse | 25 / 15 | 106 / 38 s | $0.0976 / $0.0214 | 25 rows both |
| Ariba 1 no hint | 52 | 230 s | $0.1102 | 0 rows, no skill saved |
| Ariba 2 no hint | 53 | 213 s | $0.1244 | 0 rows, no skill saved |
| Ariba 1 with hint | 29 | timed out at 700 s | $0.4804 | no result |

About $1.29 in total. The model mistake was found only because the timed-out run's log named the model.

## 4. Server mode on GPT-6.1 Sol: learn once, reuse in a fresh conversation (model confirmed on every call)

"Learn" = the task asks it to save a skill; "reuse" = a new conversation, same task, no mention of skills. Skills started empty.

| Site | Learn: calls, time, cost | Reuse: calls, time, cost | Result |
| --- | --- | --- | --- |
| CanadaBuys | 23, 130 s, $0.1617 | 8, 65 s, $0.0937 (1 call unpriced) | 50 rows, addresses, dates |
| Ontario | 40, 207 s, $0.3355 | 8, 68 s, $0.2107 | 50 rows, addresses, dates |
| MERX | 16, 85 s, $0.1178 | 6, 41 s, $0.0952 | 25 rows |
| Ariba 1 (learn WITH the hand-written hint) | 14, 77 s, $0.1113 | 9, 44 s, $0.0775 (no hint) | 4 events |
| Ariba 2 (reuse of account 1's skill) | n/a | 16, 60 s, $0.1366 (no hint) | 6 events |

Round cost about $1.34. Ariba without the hand-written first step ("use the Ariba Sourcing tab, unfold the Status: Open group") never found its events
in any no-hint run; with it in one learning session, the saved skill carried the step and later runs did not need the hint. Skills saved so far:
`ontario-tenders-open-opportunities`, `canadabuys-tender-list`, `merx-tender-list`, `ariba-open-events` (read by a person; navigation steps only).

## 5. Failure test: break a saved note on purpose (CanadaBuys, GPT-6.1 Sol)

Two facts changed in the note (list page `/en/tender-opportunities` to `/en/tender-notices`; notice pattern to `/en/notices/tender/`).

| Task wording | Calls | Time | Cost | Result |
| --- | --- | --- | --- | --- |
| also asks for a one-sentence `stale_note` if a note was wrong | 11 | 71 s | $0.1256 | 50 rows; exact plain report of what was wrong; note NOT repaired |
| also tells it to patch a wrong note | 8 | 55 s | $0.1580 | 50 rows; same report; note repaired (identical to the original) |

A stale note cost about one detour ($0.03 to $0.06 over a normal reuse run, $0.0937). Hermes repaired the note only when told to.

## 6. Where the money goes (per-call breakdowns, GPT-6.1 Sol)

Input per call = system prompt + tool definitions + the task + every earlier model reply and tool result. Fixed part about 7,400 tokens
(system prompt 19,167 chars, of which the skills list is 6,793; tool definitions about 2,300 tokens; task 971 chars). Derived rates: fresh input about $2.5
per million tokens, cached input far less, output roughly $10 to $15 per million. About 81% of input tokens are cache hits, but the first call of a session
never hits the cache, even for sessions two minutes apart, although the system prompt is identical (cause unknown).

**Ontario reuse session, $0.2107, 8 calls:**

| Call | Tokens sent | Cached | Output | Cost | What was new |
| --- | --- | --- | --- | --- | --- |
| 1 | 7,455 | 0 | 25 | $0.0189 | fixed prompt, first time |
| 2 | 8,210 | 7,452 | 41 | $0.0030 | small |
| 3 | 33,915 | 8,207 | 71 | $0.0658 | +25,700 tokens: an 85,807-character tool result |
| 4 | 34,108 | 33,912 | 123 | $0.0051 | small |
| 5 | 53,228 | 34,105 | 185 | $0.0531 | +19,100 tokens: a 52,386-character tool result |
| 6 | 53,769 | 53,225 | 606 | $0.0127 | small |
| 7 | 54,471 | 53,766 | 522 | $0.0124 | small |
| 8 | 57,866 | 54,468 | 2,571 | $0.0397 | the final answer (50 rows typed out) |

**CanadaBuys reuse session, $0.0990, 9 calls:** prompt tokens 7,464 / 8,002 / 8,864 / 9,434 / 9,756 / 11,558 / 17,623 / 18,781 / 19,075; output 24 / 139 / 474 / 225 /
125 / 210 / 81 / 168 / 2,771; cost $0.0189 / 0.0035 / 0.0077 / 0.0046 / 0.0030 / 0.0076 / 0.0171 / 0.0063 / 0.0303. One tool result of 18,568 characters (the row data);
the final answer 9,313 characters. Shares: fixed prompt 19%, retyped final answer 31%, reading the rows 17%, six small calls 33%.

Other per-session costs: the paid title-writing call about $0.0011; a "background review" (a forked pass that re-reads the whole conversation, about 30,000 tokens,
after about 15 calls or after Hermes patches a note) $0.0168 to $0.0398 per occurrence, switch `auxiliary.background_review.enabled`; our code filter refuses about half
of Hermes's snippets and each refused attempt costs about $0.009 (a rewrite of 600 output tokens).

## 7. Rows by reference ("capture") and the original agent

Capture: Hermes prints rows as `ROW|title|address|date` between `BEGIN_ROWS` and `END_ROWS`, an observer hook copies the block, our code checks every line,
and Hermes's final answer is `{"rows_from": "last_extraction"}` (13 tokens instead of about 2,771). No per-site code. All rows passed the checks in every run (0 rejected).

| Site | Try 1 (adapts the note): main + review | Try 2 (steady): main + review | Calls, time (try 2) |
| --- | --- | --- | --- |
| CanadaBuys | $0.0869 + $0.0196 (10 calls, 68 s) | **$0.0738** + none | 7, 40 s |
| Ontario | $0.1684 + $0.0279 (11 calls, 53 s) | **$0.1476** + $0.0254 | 11, 43 s |
| MERX | $0.1084 + $0.0398 (10 calls, 64 s) | **$0.0815** + none | 7, 38 s |
| Ariba 1 | $0.1269 + $0.0217 (16 calls, 62 s) | **$0.0782** + $0.0168 | 11, 54 s |
| Ariba 2 | $0.1250 + $0.0236 (12 calls, 50 s) | not run | n/a |

Capture saves about 25% on 50-row pages (CanadaBuys $0.0990 to $0.0738, Ontario $0.2107 to $0.1476) and nothing on Ariba (4 to 6 rows: $0.0782 vs $0.0775).
Where the CanadaBuys $0.0738 goes: fixed prompt $0.0192 (26%), an exploratory 27,845-character dump $0.0251 (34%), extraction code and compact read about $0.021.
Where the Ontario $0.1476 goes: one 85,578-character dump on call 5 $0.0653 (44%), fixed prompt $0.0194, the rest about $0.06.

**Original agent** (pipeline's own agent, `browser(code)` tool and browser-use CLI, production hints, GPT-6.1 Sol at medium effort, first list page):

| Site | Cost | Calls | Time | Rows | Prompt / completion tokens |
| --- | --- | --- | --- | --- | --- |
| CanadaBuys | $0.0647 (earlier run: $0.0441, 2 calls, 41 s) | 4 | 46 s | 50 | 29,189 / 3,509 |
| Ontario | $0.1150 | 11 | 85 s | 74 (all) | 111,456 / 5,981 |
| Ariba 1 | $0.0394 | 5 | 28 s | 4 | 25,034 / 1,234 |
| Ariba 2 | $0.0479 | 7 | 38 s | 6 | 48,845 / 1,647 |

Hermes (steady state, main session) against the original: 1.1x (CanadaBuys), 1.3x (Ontario), 2x (Ariba 1), 2.6x (Ariba 2, first capture run), plus $0.017 to $0.040
whenever Hermes patches a note. The original had hand-written hints per site; Hermes had none, except Ariba's first step given once.

## 8. Observations and gotchas

- One-shot mode (`-q --oneshot`) cannot learn: it hides `skill_manage`. Server mode (`hermes gateway run`, local address, random API key) can.
- A server-mode request that repeats an identical first message is treated as the same conversation; a timed-out earlier one blocks it with HTTP 409 (0 cost). Start each prompt with a unique label.
- The server needs `model:` pinned in its config; otherwise it takes its own default (glm-5.3).
- Hermes strips only credentials it knows from the code child process; our own passwords in the environment reached it until Hermes was started with a whitelisted environment.
- Hermes tried to delete skills three times at the end of the Ariba no-hint sessions; the guard (create and patch only) blocked them.
- Run-to-run variance is large for the same prompt (rows 20 vs 50, addresses present or absent, calls 22 to 49 for one learning task).
- Saved notes (read after every session) held navigation steps only, no secrets, no instructions; they also record which code our filter rejects.
- `urls.normalize_url` does not keep the `rfqId` parameter, so Ontario's `initDetailRfq.do?rfqId=...` addresses would collapse to one; Ontario's `/current/<id>/detail.si` addresses are fine. Typed addresses return HTTP 403 on Ontario while logged in (clicking works).
- MERX's terms of use were not readable (HTTP 403 to our fetch tool); MERX has a bot check (`awswaf.com`) that our guards block.
- Rough spend on the Hermes experiment, adding up the exact figures above (excludes small unlisted tests): about $6.4. Earlier one-shot spike table and named-tool runs about $1.25; one-shot learning attempts $0.55; glm-5.3 sessions $1.29 (the model mistake); Sol learn and reuse $1.34; break-a-note test $0.28; step 4 and rows-by-reference runs about $1.37; original agent on four sites $0.27.

## 9. Hermes before and after what we built (cost per list page, GPT-6.1 Sol, main session only)

"Before" = one-shot, no hint (section 2). "Hint" = one-shot, hand-written hint (section 2). "Now" = server mode with a saved note and rows by reference, steady state (section 7).
"Original" = the pipeline's own agent with its production hints (section 7). Sources are the tables above; one run each.

| Site | Before (no hint) | Hint (hand-written) | **Now (notes + rows by reference)** | Original agent | Now vs before | Now vs hint |
| --- | --- | --- | --- | --- | --- | --- |
| CanadaBuys | $0.1812 | $0.1208 | **$0.0738** | $0.0647 | -59% | -39% |
| Ontario | $0.2619 (0 addresses) | $0.1406 | **$0.1476** | $0.1150 | -44%, now with addresses | +5% |
| Ariba 1 | $0.1574 (found 0 events) | $0.1115 | **$0.0782** | $0.0394 | -50%, and now works | -30% |
| Ariba 2 | not run | $0.1012 | **$0.1250** (first capture run) | $0.0479 | n/a | +24% |
| MERX | $0.0676 | not run | **$0.0815** | not run | +21% | n/a |

Caveats: "before" and "hint" are one-shot runs, "now" is server mode, so the comparison mixes the two modes with the effect of learning. One run per cell.
"Now" excludes the $0.017 to $0.040 a session costs whenever Hermes patches its note. Ariba's first step still had to be given once by a person.
