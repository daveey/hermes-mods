# Read-only origin ownership snapshot (abandonment guard)

## Behavior

`hermes_cli.kanban_origin_snapshot.ownership_snapshot(...)` answers one question without side effects: does the exact owner task (gateway, profile, board, task id) still have a live continuation AND a result consumer able to return to the exact origin request (uid + digest)? The answer is `live` only when every proof is present; anything absent, stale, ambiguous or changed between two reads is `unknown`. It never returns `abandoned`: absence of evidence is not evidence of abandonment.

It returns the qualified binding generation (version, hash, publication/observation version, released), the owner-native running claim with PID and worker start time, or a dispatcher/wake proof for a scheduled task, and scoped return-consumer health with an `epoch` and `incarnation`.

## How it works

- Stores (ledger, `state.db`, owner board) are opened strictly `mode=ro` plus `PRAGMA query_only`. There is deliberately no `mode=rw` fallback (unlike `kanban_origin._ro`): closing the last rw connection on a WAL store can checkpoint and delete the WAL. An unreadable store gives `unknown`. Nothing refreshes observations, adopts, claims, migrates, sends courier requests or starts pollers.
- A local owner is always read locally (`owner_evidence`), then re-read with a fresh clock after composing; a remote owner answers through `hermes kanban ownership-snapshot --request-file - --json` over the existing hub v2 courier (no parallel courier). Remote evidence is accepted only if scope, binding id, TTL (30 s), claim expiry and wake time validate; its reason strings are sanitized.
- `tui_gateway.kanban_consumer_health.snapshot()` reports availability plus a process-wide `epoch` that advances on registration/replacement, Stop/disconnect (stopped or dead poller), and on `invalidate(session)`, which refusal backoff (`session_notifications`) and `_finalize_session` call at the moment scope is lost. Invalidation clears the session's receipts, so a receipt minted before it needs a fresh poll even if nobody read during the outage. Reads never mutate state. `incarnation` identifies the newest consumer holding a fresh receipt.
- `expected_consumer_epoch` lets a caller detect invalidation between its own reads (`consumer_epoch_changed`).

## Code

`hermes_cli/kanban_origin_snapshot.py`, `hermes_cli/kanban_origin_owner.py` (`snapshot_command`), `hermes_cli/kanban.py` and `kanban_parser.py` (`ownership-snapshot`), `tui_gateway/kanban_consumer_health.py`, `tui_gateway/session_notifications.py`, `tui_gateway/session_lifecycle.py`.

## Tests

`tests/hermes_cli/test_kanban_origin_snapshot.py` (live/unknown matrix, no writes or adoption, strict ro open on a WAL store, remote evidence validation, owner CLI route check) and `tests/tui_gateway/test_kanban_consumer_health.py` (epoch on replacement, Stop, invalidate). Untested: the `changed_during_read` race path and a concurrent register/snapshot stress.

## Applying

Patch 0523 (real-home guard accepts linked worktrees) is a prerequisite for running these tests in a linked worktree; 0524 is the feature. Apply in order.

**0589** (source `a2c2b3eba8`), test-only follow-up to the 0523 guard: a read of `<checkout>/../manifest.json` is allowed, because `hermes_bootstrap`'s sealed-payload probe reads that sibling on first import. Writes of that file, and any other sibling, stay refused. Test: `test_payload_manifest_beside_a_guarded_checkout_is_readable_not_writable` in `tests/hermes_cli/test_real_home_tripwire.py`. The same commit's capability-routing PATH isolation is covered in [kanban-capability-routing.md](kanban-capability-routing.md).

## Gotchas

- Remote evidence is not authenticated beyond scope/binding/TTL checks; the courier envelope id is not yet echoed by the owner. The origin-side caller that sends the courier request is not part of this change.
- Owner `observed_at` is compared with the origin's clock; skew yields `owner_evidence_stale`.
- The epoch is process-global, so unrelated registrations can yield a conservative `changed_during_read`.
