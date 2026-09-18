# IkarAI — Design Spec

Date: 2026-09-18
Status: Approved for planning

## 1. Goal

Give Claude Code an Ikariam empire to run autonomously and with personality.
Once per hour, Claude Code wakes up as the ruler of the empire, reads its
knowledge base, decides what (if anything) needs doing, acts against the
live game over HTTP, and keeps a daily journal a human can read. Claude
decides strategy itself — governance type, city placement, clan vs.
solo, war and diplomacy — researching the web when it doesn't know how a
game mechanic works, and growing a persistent in-character personality
that reacts to what happens in the game (grudges, alliances, ambition).

## 2. Non-goals / out of scope for v1

- No browser automation anywhere in this project, at bootstrap or at
  runtime. Everything after the initial HAR import is raw HTTP.
- No real-money spending, ever (see §8).
- No captcha-solving or anti-bot evasion. If the game challenges the
  agent that way, the agent stops and notifies rather than working
  around it.
- No dry-run mode. The agent is live from the first scheduled run.
- No multi-account, multi-world, or clan-management-at-scale features.
  One account, one world, to start.

## 3. Architecture overview

Claude Code itself is the "brain" — there is no separate always-on
process. A **local** cron job (or systemd timer) on this machine
invokes a headless Claude Code session (`claude -p`) roughly once an
hour. (Earlier drafts of this section proposed a cron-scheduled *cloud*
agent via the `schedule` skill; that was discovered to be infeasible
during implementation — cloud routines run in an isolated sandbox with
no access to this machine's local files, `.env`, or session cookie, and
this project's entire state model depends on exactly those. Local
scheduling was always the documented alternative and is what's actually
implemented.) Each invocation is stateless at the process level; all
durable state lives in two places:

- **Notion** — the knowledge base: persona, strategy, the discovered
  HTTP "Action Catalog," diplomacy, and daily logs. This is what makes
  the empire's memory persistent and human-readable between runs.
- **Local files under `/opt/ideas/games/ikarAI/`** — secrets, the
  cached Ikariam session (cookies), and the one-time bootstrap
  materials. Nothing here is meant to be human-curated content; it's
  operational state and this design doc.

Ikariam itself is accessed by direct HTTP requests (Python `requests`
or `curl`), replaying a logged-in session cookie. There is no browser
involved once bootstrap is done.

## 4. Local file layout

```
/opt/ideas/games/ikarAI/
  IDEA.md              # original idea (existing)
  DESIGN.md            # this spec
  .env                 # Ikariam creds + Notion integration token — chmod 600
  session/             # cached cookie jar + last-login timestamp between runs
  har/                 # user-supplied HAR captures + the one-time parser script
  scripts/             # small reusable helpers (session/login client, Notion API wrapper)
```

`/opt` is not a git repository, so there's no accidental-commit risk,
but `.env` should still be `chmod 600` since it holds a real game
password and a Notion token.

## 5. Credentials & secrets

`.env` holds:
- `IKARIAM_USERNAME`, `IKARIAM_PASSWORD`, `IKARIAM_SERVER` (the world's
  host, e.g. `s(XX).ikariam.gameforge.com`)
- `NOTION_TOKEN` (internal integration token), `NOTION_ROOT_PAGE_ID`
  (the parent page you share with the integration)

Login is manual (see §7), so `IKARIAM_PASSWORD` is stored for your own
reference only — the agent never reads or transmits it. The only
credential the agent actually uses at runtime is the session cookie in
`session/`, which you refresh manually.

## 6. Notion KB structure

One parent page ("IkarAI Empire") shared with the integration, containing:

- **Profile & Persona** (page) — ruler's name, seed traits you provide,
  how they've evolved, backstory, personal "red lines." This is the
  system-prompt-like anchor read every cycle.
- **Strategy** (page) — current long-term plan and active initiatives
  (e.g. "10-day revenge campaign against Player X," with a target
  date), governance choice and rationale, solo-vs-clan stance.
- **Action Catalog** (database) — the self-documenting API from IDEA
  point 5. One row per discovered game action: name, HTTP method, URL
  pattern, required params, headers/session notes, last-verified date,
  free-text notes on quirks.
- **Daily Logs** (database) — one entry per day: summary, resources
  gained/lost, notable events, decisions made, next steps. Updated
  incrementally through the day, finalized once the date rolls over.
- **World Knowledge** (page) — general Ikariam mechanics learned via
  web research (governance types, unit stats, building priorities),
  separate from the account-specific Action Catalog.
- **Diplomacy/Relations** (database) — known players/clans,
  relationship status (ally/enemy/neutral), history of interactions.
  This is what feeds the political/personality arc (grudges, revenge
  plans, alliances).
- **Human Required** (database) — the notification mechanism (§9): a
  row per thing the agent needs from you, title + a "Question" block
  with context. You reply by adding content to the page in Notion; a
  future cycle reads the reply and archives the row, so an empty
  Human Required database means nothing is waiting on you.

## 7. Bootstrap (one-time, before scheduling starts)

Since runtime never opens a browser, you record a short real play
session yourself (login, view city, collect resources, queue a
building, queue research, send a message — whatever core actions you
want seeded) and export it as a HAR file into `har/`. Two captures are
in hand already: `sample1.har` (the Gameforge login/lobby flow, hosts
`challenge.gameforge.com` / `lobby.ikariam.gameforge.com`) and
`sample2.har` (in-world actions on `s401-en.ikariam.gameforge.com`).
Claude parses the HAR(s) once to:

1. Extract how to recognize a valid vs. expired world-server session
   cookie from a response (needed for step-checking in §8). Login
   itself is **not** automated — see the note below.
2. Extract each captured in-world action from `sample2.har` as an
   initial row in the Action Catalog (method, URL pattern, params).

This is a manual, reviewed step — you look at what got imported into
the Action Catalog before the first scheduled run.

**Login is manual, not automated.** `sample1.har` shows Gameforge's
login gated behind `challenge.gameforge.com`'s bot-detection/
fingerprinting script (`withFetchPatch.js`) — the kind of JS challenge
plain HTTP requests can't reliably satisfy, and browser automation is
out of scope for this project (§2). So the agent never attempts to log
in itself. Instead: you periodically log in via your own browser and
place a fresh session cookie into `session/` (exact file format is an
implementation detail). The hourly cycle only ever reuses that cookie
over HTTP; when it's invalid or expired, the agent stops for the run
and notifies you that a manual refresh is needed (see §8, §9).

## 8. Wake-up cycle

Not a fixed hourly cadence — see §10. Each cycle:

1. Check the **Human Required** Notion database for any entries the
   human has replied to (extra content blocks beyond the agent's
   original question); act on replies, then archive those rows so
   they're removed once resolved — this is the notification mechanism
   (see §9; no push notification exists, it's Notion-based).
2. Load `.env`. Check `session/` for a cached cookie and validate it
   with a lightweight GET. If missing or invalid, stop the run, create
   a Human Required entry, pick a short next-wake time (§10), and exit
   — the agent never attempts to log in itself (see §7).
3. Pull context from Notion: Persona, Strategy, the last few Daily Log
   entries, the Action Catalog, and Diplomacy.
4. Fetch current game state via already-documented GET actions
   (resources, city overview, messages/reports, military status).
5. Decide and act — **a loop, not a single step.** Take an action,
   reassess, consider another, repeat until nothing more is worth
   doing this cycle or you're waiting on something (a build timer,
   etc.). Per decision:
   - Nothing to do is a fine place to stop the loop.
   - Known action in the Action Catalog → execute via HTTP.
   - Undocumented action needed → research it (web search) but
     **never** guess-execute an unverified HTTP call against the live
     account — log it in the Daily Log as blocked, or create a Human
     Required entry if genuinely time-sensitive (e.g., under attack).
   - Newly-confirmed HTTP recipes get written back into the Action
     Catalog (the "modifies itself" behavior from IDEA point 5).
6. Update Diplomacy if this cycle involved another player/clan.
7. Update (or create) today's Daily Log entry.
8. Decide the next wake time based on what's pending, write it to
   `session/next_wake.txt`, and exit (see §10).

## 9. Safety guardrails

- **Real-money purchases**: hard-blocked categorically. If a strategy
  consideration surfaces a premium-currency option, it gets logged as
  "considered, declined — policy," never executed. No user-approval
  override exists for this one; it's a flat rule, not a pause-and-ask.
- **Destructive/high-blast-radius game actions** (disbanding the army,
  leaving a clan, abandoning a city): pause and create a Human Required
  entry describing what would happen and why; only proceed once a
  future cycle reads an explicit reply, same as any other hard-to-
  reverse action in this environment.
- **Unknown actions**: never guess-execute an undocumented HTTP call
  against the live account (see §8 step 5).
- **Session/anti-bot**: the agent never attempts to log in itself
  (see §7). If the cached session cookie is missing/invalid, or any
  response looks like a bot-detection challenge, stop the run and
  create a Human Required entry asking for a manual cookie refresh,
  rather than retrying or attempting to work around it.
- **Notifications**: there is no push-notification channel. "Notify
  the human" always means: create a row in the **Human Required**
  Notion database (title + a question block). The human reads Notion
  and replies by adding content to that page; a future cycle picks up
  the reply (§8 step 1) and archives the row once resolved.
- **Rate limiting**: within a single cycle, avoid firing a large burst
  of requests back to back. The self-paced wake interval (§10) is the
  main throttle across cycles.

## 10. Scheduling

Self-paced, not a fixed hourly cadence. A lightweight **ticker** cron
job runs every minute on this machine and checks `session/next_wake.txt`
(an ISO-8601 UTC timestamp the agent writes at the end of each cycle,
§8 step 8); if that time hasn't passed yet, the ticker exits
immediately at near-zero cost. Once it has (or the file doesn't exist
yet, e.g. before the first run), the ticker invokes `claude -p`
headlessly from `/opt/ideas/games/ikarAI/` with a prompt telling it to
follow `RUNBOOK.md`, running the cycle in §8 with full access to this
machine's `.env`, `session/`, and scripts — exactly what the design in
§3 requires. The agent decides its own pacing: shortly after something
pending is expected to finish, or a longer default (tens of minutes to
a few hours) when idle — never sub-5-minute waits out of habit. Exact
ticker/cron config is an implementation detail for the plan, not this
spec.

## 11. Known risks

- Ikariam's ToS may prohibit automation/botting; this is the user's
  own account and decision to accept that risk — noted here, not
  gatekept.
- The site can change its HTML/request shapes over time, which would
  silently break Action Catalog entries; the "last-verified" field
  exists so staleness is at least visible in the KB.
- HAR-based bootstrap only covers what you happened to click during
  capture; broad play (research tree, clan management, trading with
  other players, building every structure type) will surface
  undocumented actions over time and log as blocked until you
  capture a follow-up HAR for those flows.


## Data for implementation

Credentials (Ikariam login, Notion token) live in `.env` (chmod 600),
not here — see §5.

- Ikariam world server: `s401-en.ikariam.gameforge.com` (found in the
  HAR capture; login itself goes through Gameforge's centralized
  account system at `challenge.gameforge.com` / the lobby, then
  redirects into the world server with a session — see §7).
- Real-money shop lives on its own subdomain,
  `intl14-shop.ikariam.gameforge.com` — the hard-block in §9 can be
  implemented simply as "never issue a request to this host."
- City name: `ClaudeEmpire`.
- Character profile (persona seed, goes into the Notion Profile &
  Persona page per §6): seeks to be the best in the game and to be
  known for it; very patient, knows when he's in a position of power
  versus when to step back and wait for the right moment; a skilled
  politician who builds alliances; doesn't keep relationships or
  assets that no longer serve him.
- Notion root page: created — ["IkarAI Empire"](https://app.notion.com/p/IkarAI-Empire-3dfe84e320f581b49b83c51021ac8986),
  ID `3dfe84e3-20f5-81b4-9b83-c51021ac8986`, stored as
  `NOTION_ROOT_PAGE_ID` in `.env`. The integration has full workspace
  access, so this page is the deliberate scope boundary for this
  project even though nothing technically prevents writing elsewhere.