# Cross-gateway card wait fallback

Patch: 0658 (a9ed7fc9ff).

## Behavior

When SSH exits 255, times out, or cannot start, `hermes kanban wait-check task <id> --gateway <name>` reads the gateway's owner-qualified default-profile task replica. It includes completed and archived rows. It does not trust legacy board-only keys. Unknown IDs, invalid ownership, missing scope observations, and stale replicas return exit 2, never exit 1. Freshness is 600 seconds with 60 seconds of forward clock skew allowed.

Producer confirmation and automatically generated wake probes share this route. Orphan producers are refused on both SSH and replica paths. Hand-written `hermes-hub tasks` probes containing task IDs are refused with the supported command as remedy.

## How it works

`kanban_wait_replica.read_task` reads task and task-scope heartbeat in one SQLite snapshot. Task edit timestamps are not freshness evidence. `check_remote` uses this reader only for transport failures, not ordinary remote application errors. The owner collector adds `wait_observation` for scheduled and blocked cards, recording orphan reasons and agent-owned parks from the same owner snapshot. This requires an updated owner core. Older scheduled snapshots lacking this evidence fail closed when `parked-agent` is requested.

## Code

- `hermes_cli/kanban_wait_replica.py`: exact record read, freshness, status mapping, owner wait evidence.
- `hermes_cli/kanban_wait_check.py`: SSH fallback and local orphan markers.
- `hermes_cli/kanban_awaiting.py`: producer confirmation.
- `hermes_cli/kanban_origin_ledger.py`: owner publication.
- `hermes_cli/kanban_wake_check.py`: probe refusal and help text.

## Tests

`scripts/run_tests.sh tests/hermes_cli/test_kanban_wait_replica.py`: 19 passing cases, covering completed/archived/blocked/running/scheduled status, missing/stale scope, qualified vs legacy keys, transport exceptions, confirmation refusal, implicit probe --from, identity isolation, supported/unsupported probe commands, owner publication and SSH orphan parity. Initial regressions: nine failures before implementation.

Full `tests/hermes_cli/test_kanban*.py`: final rebased run 2,195 passed, zero failures, eight platform skips. Baseline sweep: 2,155 passed, zero failures. All 12 `scripts/check` checks pass. Two independent reviews were performed and their findings addressed.

Live CLI and core-transition proof on a hub with no SSH route: a real completed remote card returned 0; an unknown ID returned 2; a continuation park was accepted; scheduled status and the automatic probe were read back; disposable fixtures were archived. Tool/REST and Desktop end-to-end flows are not separately exercised.

## Applying

Apply after 0657 with `git am -3`. The three-patch append 0656–0658 is checked against the core revision represented by 0655. Keep the existing reconciled public series numbering rather than renumbering private equivalent commits.

## Gotchas

- Default-profile records only: this command has no remote profile selector.
- Offline beyond ten minutes is unknown, not indefinitely pending.
- Owner-core rollout is needed to publish scheduled producer evidence. A fresh replica alone cannot recover evidence never published.
- Archived rows already removed by the collector's retention policy are unknown.
- This is core lifecycle/probe behavior, not a plugin hook.
