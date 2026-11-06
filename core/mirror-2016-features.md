# Mirror 2016 feature: unknown owner task scope

This patch extends snapshot 2015. It applies directly after 2015 with no new
merge or bridge; follow the full [Applying recipe](README.md), not a standalone
cherry-pick. Execution results and limits are in
[mirror-2016-verification.md](mirror-2016-verification.md).

## 2016: unknown owner task scope is unknown-not-yet

### Behavior
A remote task wait (`hermes kanban wait-check task <id> --gateway <gw>`) whose
owner gateway publishes its task scope as `state: unknown` (the owner's
collector could not read that board for longer than its heartbeat carry
window) is classified as **unknown, not yet**: exit 3 with
`source_state=unknown gateway=<gw>`. It is never treated as completion or as
fresh evidence, and it is not a broken probe.

- The first such result on a parked card wakes it once with
  `wake_check_broken` reason `gateway_scope_unknown` (fails=0, its own context
  header), so a human-visible escalation still happens.
- After the card reparks on the same task/probe/gateway, further unknown
  results only update the probe row while a `gateway_offline` or
  `gateway_scope_unknown` receipt is under 24 hours old. It re-escalates at most
  once per 24 hours.
- When the owner publishes a successful scope again, normal 0/1/stale handling
  resumes (a done task wakes with exit 0).
- Missing, ambiguous or other-state scopes, `unknown` with an `observed_at`,
  future timestamps, invalid tasks and DB errors stay exit 2 and still count
  toward the three-failure broken-probe wake.

### How it works / Code
- `hermes_cli/kanban_wait_replica.py`: `read_task` raises a typed
  `UnknownScope` (carrying the last-seen task) only for exactly one matched
  scope with `state == "unknown"` and no `observed_at`; `check()` maps it to
  exit 3 without inventing a `stale_age`, and never evaluates until/since.
- `hermes_cli/kanban_wake_check.py`: `source_unknown()` recognizes that
  result; it never increments the BROKEN_AFTER streak, applies the shared
  24-hour receipt family and is accepted by the dry run so the acknowledging
  repark is not refused. `_remote_completed_at` reads the last-seen record as
  for `StaleReplica`.
- `hermes_cli/kanban_offline_backoff.py`: `recently_escalated` takes a reason
  family (default unchanged: `gateway_offline` only).

### Tests
`tests/hermes_cli/test_kanban_wait_replica_unknown_scope.py`,
`tests/hermes_cli/test_kanban_offline_backoff.py` and
`tests/hermes_cli/test_kanban_wait_replica.py` pass on the source tree at
`d9484acb9d3158bad78466793541023278597149`; results on the replayed public tree
are recorded in the verification document. They cover the producer's unknown
shape, first escalation, wake→claim→repark backoff, 24-hour re-escalation,
recovery to a done task (exit 0) and genuine-fault controls (exit 2 still
breaks after three). The cross-repo producer proof (real hermes-hub
`collect_all` → outbox → `pull()` → core check) ran in the source session only
and is not part of this repository's tests. Not tested here: the awaiting
`confirm_remote` paths for unknown→accepted and done→producer_done_error.

### Applying / Gotchas / plugin?
Patch 2016 (`d9484acb9d3158bad78466793541023278597149`) builds on 2015's
offline repark cooldown (shared receipt family). Why the owner's board reads
failed in the originating incident is not determined by this patch. Core
change, no plugin.
