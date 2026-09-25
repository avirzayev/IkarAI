# Chores engine and pre-tick gate — spec

Goal: routine upkeep runs as deterministic Python; the LLM cycle only runs
when there is a decision to make. New chores are written by the agent as
data (a Notion row), proven in dry-run, then promoted — so cycles get
cheaper as the empire grows instead of more expensive.

```
ticker.sh (every minute, gated by next_wake)
  └─ run_hourly_cycle.sh
       ├─ scripts/pretick.py      ← state + chores + wake decision (no LLM)
       │     exit 0  → handled; it already wrote next_wake. Done.
       │     exit 10 → LLM needed; reasons written to session/wake_reasons.txt
       │     other   → error → run the LLM cycle anyway (fail open)
       └─ claude -p (RUNBOOK) with the wake reasons in the prompt
```

## 1. Structured state — `python3 scripts/game.py state [--json]`

One call gathers every own city (like `cities`), persists the rotating
token, and emits this JSON (`--json`) or a compact text rendering (no
flag). Any field that can't be determined is `null` — never guessed.

```json
{
  "time": 1790336336,
  "gold": 30456, "gold_per_hour": 2229,
  "transporters": {"free": 8, "max": 8},
  "alerts": {"under_attack": false, "unread_messages": 0},
  "queue": [{"city": "Async", "done_in_min": 108}],
  "cities": [{
    "id": "355955", "name": "Kernel", "coords": "53:67",
    "island_id": "692", "tradegood": "wine",
    "resources": {"wood": 69474, "wine": 26604, "marble": 11, "crystal": 181, "sulfur": 1017},
    "max_resources": 73920,
    "production_per_hour": {"wood": 548, "tradegood": 120},
    "wine_per_hour": 44, "wine_hours_left": 604.6,
    "tavern": {"position": 7, "level": 8, "wine_level": 6, "max_wine_level": 8},
    "citizens": 182, "population": 812, "max_population": 900,
    "growth_per_hour": 0.09, "happiness": "neutral", "corruption_pct": 3,
    "workers": {"wood": 548, "tradegood": 100, "scientists": 58, "priests": 0},
    "construction": [{"building": "Shipyard", "position": 1, "done_in_min": 14}],
    "buildings": [{"position": 0, "name": "Town Hall", "level": 11, "busy": false}]
  }]
}
```

Also saved to `session/state.json` on every run (pretick and chores read it).

## 2. Chores — Notion "Chores" database

| Property | Type | Meaning |
|---|---|---|
| Name | title | e.g. `wine-resupply` |
| Status | select | `proposed` · `dry-run` · `active` · `disabled` |
| Rule | rich_text | JSON rule (below) |
| Why | rich_text | one line: what recurring problem it fixes |
| DryRunsOK | number | consecutive correct dry runs |
| Runs / Failures | number | lifetime counters |
| LastRun | date | |
| LastResult | rich_text | one line |

Rule JSON (a small, closed vocabulary — no code):

```json
{
  "scope": "each_city",
  "when": [{"field": "wine_hours_left", "op": "<", "value": 6},
           {"field": "tavern.wine_level", "op": ">", "value": 0}],
  "do": {"primitive": "transport", "resource": "wine", "amount": 1500,
         "from": "richest", "to": "$city"},
  "limits": {"cooldown_min": 120, "max_per_day": 6, "max_share_of_source": 0.3}
}
```

- `scope`: `each_city` (clauses evaluated per city; `$city` = that city's
  id) or `global` (fields are top-level state paths, e.g. `gold`).
- `when`: AND of clauses; `field` is a dotted path into the city (or the
  state); `op` ∈ `< <= > >= == !=`; a `null` field makes the clause false.
- Primitives (the only things chores can do):
  - `transport` — resource, amount, `from` (`richest` = own city with most
    of that resource excluding the target, or a city id), `to` (`$city` or
    id). Uses the catalogued `loadTransportersWithFreight` request shape.
    Clamps amount to `max_share_of_source` of the source stock and to free
    transporter capacity (500/ship); skips if 0 ships free.
  - `set_tavern_wine` — `city`, `level` (int or `max_affordable` = the
    highest level whose hourly wine the city can sustain for ≥ 12 h).
  - `set_workers` — `city`, `wood`/`tradegood`/`scientists`/`priests`
    targets (ints or `max`), via the catalogued `workerPlan` shape.
  - `catalog_action` — `name` (existing Action Catalog row) + `params`
    (values may use `$city`); only `kind=action` rows, never ones whose
    Notes mention premium/Ambrosia/real money.
- Lifecycle: `proposed` → (runner flips to) `dry-run` → after
  `DryRunsOK ≥ 3` the LLM may promote with `chores.py promote` (the human
  can also flip Status in Notion) → `active`. Two consecutive failures →
  `disabled` automatically and the LLM is woken with the reason.

Hard guardrails in the runner (not configurable by rules):
- never real-money/premium actions, never destructive actions
  (disband/abandon/leave), never `catalog_action` on unknown rows;
- at most 5 executed actions per tick across all chores;
- a Notion row named `KILL SWITCH` with Status `active` disables all execution;
- every executed/dry-run action is one line in today's Daily Log under a
  `🔧 Chores HH:MM` heading and in `session/hourly.log`.

CLI (`python3 scripts/chores.py ...`): `list`, `show NAME`,
`add NAME --why TEXT --rule JSON` (Status=proposed; validates the rule),
`promote NAME`, `disable NAME`, `run [--dry-run-all]` (used by pretick).

## 3. Pre-tick gate — `scripts/pretick.py`

1. `game.py state --json` (fails → exit 10 with reason "state unavailable").
2. Run chores (`chores.py run`).
3. Wake the LLM (exit 10) if ANY of:
   - a Human Required row is answered;
   - `alerts.under_attack` or unread messages > 0;
   - a chore was auto-disabled or failed this tick;
   - any city has no construction in progress (a build decision is due);
   - a chore is `proposed` or has `DryRunsOK ≥ 3` (review/promotion due);
   - last LLM cycle (`session/last_llm_cycle.txt`) older than 6 h;
   - daily chore review due (`session/last_review.txt` older than 24 h).
4. Otherwise exit 0 after writing `next_wake` = earliest construction or
   queue completion (+2 min), capped at +3 h and floored at +10 min.

Reasons are written one per line to `session/wake_reasons.txt`; the run
script passes them to the LLM prompt and uses `IKARAI_LIGHT_MODEL` /
`IKARAI_LIGHT_EFFORT` (if set) when every reason is a light one
(chore review/promotion, periodic check-in).
