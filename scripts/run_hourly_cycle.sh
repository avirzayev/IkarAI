#!/usr/bin/env bash
# /opt/ideas/games/ikarAI/scripts/run_hourly_cycle.sh
set -euo pipefail
cd /opt/ideas/games/ikarAI
exec claude -p "Follow /opt/ideas/games/ikarAI/RUNBOOK.md for this hour's IkarAI cycle. If the session cookie is invalid or missing, stop and notify rather than attempting to log in yourself." \
  --allowedTools "Bash,Read,Write,Edit" \
  >> /opt/ideas/games/ikarAI/session/hourly.log 2>&1
