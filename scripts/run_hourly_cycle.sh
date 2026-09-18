#!/usr/bin/env bash
# /opt/ideas/games/ikarAI/scripts/run_hourly_cycle.sh
# Invoked by scripts/ticker.sh once the agent's self-declared next_wake
# time has passed — not on a fixed schedule. flock prevents two cycles
# from overlapping if a run takes a while.
set -euo pipefail
cd /opt/ideas/games/ikarAI
# cron runs with a minimal PATH that doesn't include ~/.local/bin, where
# `claude` actually lives — call it by full path rather than by name.
CLAUDE_BIN="${CLAUDE_BIN:-$HOME/.local/bin/claude}"

# Load .env (TIMEZONE among everything else) so logs/dates use the
# configured local timezone instead of the server's default (UTC).
set -a
source .env
set +a
export TZ="${TIMEZONE:-UTC}"

echo "" >> session/hourly.log
echo "=== Cycle started $(date '+%Y-%m-%d %H:%M:%S %Z') ===" >> session/hourly.log

exec flock -n session/run.lock "$CLAUDE_BIN" -p "Follow /opt/ideas/games/ikarAI/RUNBOOK.md for this cycle. If the session cookie is invalid or missing, stop, create a Human Required entry, and do not attempt to log in yourself." \
  --allowedTools "Bash,Read,Write,Edit,WebSearch,WebFetch" \
  >> /opt/ideas/games/ikarAI/session/hourly.log 2>&1
