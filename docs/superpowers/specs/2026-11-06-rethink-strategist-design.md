# Rethink Strategist — Design

**Date:** 2026-11-06
**Status:** Design approved section by section; awaiting review of this written spec.

## Problem

The agent follows its plan but never questions it. A review on 2026-11-06 found:

- Daemon held 716k marble while other cities could not build, moved at 6,000 per
  trip because `idle extra merchant ships` stayed on the DECLINED list.
- The corruption fix that worked at Daemon was never finished at Syscall and Async.
  The plan was lost when the Strategy page was condensed.
- Wine: the agent recorded `ServeBonus(0)` and skipped wine, against its own rule.
- Kernel lost about 2,192 wood/hour at its storage cap.
- Marble went to warehouses with plenty of free capacity.

Underlying causes: cycles have no memory, `state.json` is overwritten so no trend
is visible, the runbook gives no goal, wakes are driven by "no construction in
progress", and DECLINED items are never re-examined.

## Goal

Let the agent notice when its plan is failing and change direction, including
reversing its own rules and reordering priorities, with a record that lets bad
pivots be caught.

**Owner priority order (default):** economy > military > ranking > expansion. The
agent may reorder these with a written justification.

## Design overview

Normal cycles keep carrying out the plan. A separate, deeper strategist run decides
what the plan should be.

```
cron -> ticker.sh -> run_hourly_cycle.sh
                       |- pretick.py  appends a metrics line, evaluates triggers
                       |- [rethink reason present] strategist (RETHINK.md, read-only game)
                       |- normal cycle (RUNBOOK.md)
```

## Components

### 1. Scoreboard: `scripts/metrics.py` (new)

Each pre-tick appends one JSON line to `session/metrics.jsonl`, with no model use.
Priorities are tracked separately; there is no combined score, because one could
hide a collapse in one area behind a gain in another.

| Area | Fields |
|---|---|
| Economy | total population; per-city population/max; production per hour; gold and gold per hour; resources at cap |
| Logistics | per-city stock; fleet max; fleet capacity (ships x 500) |
| Military | army and fleet strength, or `null` |
| Ranking | rank and score, or `null` |
| Expansion | towns used |
| Wake | the reasons computed for this tick |

- Economy, logistics and expansion come from data `state.json` already has.
- Military stays `null` for now, with no extra game traffic. The saved
  `view:militaryAdvisor` response (checked 2026-11-06) only lists movements and
  occupied towns, not army strength. Finding a source is on the strategist's
  research list.
- Ranking stays `null` until a view is discovered: the action catalog has none.
- `metrics.py summary` prints 7-day trends compactly for the strategist. Missing
  fields are shown as "unavailable", never as zero.
- Lines older than 90 days are pruned.

### 2. Triggers: `scripts/pretick.py`

Adds wake reasons prefixed `rethink:`. In `propose` and `live` modes, any such reason
wakes the model (exit 10) and runs the strategist before the normal cycle. In `log`
mode, triggers are written to `hourly.log` as `[rethink-shadow]` lines, produce no
wake reason and use no model. In `off` mode, they are not evaluated. Thresholds are
module constants.

| Trigger | Fires when |
|---|---|
| Daily | 24 hours since the last completed rethink |
| Stagnant | total population and total production each up less than 1% over 48 hours; needs 48 hours of data |
| Stuck | the last 6 model wakes have identical non-rethink reason sets |
| Stranded stock | for a resource, one city holds over 20 x fleet capacity while another holds under 1 x fleet capacity |
| Wasted cap | a city's resource at 98% or more of its cap continuously for 24 hours |
| Empty city | a city under 70% of max population continuously for 24 hours |
| Return from outage | no successful model cycle for 12+ hours |
| Requested | a normal cycle ran `kb.py rethink "reason"` |

**Safeguards**

- At least 6 hours between rethink runs, and at most 3 per rolling 24 hours. A
  suppressed trigger logs a line and does not produce a `rethink:` reason.
- Failed runs count toward these limits, so a broken strategist cannot loop.
- Attack and message wakes are `heavy:` reasons, so a normal cycle still runs
  immediately regardless of these limits.
- Metrics or trigger errors are logged and ignored; the existing pre-tick logic and
  normal cycles proceed. This matches the current fail-open behaviour.

**Prerequisite fix:** `last_llm_cycle.txt` is written even when a cycle fails, so it
cannot detect outages. The launcher adds `session/last_model_success.txt` and
`session/model_failures.txt` (consecutive failure count), derived from
`last_run.json` (`is_error`) and the exit status.

### 3. Auth and failure alert: `scripts/pretick.py`

A login failure would break rethinks too, so this does not use the model. After 3
consecutive failed model cycles, pre-tick files one Human Required row titled
`Model cycles failing — check Claude login`, including the last error text. No
duplicate is filed while one is open. Failure to file is logged and ignored.

### 4. Launcher: `scripts/run_hourly_cycle.sh`

When wake reasons include `rethink:` and `IKARAI_RETHINK_MODE` is not `off`/`log`:

1. Run `claude -p` with `RETHINK.md` as the appended system prompt, using the same
   lean flags as normal cycles, inside the existing `flock`.
2. Set `IKARAI_READ_ONLY=1`, and in `propose` mode `IKARAI_STRATEGY_READ_ONLY=1`.
3. Write output to `session/last_rethink_run.json` and log usage through
   `log_usage.py`.
4. Update `session/last_rethink.txt` and the run history used by the safeguards.
5. Run the normal cycle regardless of the strategist's result, without `rethink:`
   lines in its wake reasons.

**Settings (`.env`)**

| Variable | Default | Meaning |
|---|---|---|
| `IKARAI_RETHINK_MODE` | `log` | `off`: disabled. `log`: triggers log only. `propose`: strategist writes `session/rethink_proposal.md`. `live`: strategist updates the Strategy. |
| `IKARAI_RETHINK_MODEL` | `claude-opus-5-5` | Strategist model |
| `IKARAI_RETHINK_EFFORT` | `high` | Strategist effort |
| `IKARAI_RETHINK_MAX_BUDGET_USD` | `5` | Per-run cap |

The approved `IKARAI_RETHINK=0` off switch is implemented as
`IKARAI_RETHINK_MODE=off`, so one setting covers every stage.

### 5. Strategist instructions: `RETHINK.md` (new)

Reads: `metrics.py summary`, `game.py state`, `kb.py context`, recent Daily Log
entries, `session/pivots.jsonl`, and the trigger reasons.

Hard rules are repeated and cannot be changed by the strategist: no real-money
purchases, no logging in, no captchas, no anti-bot evasion, no unverified game
actions.

It must work through, in order:

1. **Grade past pivots.** Did recent changes do what they predicted? If not, why?
2. **Find the main limit.** What one thing most holds back the top priority, in numbers?
3. **Challenge the rules.** Re-justify every assumption and DECLINED item against
   today's numbers. Drop any that cannot be justified.
4. **Research.** Check the mechanics behind each option: knowledge base, then Ikariam
   wiki, then web search. Every rethink also looks for unused mechanics (buildings,
   research, government, actions), and for the missing ranking view and how attacks
   on players work. Save findings with `kb.py knowledge add`, marked as external
   research. Live game data beats the wiki when they disagree.
5. **Compare options.** At least two real alternatives against the current plan, by
   expected payoff toward the priorities.
6. **Decide.** Keep or change the plan and priorities, and write the outputs below.

It takes no game actions. Token-saving rules are relaxed, limited by the budget cap.

### 6. Outputs

**Strategy page**, within the existing 3,500-character cap and archived on change:

- `GOALS`: priority order and why
- `MAIN LIMIT`: the current bottleneck, in numbers
- `PLAN`: ordered steps
- `DECISIONS`: active rules
- Keeps the reference sections normal cycles rely on (empire, logistics, diplomacy)

**Decision rules.** Every `DECISIONS` line needs a reason and a re-check condition,
for example: `no warehouses - because stock is far below cap - recheck if any city
reaches 90%`. Hard rules are exempt because they live in the runbook. The first
rethink migrates the current `DECLINED` section into `DECISIONS`.

**Pivot history**, `session/pivots.jsonl` (append-only), one JSON line per rethink:
`time`, `triggers`, `main_limit`, `changes`, `expected`, `check_by`, `sources`,
`grade_of_previous`. A run that keeps the plan still writes a line with empty
`changes`. A Daily Log entry summarizes each run. In `propose` mode, the proposal file
replaces all of these: the Strategy, pivot history and Daily Log are left untouched.
Knowledge base additions are still allowed, since they are facts rather than decisions.

### 7. Read-only and validation: `scripts/game.py`, `scripts/kb.py`

- **`game.py`:** with `IKARAI_READ_ONLY=1`, `action` commands, `action` lines inside
  `batch`, and `set-cookie` are refused with a clear message; `nav` still works. This
  guards against mistakes. It is not a security boundary, because the strategist has
  Bash.
- **`kb.py rethink "reason"` (new):** writes `session/rethink_request.txt`, which
  pre-tick consumes as the Requested trigger.
- **`kb.py strategy`:** refuses `DECISIONS` lines missing a reason or re-check
  condition, for both `set` and `section`. With `IKARAI_STRATEGY_READ_ONLY=1`, it
  refuses all strategy writes.

### 8. Runbook: `RUNBOOK.md`

- Follow the Strategy's `GOALS`, `MAIN LIMIT` and `PLAN`.
- Normal cycles may not reverse a `DECISIONS` line unless its re-check condition is
  met. If the plan looks wrong, run `kb.py rethink "reason"` and continue.
- **Attack policy (owner decision):** the agent may attack anyone on its own
  judgment, recording target, expected gain and retaliation risk in the Daily Log.
  The rule against unverified actions still applies. Only
  `attackBarbarianVillage` is catalogued, so attacks on players require first
  discovering and recording the real request.

## Failure handling

| Failure | Behaviour |
|---|---|
| Strategist crashes, times out or hits budget | Strategy unchanged; failure logged; counts toward safeguards; normal cycle runs |
| Strategist writes an invalid `DECISIONS` line | `kb.py` refuses it; old section stays |
| Metrics or trigger error | Logged; normal pre-tick logic continues |
| Missing data (military, ranking, short history) | Field `null`; dependent triggers do not fire |
| Claude login expires | Cycles fail; Human Required row after 3 failures |
| Bad pivot | Graded at the next rethink; old Strategy versions remain archived |

## Testing

Written test-first, extending `tests/test_pretick.py`, `tests/test_game.py` and
`tests/test_kb.py`, with new `tests/test_metrics.py`:

- **Today's-state replay:** a metrics series built from the real 2026-11-06 state
  fires Stranded stock, Wasted cap and Empty city.
- **Healthy series:** a growing empire fires only Daily.
- Each trigger's boundaries, and short or missing history.
- Safeguards: 6-hour gap, 3-per-24-hours cap, failed runs counted, attack wakes
  unaffected.
- Auth alert: fires at 3 failures, no duplicates, success resets the count.
- Read-only mode refuses `action`, `batch` action lines and `set-cookie`, allows `nav`.
- `DECISIONS` validation accepts and rejects correctly.
- Launcher: strategist runs before the normal cycle; its failure does not block the
  normal cycle; `off`, `log`, `propose` and `live` behave as specified. Uses a stub
  `claude` via `CLAUDE_BIN`.

## Rollout

The server's code is byte-identical to `origin/master` (175c11b, checked 2026-11-06),
but its `.git` is corrupted.

1. **Build and test:** PR on `avirzayev/IkarAI`.
2. **Log only:** rebuild the server's `.git`, pull, keep `IKARAI_RETHINK_MODE=log`.
   Run about a day and check for false alarms.
3. **Supervised rethink:** run once in `propose` mode. The owner reads
   `session/rethink_proposal.md` and decides whether to continue.
4. **Live:** set `live`. Check the first few pivots against the scoreboard and report.

Only one copy of the agent may ever run, since the game session is shared. All
server changes happen while `session/run.lock` is free.

## Success criteria

Within 7 days of going live:

- Syscall and Async are at about 3% corruption.
- Colonies are growing toward max population, or the strategist has recorded why not.
- No resource sits at cap for more than 24 hours without a recorded reason.
- Every `DECISIONS` line has a reason and re-check condition.
- At least one pivot has been graded against its prediction.
- Actual rethink cost is measured and reported against the $1–3 estimate.

## Out of scope

- Debate between multiple agents (could be added later if one strategist proves too narrow)
- Email alerts (Human Required row only for now)
- A dashboard card for pivots
- The `export-strategy.sh` missing timeout (separate small fix)
- Building player-attack automation; the agent discovers that itself under existing rules
