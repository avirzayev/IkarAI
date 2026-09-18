#!/usr/bin/env bash
# /opt/ideas/games/ikarAI/scripts/ticker.sh
# Runs every minute via cron. Only invokes the real cycle once the
# agent's self-declared next_wake.txt time has passed (or isn't set
# yet, e.g. before the first-ever run).
set -euo pipefail
cd /opt/ideas/games/ikarAI

NEXT_WAKE_FILE="session/next_wake.txt"
NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)

if [ -f "$NEXT_WAKE_FILE" ]; then
  NEXT_WAKE=$(tr -d '[:space:]' < "$NEXT_WAKE_FILE")
  if [ -n "$NEXT_WAKE" ] && [[ "$NOW" < "$NEXT_WAKE" ]]; then
    exit 0
  fi
fi

exec /opt/ideas/games/ikarAI/scripts/run_hourly_cycle.sh
