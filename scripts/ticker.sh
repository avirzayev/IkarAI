#!/usr/bin/env bash
# scripts/ticker.sh
# Runs every minute via cron. Only invokes the real cycle once the
# agent's self-declared next_wake.txt time has passed (or isn't set
# yet, e.g. before the first-ever run).
set -euo pipefail
# Project root is wherever this script actually lives, not a hardcoded
# path — makes the same committed script work regardless of install
# location (e.g. /opt/ideas/games/ikarAI on one machine, /root/IkarAI
# on another).
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

NEXT_WAKE_FILE="session/next_wake.txt"
NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)

if [ -f "$NEXT_WAKE_FILE" ]; then
  NEXT_WAKE=$(tr -d '[:space:]' < "$NEXT_WAKE_FILE")
  if [ -n "$NEXT_WAKE" ] && [[ "$NOW" < "$NEXT_WAKE" ]]; then
    exit 0
  fi
fi

exec "$PROJECT_ROOT/scripts/run_hourly_cycle.sh"
