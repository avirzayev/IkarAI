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
process. A cron-scheduled cloud agent (via the `schedule` skill) invokes
a Claude Code session roughly once an hour. Each invocation is
stateless at the process level; all durable state lives in two places:

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

## 8. Hourly cycle

1. Load `.env`. Check `session/` for a cached cookie and validate it
   with a lightweight GET. If missing or invalid, stop the run and
   notify you that a manual cookie refresh is needed — the agent never
   attempts to log in itself (see §7).
2. Pull context from Notion: Persona, Strategy, the last few Daily Log
   entries, the Action Catalog, and Diplomacy.
3. Fetch current game state via already-documented GET actions
   (resources, city overview, messages/reports, military status).
4. Decide: compare state against the current strategy and goals.
   - If nothing actionable this hour, exit immediately — this is the
     expected common case, matching IDEA point 4.
   - If an action is needed and it's already in the Action Catalog,
     execute it via HTTP.
   - If an action is needed but undocumented, research it (web search
     for the general mechanic) but do **not** guess-execute an
     unverified HTTP call against the live account — log it in the
     Daily Log as blocked/needs-bootstrap and notify you if it's
     time-sensitive (e.g., under attack).
5. Any newly-confirmed HTTP recipe gets written back into the Action
   Catalog (this is the "modifies itself" behavior from IDEA point 5).
6. Append to today's Daily Log entry (create it if this is the first
   run of the day).
7. Exit.

## 9. Safety guardrails

- **Real-money purchases**: hard-blocked categorically. If a strategy
  consideration surfaces a premium-currency option, it gets logged as
  "considered, declined — policy," never executed. No user-approval
  override exists for this one; it's a flat rule, not a pause-and-ask.
- **Destructive/high-blast-radius game actions** (disbanding the army,
  leaving a clan, abandoning a city): pause and request your explicit
  approval via push notification before executing, same as any other
  hard-to-reverse action in this environment.
- **Unknown actions**: never guess-execute an undocumented HTTP call
  against the live account (see §8 step 4).
- **Session/anti-bot**: the agent never attempts to log in itself
  (see §7). If the cached session cookie is missing/invalid, or any
  response looks like a bot-detection challenge, stop the run and
  notify you that a manual cookie refresh is needed, rather than
  retrying or attempting to work around it.
- **Rate limiting**: the hourly cadence itself is the main throttle;
  within a single run, avoid firing a large burst of requests back to
  back.

## 10. Scheduling

A cron-scheduled cloud agent (via the `schedule` skill) fires roughly
hourly and runs the cycle in §8. Exact cron config is an implementation
detail for the plan, not this spec.

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