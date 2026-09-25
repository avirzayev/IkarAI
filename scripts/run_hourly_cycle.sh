#!/usr/bin/env bash
# scripts/run_hourly_cycle.sh
# Invoked by scripts/ticker.sh once the agent's self-declared next_wake
# time has passed — not on a fixed schedule. flock prevents two cycles
# from overlapping if a run takes a while.
set -euo pipefail
# Project root is wherever this script actually lives, not a hardcoded
# path — makes the same committed script work regardless of install
# location (e.g. /opt/ideas/games/ikarAI on one machine, /root/IkarAI
# on another).
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"
# cron (and proot-distro login shells) run with a minimal PATH that may
# not include wherever `claude` actually lives — resolve it explicitly,
# overridable via CLAUDE_BIN for environments where it's installed
# somewhere unusual (e.g. a proot-distro container's /usr/local/bin).
CLAUDE_BIN="${CLAUDE_BIN:-}"
if [ -z "$CLAUDE_BIN" ]; then
  if command -v claude >/dev/null 2>&1; then
    CLAUDE_BIN="$(command -v claude)"
  elif [ -x "$HOME/.local/bin/claude" ]; then
    CLAUDE_BIN="$HOME/.local/bin/claude"
  else
    CLAUDE_BIN="claude"
  fi
fi

# Load .env (TIMEZONE among everything else) so logs/dates use the
# configured local timezone instead of the server's default (UTC).
set -a
source .env
set +a
export TZ="${TIMEZONE:-UTC}"

# Model/effort are pinned here rather than inherited from ~/.claude
# settings, which this run deliberately doesn't load (see below).
MODEL="${IKARAI_MODEL:-claude-sonnet-5}"
EFFORT="${IKARAI_EFFORT:-medium}"
BUDGET_ARGS=()
if [ -n "${IKARAI_MAX_BUDGET_USD:-}" ]; then
  BUDGET_ARGS=(--max-budget-usd "$IKARAI_MAX_BUDGET_USD")
fi

# The ticker fires every minute; if the previous cycle is still running,
# quietly skip (before logging anything, so the log isn't spammed).
exec 9>session/run.lock
flock -n 9 || exit 0

echo "" >> session/hourly.log
echo "=== Cycle started $(date '+%Y-%m-%d %H:%M:%S %Z') ===" >> session/hourly.log

# Token diet:
# - --setting-sources project,local: skip ~/.claude user settings, so the
#   operator's personal plugins/hooks/skills (superpowers, ecc, ...) don't
#   ride along in every turn's context.
# - autoMemoryEnabled false: cycles are stateless by design (state lives
#   in Notion); auto-memory only added noise, e.g. nudging DESIGN.md reads.
# - --strict-mcp-config / --no-chrome / --disable-slash-commands: no MCP
#   servers, browser tools or skill listings.
# - --tools: only the tool definitions this agent actually uses.
# - RUNBOOK.md goes in the system prompt (cached) instead of being Read.
# Output is JSON so log_usage.py can record per-cycle token usage in Notion.
WAKE_BEFORE="$(stat -c %Y session/next_wake.txt 2>/dev/null || echo none)"
set +e
"$CLAUDE_BIN" -p "Run one IkarAI cycle now, following the runbook in your system prompt." \
  --model "$MODEL" \
  --effort "$EFFORT" \
  --output-format json \
  --setting-sources project,local \
  --settings '{"autoMemoryEnabled": false}' \
  --strict-mcp-config \
  --no-chrome \
  --disable-slash-commands \
  --tools "Bash,Read,Write,Edit,WebSearch,WebFetch" \
  --allowedTools "Bash,Read,Write,Edit,WebSearch,WebFetch" \
  --append-system-prompt "$(cat "$PROJECT_ROOT/RUNBOOK.md")" \
  "${BUDGET_ARGS[@]}" \
  < /dev/null > session/last_run.json 2>> session/hourly.log
STATUS=$?
set -e

# Safety net: a cycle that ends without `kb.py wake` (crash, budget cap,
# or the agent pausing to wait on something) would otherwise leave a stale
# next_wake and re-run every minute. Schedule a retry and say so in the log.
if [ "$(stat -c %Y session/next_wake.txt 2>/dev/null || echo none)" = "$WAKE_BEFORE" ]; then
  echo "[safety-net] cycle ended without setting next_wake — scheduling a retry in 30 min" >> session/hourly.log
  python3 scripts/kb.py wake +30 >> session/hourly.log 2>&1 || true
  python3 scripts/kb.py log add "⚠️ This cycle ended before finishing (no log entry or wake time was written). Retrying in 30 minutes — check session/hourly.log." >> session/hourly.log 2>&1 || true
fi

python3 scripts/log_usage.py session/last_run.json --exit-code "$STATUS" >> session/hourly.log 2>&1 || true
exit "$STATUS"
