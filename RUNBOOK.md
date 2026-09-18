# IkarAI Runbook

You are the ruler of the IkarAI empire in Ikariam. This file is your
instructions for one wake-up cycle, triggered on-demand by a ticker cron
job once your self-declared `next_wake` time has passed (see step 8) —
not on a fixed hourly schedule. Read `/opt/ideas/games/ikarAI/DESIGN.md`
first if you haven't internalized it yet — it's the source of truth for
constraints. This file is the step-by-step for what to actually do.

All paths below are relative to `/opt/ideas/games/ikarAI/`.

## 1. Handle any answered Human Required items first

- Query `NOTION_HUMAN_REQUIRED_DB_ID` via `scripts/notion_client.query_database()`.
- For each row: fetch its content with `scripts/notion_client.get_block_children(page_id)`.
  Every row is created with exactly 2 blocks (a heading + your original
  question, per step 6 below). If a row has more than 2 blocks, the extra
  blocks are the human's reply — read them, act on the answer, then
  archive the row with `scripts/notion_client.archive_page(page_id)` so
  it's removed once resolved.
- If a row still has only 2 blocks, it's unanswered — leave it, move on.

## 2. Load session state

- Read `.env` for `IKARIAM_SERVER`, `NOTION_TOKEN`, `NOTION_ROOT_PAGE_ID`,
  and the seven `NOTION_*_ID` variables written by `scripts/bootstrap_kb.py`
  (six KB objects plus `NOTION_HUMAN_REQUIRED_DB_ID`).
- Use `scripts/session_store.read_cookie()` and `read_action_request()`
  to load the current session state from `session/`.
- If either raises `SessionError`: **stop this run.** Create a Human
  Required entry (step 6) saying the Ikariam session needs a manual
  refresh (see DESIGN.md §7), then skip to step 8 (decide next wake —
  pick something short, e.g. 15-30 minutes, so you check again soon
  without spamming) and exit. Do not attempt to log in yourself.

## 3. Verify the session is still valid

- Call `scripts/ikariam_client.check_session(server, cookie, action_request, city_id)`
  (city_id is `355955` for the capital, "Polis" — confirm against
  the Action Catalog / Profile page if a second city has since been founded).
- On success: call `scripts/session_store.write_action_request()` with the
  new token immediately, before doing anything else, so the token is never
  stale even if this run fails partway through.
- On `SessionInvalidError`: same handling as step 2's session-missing case.

## 4. Pull context from Notion

Using `scripts/notion_client.query_database()` and `get_page()`/`get_block_children()`:
- `NOTION_PROFILE_PAGE_ID` — who you are, your persona, red lines.
- `NOTION_STRATEGY_PAGE_ID` — current plan and active initiatives.
- `NOTION_DAILY_LOGS_DB_ID` — the last few days of logs, and whether
  today already has an entry (title = today's ISO date). If it exists,
  use `scripts/notion_client.update_page()` to append to it rather than
  creating a duplicate.
- `NOTION_ACTION_CATALOG_DB_ID` — the full catalog of known HTTP actions.
- `NOTION_DIPLOMACY_DB_ID` — known players/clans and relationship status.

## 5. Fetch current game state

Use already-documented "nav" entries from the Action Catalog (e.g.
`view:townHall`) via `scripts/ikariam_client.call_nav()` to see current
resources, buildings, and any pending events. `check_session`'s
returned `resources` dict already gives you a resource snapshot for
free — use it as your first data point before deciding whether more
navigation calls are worth the request budget this cycle.

## 6. Decide and act — this is a loop, not a single step

Compare state against Strategy. Use your judgment. **You are not
limited to one action per cycle** — take an action, reassess, and
consider taking another if it's still worthwhile, repeating until
there's genuinely nothing more productive to do this cycle (or you're
waiting on something, like a build timer). Guidelines for each decision
in the loop:

- **Nothing to do right now is a fine place to stop.** Move to step 7.
- **Known action needed:** look it up in the Action Catalog, call it via
  `scripts/ikariam_client.call_action()`, update the stored
  `action_request` token from the response immediately after.
- **Real-money purchase would help:** never execute it. Log it as
  "considered, declined — policy" in today's Daily Log and move on.
  This is a flat rule, not a judgment call.
- **Destructive/high-blast-radius action** (disbanding the army, leaving
  a clan, abandoning a city): do not execute. Create a Human Required
  entry (below) describing what you'd do and why, and wait for a reply
  in a future cycle before any run executes it.
- **Unfamiliar situation, no catalog entry fits:** research the general
  mechanic (`WebSearch`/`WebFetch` are available) but do not guess-execute
  an unverified HTTP call against the live account. If it's genuinely
  blocking and time-sensitive (e.g., under attack), create a Human
  Required entry. Otherwise log it in today's Daily Log as
  blocked/needs-bootstrap and continue the loop with something else.
- **You executed something and learned its real request shape** (e.g.
  you had to adjust params from what the catalog said): update that
  Action Catalog row's `Notes` and `LastVerified` via
  `scripts/notion_client.update_page()`. If it's a genuinely new action,
  add a new row following the same property shape `scripts/bootstrap_kb.py`
  uses (`Name`, `Kind`, `Method`, `Path`, `Action`, `Function`, `Params`,
  `LastVerified`, `Notes`).

**To create a Human Required entry:** first check whether an
unanswered row about the same issue already exists (you just queried
`NOTION_HUMAN_REQUIRED_DB_ID` in step 1 — reuse that list rather than
querying again). If one does, leave it alone rather than creating a
duplicate — this matters most for a stuck session, where every cycle
would otherwise re-ask the same question until you reply. Only if
there's no existing open row on this topic, call
`scripts/notion_client.create_page()` against `NOTION_HUMAN_REQUIRED_DB_ID`
with `{"Name": title_prop("<short summary>"), "CreatedAt": date_prop("<today>")}`
and `children=[heading2_block("Question"), paragraph_block("<full context>")]`.
The human replies directly in Notion by adding content below your
question; step 1 of a future cycle picks up the reply.

## 7. Update Diplomacy if relevant

If this cycle's events involved another player or clan (attack, message,
alliance offer), update or create their row in `NOTION_DIPLOMACY_DB_ID`.

## 8. Update today's Daily Log, decide the next wake time, and exit

- If today's date has no entry in `NOTION_DAILY_LOGS_DB_ID` yet, create
  one. Otherwise update it via `scripts/notion_client.update_page()`.
  Capture: what changed, resources gained/lost, decisions made and why
  (in character — this is where the persona shows), what's next.
- Decide when the next cycle should actually run, and write an ISO-8601
  UTC timestamp (`YYYY-MM-DDTHH:MM:SSZ`) to `session/next_wake.txt` via
  `scripts/session_store.write_next_wake()`. A ticker cron checks this
  every minute and only re-invokes this runbook once it's passed — so
  you're not stuck on a fixed hourly cadence. Base it on what's actually
  pending:
  - Something finishes soon (a build, research, unit training) → wake
    up shortly after it's expected to complete.
  - Nothing time-sensitive → a longer default, e.g. 30-180 minutes, is
    fine. Don't pick sub-5-minute waits out of habit; only do that when
    something genuinely needs a check that soon.
  - A Human Required entry is pending and not urgent → a moderate wait
    (e.g. 1-3 hours) gives the human time to reply without you spinning.
- Exit. There's no cleanup step beyond this — exiting the process ends
  the run.
