# Cross-gateway card wait fallback

Patch: 0658 (a9ed7fc9ff).

## Behavior

When SSH exits 255, times out, or cannot start, `hermes kanban wait-check task <id> --gateway <name>` reads the gateway's owner-qualified default-profile task replica. It includes completed and archived rows. It does not trust legacy board-only keys. Unknown IDs, invalid ownership, missing scope observations, and a replica whose timestamp is more than 60 seconds in the future return exit 2, never exit 1. Freshness is 600 seconds. A card that is still in a replica older than that is exit 3 as of 0733 (below), not exit 2.

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
- Offline beyond ten minutes, when the card is still in the last replica, is exit 3 (unknown, not yet) until the replica is 24 h stale; then the card wakes with a gateway-offline note (0733). A missing card is still exit 2. 0658 treated every stale replica as exit 2.
- Owner-core rollout is needed to publish scheduled producer evidence. A fresh replica alone cannot recover evidence never published.
- Archived rows already removed by the collector's retention policy are unknown.
- This is core lifecycle/probe behavior, not a plugin hook.

## Follow-up patches (0733–0734, 0737)

**0733** (fork `7fe2f48e51`), **0734** (fork `68654ac016`, test-only: the schedule test asserts the stale-replica rc so ruff RUF059 stays quiet).

A card that is in a stale hermes-hub replica of an offline gateway (a sleeping Mac) is no longer a broken probe:

- `hermes kanban wait-check` exits 3, not 2. Output is `<id> <last seen status> (...)` and includes `stale_age=` and `gateway=`. It never starts with `error:`. A missing card, a replica timestamp more than 60 seconds in the future, and an orphan producer are still refused (exit 2, or the orphan refusal).
- The wake-check dispatcher re-probes exit 3 without counting it toward `BROKEN_AFTER`, until `stale_age` reaches `STALE_WAKE_AFTER` (24 h). Then the card wakes with `wake_check_broken` `reason=gateway_offline` and a note `Gateway <name> offline for N h`. Exit 3 output that lacks `stale_age=` still counts as broken.
- `kanban_schedule` with `awaiting.kind=card` accepts that observation (`confirm_remote` treats rc 3 like rc 0/1) and records the last-seen status and staleness. Missing cards and orphan producers are still refused.

Code: `hermes_cli/kanban_wait_replica.py` (`StaleReplica`, `STALE_RC`, `stale_message`), `kanban_wait_check.py` (pass rc 3 through), `kanban_wake_check.py` (`UNKNOWN_RC`, `stale_age`, `STALE_WAKE_AFTER`), `kanban_awaiting.py` (`confirm_remote`).

Tests in `tests/hermes_cli/test_kanban_wait_replica.py`: `test_missing_is_error_and_stale_known_card_is_unknown_not_yet`, `test_stale_macbook_replica_is_not_marked_broken_after_three_checks`, `test_gateway_offline_past_cap_wakes_with_offline_note`, `test_schedule_awaiting_card_on_stale_replica_is_accepted`. Ran the file on fork `origin/main` `a65917ae05` for this mirror: 25 passed (that tip also contains `c3c618c3f0`). The `kanban_block` entry path is covered by **0737** (fork `c3c618c3f0`, test-only).

**0737** pins `kanban_block(kind='time', awaiting=card@gateway)` against a stale replica (no `wake_check`, auto-filled probe): the hub refused this with `invalid_awaiting: ... stale replica` while a Mac slept, and 0733 fixed the behavior. A card the stale replica shows as missing is still refused (`invalid_awaiting`); an archived one is refused (`agent_action_wait`). Tests in the same file: `test_block_awaiting_card_on_stale_replica_is_accepted`, `test_block_awaiting_card_on_stale_replica_refuses_missing_or_archived`. Re-run on `a65917ae05`: 25 passed.
