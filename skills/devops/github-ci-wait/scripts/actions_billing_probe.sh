#!/bin/bash
# Poll until GitHub Actions stops refusing jobs for billing (spending limit / failed payment).
# Usage: actions_billing_probe.sh OWNER/REPO [workflow-file, default gitleaks.yml] 
# Env: INTERVAL (s, default 300), MAX_TRIES (default 144 = 12 h at 5 min).
# Each probe re-runs the newest run of a CHEAP workflow and inspects its jobs:
#   annotation contains 'spending limit'  -> still blocked
#   any job has steps or is in_progress   -> Actions is running again (prints 'ACTIONS OK', exit 0)
# Prints 'STILL BLOCKED' and exits 1 after MAX_TRIES. Start with terminal(background=true,
# notify=['ACTIONS OK','STILL BLOCKED'], persist_on_release=true) and end the turn.
REPO=${1:?OWNER/REPO}; WF=${2:-gitleaks.yml}
INTERVAL=${INTERVAL:-300}; MAX_TRIES=${MAX_TRIES:-144}
export PATH=/opt/homebrew/bin:/usr/local/bin:$PATH
blocked() {
  local st jid n
  for _ in 1 2 3 4 5 6; do
    sleep 20
    st=$(gh api repos/$REPO/actions/runs/$1 --jq .status 2>/dev/null)
    [ "$st" = completed ] && break
    [ "$st" = in_progress ] && return 1
  done
  for jid in $(gh api repos/$REPO/actions/runs/$1/jobs --jq '.jobs[].id' 2>/dev/null); do
    gh api repos/$REPO/check-runs/$jid/annotations --jq '.[].message' 2>/dev/null | grep -q 'spending limit' && return 0
    n=$(gh api repos/$REPO/actions/jobs/$jid --jq '.steps|length' 2>/dev/null)
    [ "${n:-0}" -gt 0 ] && return 1
  done
  return 0
}
for i in $(seq 1 "$MAX_TRIES"); do
  rid=$(gh api "repos/$REPO/actions/workflows/$WF/runs?per_page=1" --jq '.workflow_runs[0].id')
  gh run rerun "$rid" -R "$REPO" >/dev/null 2>&1
  if ! blocked "$rid"; then
    echo "ACTIONS OK at $(date '+%H:%M %Z') after probe $i (run $rid)"; exit 0
  fi
  echo "$(date '+%H:%M') probe $i: still billing-blocked (run $rid)"
  sleep "$INTERVAL"
done
echo "STILL BLOCKED after $MAX_TRIES probes"; exit 1
