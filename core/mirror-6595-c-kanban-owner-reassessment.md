# Bounded owner reassessment

**Scope:** Opt-in reassessment parks, target observation, replica quarantine, evidence overflow.

**Source ordinals:** 6533, 6534, 6535, 6536, 6537, 6538

## Behavior

- An opt-in request (version, reassess_at, target, evidence refs, no caller-supplied authority fields) may shorten an already-admitted park wake; it never extends it and preserves the original final backstop. The immutable receipt is written in the same park transaction after revalidating actual private owner custody; ordinary continue parks are admitted first.
- Target selectors of the form owner-target:<label>/<label> are observed read-only from the source host with fenced Git reads; observations are not health attestations. A child reap allowance is independent of an expired observation deadline.
- Sync transport is gated, contradictory receipts are quarantined per card, deterministic evidence overflow is quarantined while refused optional parks converge, and oversized HTTP failures stay retryable without quarantining cards. When the Board refuses only the certificate, the opt-in park is resent without it so the status still lands. Quarantine kinds that cannot be cleared by the generic release say so and grant no authority.
- The pending receipt is an immutable journal row (`execution_authority` false, `suppresses: ['too_long']` as data only); nothing in this series installs a scanner exemption. Only a Board-attested (`owner_reassessment_v1`) receipt matching the canonical park event, size-capped at 256 KiB, can be certified, and a later park supersedes it. Opt-ins need a timed park on an exact structured producer (card, ci, deploy or external; not inferred or shared-blocker), 1-16 distinct `task_events` evidence uids already on the owner run, and a target kind of queue, update or external.

## How it works

- `kanban_owner_reassessment.py` (validate/plan/prepare/commit with a pending journal table), `_canonical.py`, `_replica.py`, `kanban_owner_target.py`, `kanban_continuation.py`, `kanban_db_schedule.py`, sync envelope/compat/quarantine/hub-transport modules, and `tools/kanban_tools_reassessment.py`.

## Code

Files touched by the series (paths relative to the source tree):

- `hermes_cli/kanban_board_row_fields.json`
- `hermes_cli/kanban_continuation.py`
- `hermes_cli/kanban_db_schedule.py`
- `hermes_cli/kanban_db_sync.py`
- `hermes_cli/kanban_owner_reassessment.py`
- `hermes_cli/kanban_owner_reassessment_canonical.py`
- `hermes_cli/kanban_owner_reassessment_replica.py`
- `hermes_cli/kanban_owner_reassessment_transport.py`
- `hermes_cli/kanban_owner_target.py`
- `hermes_cli/kanban_sync_client.py`
- `hermes_cli/kanban_sync_compat.py`
- `hermes_cli/kanban_sync_envelope.py`
- `hermes_cli/kanban_sync_hub_transport.py`
- `hermes_cli/kanban_sync_hub_urlopen.py`
- `hermes_cli/kanban_sync_quarantine.py`
- `hermes_cli/kanban_sync_quarantine_notice.py`
- `hermes_cli/kanban_sync_status_origin.py`
- `hermes_cli/kanban_worker_node.py`
- `hermes_cli/worker_drain_spawn_inventory.json`
- `tools/kanban_tools.py`
- `tools/kanban_tools_reassessment.py`

## Tests

Source test files changed or added by the series:

- `tests/hermes_cli/fixtures/board_emitted_owner_reassessment_evidence.json`
- `tests/hermes_cli/fixtures/core_emitted_owner_reassessment_receipt.json`
- `tests/hermes_cli/test_kanban_evidence_response_bounds.py`
- `tests/hermes_cli/test_kanban_evidence_response_typing.py`
- `tests/hermes_cli/test_kanban_owner_reassessment.py`
- `tests/hermes_cli/test_kanban_owner_reassessment_board_compat.py`
- `tests/hermes_cli/test_kanban_owner_reassessment_canonical.py`
- `tests/hermes_cli/test_kanban_owner_reassessment_hardening.py`
- `tests/hermes_cli/test_kanban_owner_reassessment_lifecycle.py`
- `tests/hermes_cli/test_kanban_owner_reassessment_pull_quarantine.py`
- `tests/hermes_cli/test_kanban_owner_reassessment_review_v11.py`
- `tests/hermes_cli/test_kanban_owner_reassessment_review_v11_push.py`
- `tests/hermes_cli/test_kanban_owner_reassessment_transport.py`
- `tests/hermes_cli/test_kanban_owner_target.py`
- `tests/tools/test_kanban_owner_reassessment_schema.py`

These were read as part of the mirror from the source diffs only. **Source test coverage was NOT executed in this mirror run**; no pass/fail result is claimed.

## Applying

Treat this series (source ordinals 6533-6538, 6 commits: 6533, 6534, 6535, 6536, 6537, 6538) as one dependency unit: later commits fix, harden or test earlier ones, so apply them together and in ordinal order rather than cherry-picking. Series-level ordering and the overall mirror procedure are described in [README.md](README.md). Applying means mirroring source into a checkout; it does not install, activate or deploy anything.

## Gotchas

- Opt-in only; it is never evidence of producer health.
- Two byte caps and connection disposal apply to error and redirect bodies.
- Nothing in this document is a deployment or live-behaviour claim.

## Why core, not a plugin?

Schema/sync.
