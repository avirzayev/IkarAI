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

echo "" >> session/hourly.log
echo "=== Cycle started $(date '+%Y-%m-%d %H:%M:%S %Z') ===" >> session/hourly.log

exec flock -n session/run.lock "$CLAUDE_BIN" -p "Follow $PROJECT_ROOT/RUNBOOK.md for this cycle. If the session cookie is invalid or missing, stop, create a Human Required entry, and do not attempt to log in yourself." \
  --allowedTools "Bash,Read,Write,Edit,WebSearch,WebFetch" \
  >> "$PROJECT_ROOT/session/hourly.log" 2>&1
