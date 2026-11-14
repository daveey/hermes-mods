# Board feed clears this host’s dead local claims

Patches: 3796, 3797.

## Behavior

When the Board applies a failure release this host queued, the local claim clears in that same feed transaction. Retries need not wait for leftover-claim grace; give-ups to triage no longer retain the dead worker claim forever.

## How it works

`kanban_db_sync` calls `release_on_feed` after applying task columns. The helper requires a transition off `running`, a local lock, a current run UID with this task's queued failure command, and a worker no longer alive. Its UPDATE compares the observed lock/PID and target status before clearing claim, expiry, worker and heartbeat fields. The general leftover sweep also includes `triage`.

The restart-resume test now expects claim/PID to be empty immediately after feed application; the later leftover sweep must do nothing. This distinguishes authoritative feed settlement from delayed repair.

## Code

- `hermes_cli/kanban_db_dispatch_claims.py`
- `hermes_cli/kanban_db_sync.py`
- `hermes_cli/kanban_release_claim.py`

## Tests

**Tests were not rerun by this documentation pass.** The assertions below describe the staged test diffs, not fresh execution results. Full replay verification is owned by the parent mirror pass; no deployment or live-runtime claim is made.

- `tests/hermes_cli/test_kanban_leftover_local_claim.py` (patch 3796).
- `tests/hermes_cli/test_kanban_release_claim.py` (patch 3796).
- `tests/gateway/test_session_kanban_restart_resume_unused_grant.py` (patch 3797).

- **3796:** Primary crash-release feed tests require immediate cleared local claim/PID fields for ready/triage/blocked, immediate retry claimability and triage archiving; no queued release, live worker or still-running feed preserves the claim. Leftover sweep parameterization adds triage.
- **3797:** The restart-resume crash fixture requires claim_lock and worker_pid already cleared after feed apply, and the subsequent leftover sweep returns an empty list.

## Applying

Use the complete ordered series and the [README Applying recipe](README.md#applying), including its historical integration bridges and ancestry-only handling where listed. Then run the mandatory snapshot-bound [hostname escape repair](hostname-escape-repair.md) and repaired-tree checks specified there. These ordinals are documentation groupings, **not independent cherry-picks**; do not apply just this subset or a one-glob series that omits bridges. The parent mirror pass must establish replay/tree verification for the final snapshot.

## Gotchas

A status move alone is not authority to clear a claim. Foreign, live-worker, still-running and unproven-release claims remain protected. The helper's compare-and-set prevents clearing a replacement claim from a later claimant.

## Plugin?

No. This needs atomic integration with core feed application and local-only claim columns.
