<img src="icon.png" width="140" align="right" alt="IkarAI icon">

# IkarAI

An autonomous Claude Code agent that runs an empire in the browser
game [Ikariam](https://ikariam.gameforge.com/) — deciding strategy,
managing buildings and research, tracking diplomacy, and keeping a
daily journal — entirely over direct HTTP, with no browser automation
anywhere in the loop.

## How it works

- **The game client is real HTTP, not a browser.** `scripts/ikariam_client.py`
  replays the exact request/response shapes captured from a real play
  session (method, URL, form fields, the rotating `actionRequest`
  CSRF-like token), not guessed endpoints.
- **Login is always manual.** Ikariam's login is gated behind a
  bot-detection/fingerprinting challenge that plain HTTP can't satisfy,
  and this project never automates a browser to work around it. You
  periodically copy a fresh session cookie from your own browser (see
  "Session cookie" below); the agent only ever reuses it.
- **The knowledge base lives in Notion**, not local files: the agent's
  persona, current strategy, a self-documenting "Action Catalog" of
  every HTTP action it has discovered and verified, a diplomacy/
  relations tracker, and daily logs. `scripts/bootstrap_kb.py` creates
  and seeds all of it.
- **Notifications go through Notion too.** There's no push-notification
  channel — when the agent needs you (session expired, a destructive
  action needs approval, something urgent and unfamiliar), it creates a
  row in a "Human Required" database. You reply by typing directly into
  that page in Notion; the agent picks up the reply on its next cycle
  and archives the row once resolved.
- **Scheduling is self-paced.** A lightweight per-minute cron "ticker"
  (`scripts/ticker.sh`) checks a timestamp the agent writes for itself
  at the end of each cycle (`session/next_wake.txt`) and only spins up
  a real agentic session once that time has passed — so the agent can
  ask to be checked on again in 10 minutes or not for three hours,
  based on what's actually happening in the game.
- **The decision-making is Claude's own judgment, not scripted logic.**
  `RUNBOOK.md` is the instructions a scheduled Claude Code session
  follows each cycle; the safety rules below are enforced by the model
  reading and following that document, not by hard constraints in the
  HTTP client.

## Safety guardrails

- **Real-money purchases are never executed**, categorically — no
  user-approval override exists for this one.
- **Destructive actions** (disbanding the army, leaving a clan,
  abandoning a city) are within the agent's own judgment — no approval
  gate. It documents the reasoning in the Daily Log after the fact.
- **Unknown actions are never guess-executed** against the live
  account — an unfamiliar situation gets researched and logged, not
  improvised against a real HTTP endpoint.
- **No login automation, no captcha-solving, no anti-bot evasion.**

## Setup

Requires Python 3.10+, a Notion internal integration token (with a
parent page shared with it, or full workspace access), and an Ikariam
account.

```bash
cp .env.example .env
chmod 600 .env
# fill in IKARIAM_* and NOTION_TOKEN / NOTION_ROOT_PAGE_ID in .env

make setup   # installs deps, bootstraps the Notion KB, installs the cron ticker
```

### HAR bootstrap (one-time, before the Action Catalog has anything in it)

Since the agent never opens a browser, the initial Action Catalog is
seeded from a real captured play session. Record one yourself (login,
view city, collect resources, queue a building, queue research, send a
message — whatever you want seeded), export it as a HAR file into
`har/`, and re-run `make bootstrap` — `scripts/bootstrap_kb.py` parses
it and adds any newly-discovered actions. (A HAR capture isn't included
in this repo — it's real account traffic.)

### Session cookie (manual, recurring)

The agent never logs in itself. Whenever `session/cookie.txt` /
`session/action_request.txt` are missing or the session has expired:

1. Log into your world server in your own browser.
2. Open DevTools → Network, click any in-game action.
3. Copy the full `Cookie` request header value into `session/cookie.txt`.
4. Copy that same request's `actionRequest` value into
   `session/action_request.txt`.

The agent keeps `action_request.txt` fresh itself between refreshes
(each response carries the next token); only the cookie needs periodic
manual renewal.

## Useful commands

| Command | What it does |
|---|---|
| `make setup` | Full from-zero setup (deps, KB bootstrap, cron) |
| `make bootstrap` | Re-run the Notion KB bootstrap (idempotent) |
| `make cron-install` / `make cron-remove` | Manage the per-minute ticker cron job |
| `make cron-status` | Check whether the ticker is installed |
| `make test` | Run the test suite |
| `make run-once` | Manually trigger one cycle right now |
| `make clean` | Remove `__pycache__`/`.pytest_cache` |

## Project layout

```
scripts/
  config.py           # .env loader
  notion_client.py     # thin Notion REST API wrapper
  bootstrap_kb.py       # creates + seeds the Notion knowledge base
  session_store.py      # session cookie / actionRequest / next_wake file I/O
  ikariam_client.py     # the Ikariam HTTP client
  run_hourly_cycle.sh    # invokes `claude -p` against RUNBOOK.md
  ticker.sh              # per-minute cron entry point, self-paced dispatch
har/
  parse_har.py           # HAR capture parser (seeds the Action Catalog)
tests/                    # pytest suite, all HTTP calls mocked
RUNBOOK.md                 # the instructions the scheduled agent follows
```

## Status

Live and running against a real account. Origin idea and full design
history are kept outside this repo.

## Follow along

Updates on the empire's progress: [@iccub3 on X](https://x.com/iccub3)
