# Fresh Kanban claims: heartbeat reset and lease echo fencing

Patch: `0476`. Fork commit: `36df211821def79e500d656bba07c299b84ba156`.

## Behavior

A new ready/review claim seeds its heartbeat instead of inheriting the previous run's activity. An older lease snapshot cannot shorten a newly granted claim. Newer genuine lease releases and takeovers still expire the claim.

## How it works

The Board grant transaction emits an intermediate task snapshot without its lease before emitting the final lease-bearing snapshot. Replaying that intermediate snapshot after a successful grant used to expire the fresh claim. A separate local `lease_version` records the version acknowledged by grant/renewal RPCs. Full task rows still apply their unrelated fields, but their lease portion is ignored below that floor. Lease-only changes use the same fence.

## Code

- `hermes_cli/kanban_admission.py`: atomically seed task heartbeat at claim.
- `hermes_cli/kanban_db_sync.py`: local schema column and shared lease snapshot fence.
- `hermes_cli/kanban_sync_client.py`: preserve the authoritative version in lease responses.

## Tests

New `test_kanban_claim_heartbeat_reset.py`: four cases, ready/review fresh-worker survival and current-run stale-worker controls. New `test_kanban_sync_lease_fencing.py`: seven cases, old release/null grant echoes, renewal echoes, unrelated task fields, and genuine takeover/release. Both suites were RED before their fixes.

Combined run with existing `test_kanban_worker_pid_fingerprint.py`, `test_kanban_db.py`, `test_kanban_sync_leases.py`, `test_kanban_sync_apply.py`, `test_kanban_sync_capture.py`, and `test_kanban_sync_client.py`: 141 passed, zero failures, two platform-specific skips on macOS.

## Applying

Apply the fork commit, or this patch on a compatible fork with the existing central sync seams, and use the normal idle update path. Existing processes must load the new core before affected cards are retried.

## Gotchas / plugin?

Core-only: claim transaction and sync apply are database boundaries, not plugin hooks. This changes no timeout policy. The lease response must include its authoritative task version to establish the grant fence; older clients without it retain legacy behavior. Live deployment is separate from passing tests; do not restart active chats to force it.
