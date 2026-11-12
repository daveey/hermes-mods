# Kanban placement recovery: promotion and stale-pin recovery via the Board

Patches: 2817, 2818, 2819, 2820, 2821, 2822, 2823, 2824, 2825, 2826, 2827, 2828, 2829, 2830.

- **2817** (`ae03f11de6`, fix(kanban): recompute promotion eligibility before Board recovery)
- **2818** (`0422346d05`, fix(kanban): recover stale machine pins through Board authority)
- **2819** (`412e1e20d1`, test(kanban): retain independent evidence fence control)
- **2820** (`209cee82df`, fix(kanban): preserve local custody and current specs during recovery)
- **2821** (`9b534e07cf`, fix(kanban): require fresh recovery evidence and preserve courier custody)
- **2822** (`f23c28b751`, fix(kanban): back off unsupported recovery endpoints without stopping reserves)
- **2823** (`5da8cddc23`, fix(kanban): isolate recovery scans from reservation renewal budget)
- **2824** (`5b05ae7a1a`, test(kanban): exercise producer intent and approval recovery ingress)
- **2825** (`0aa9a05f01`, test(kanban): compose dispatch recovery with authoritative feed)
- **2826** (`410ea2e347`, test(kanban): isolate recovery guards and bound cache state)
- **2827** (`7735109678`, test(kanban): isolate placement policy clauses and skill carriage)
- **2828** (`a59febd4e7`, test(kanban): fence recovery scan slots and cursor owners)
- **2829** (`23f07c6bb8`, test(kanban): isolate renewal and recovery routing)
- **2830** (`000175d5a9`, Preserve ordinary reservation snapshots during recovery ticks)

Mirrored in the 2788-2848 pass (part b); see [mirror-2909-verification.md](mirror-2909-verification.md).

## Behavior

- 2817: `promotion_mode` recomputes eligibility from today's inputs (not the creation-time portable reason) before asking the Board; explicit intent, policy and prior move/repin events are preserved. `Hub.recover` posts creator/CAS recovery to `/v2/boards/{board}/placement/recover`.
- 2818: `recovery_mode` proposes `stale_pin` only from an affirmative registry entry: revoked/inactive/hold or `last_seen` older than 30 min (milliseconds), only for a single `machine:` pin, never overriding independent constraints or an unapplied owner move.
- 2819/2820: portable-reason audit control tests; recovery rejects unsafe or rejected local specs, rechecks the spec between calls, retains explicit intent on audit failure, preserves unpublished custody and stored creation path.
- 2821: stale evidence must be fresh (`registry(require_fresh=True)` refuses a last-good cache); courier custody (`origin_creation_intents`) blocks recovery; dispatch without a pool does not pull.
- 2822: an absent recovery endpoint (404/405/501) backs off per (hub url, board, gateway) for 600 s without stopping ordinary policy reservations; other errors stay observable; `recover` is a queue-reason stage.
- 2823: recovery gets its own bounded scan (`select_rows`) with a rotating process-local cursor so legacy rows cannot starve policy renewals.
- 2824-2829: test-only coverage (ingress, authoritative feed, guard isolation, bounded cache, policy clauses, skill carriage, scan slots and cursor owners, renewal/recovery routing).
- 2830: recovery revalidates current inputs (`unchanged_ready`) while ordinary renewals keep the pre-loop snapshot.

## How it works

- `hermes_cli/kanban_placement_recovery.py` (new), `kanban_worker_placement.py`, `kanban_sync_client.py` (`Hub.recover`), `kanban_worker_node.py`, `kanban_queue_reason.py`.

## Code

- `hermes_cli/kanban_placement_recovery.py`
- `hermes_cli/kanban_sync_client.py`
- `hermes_cli/kanban_worker_placement.py`
- `hermes_cli/kanban_worker_node.py`
- `hermes_cli/kanban_queue_reason.py`

## Tests

- `test_kanban_placement_recovery.py`, `test_kanban_stale_pin_recovery.py`, `test_kanban_portable_reason_audit.py`, `test_kanban_recovery_safety.py`, `test_kanban_recovery_observation.py`, `test_kanban_recovery_backoff.py`, `test_kanban_recovery_fairness.py`, `test_kanban_recovery_ingress.py`, `test_kanban_recovery_feed.py`, `test_kanban_recovery_cache.py`, `test_kanban_recovery_guard_isolation.py`, `test_kanban_recovery_routing.py`, `test_kanban_no_pool_audit.py` (all under `tests/hermes_cli/`).

Test files touched by these patches:

- `tests/hermes_cli/test_kanban_placement_recovery.py`
- `tests/hermes_cli/test_kanban_stale_pin_recovery.py`
- `tests/hermes_cli/test_kanban_portable_reason_audit.py`
- `tests/hermes_cli/test_kanban_recovery_safety.py`
- `tests/hermes_cli/test_kanban_recovery_observation.py`
- `tests/hermes_cli/test_kanban_recovery_backoff.py`
- `tests/hermes_cli/test_kanban_recovery_fairness.py`
- `tests/hermes_cli/test_kanban_recovery_ingress.py`
- `tests/hermes_cli/test_kanban_recovery_feed.py`
- `tests/hermes_cli/test_kanban_edit_requires.py`
- `tests/hermes_cli/test_kanban_recovery_cache.py`
- `tests/hermes_cli/test_kanban_recovery_guard_isolation.py`
- `tests/hermes_cli/test_kanban_recovery_routing.py`
- `tests/hermes_cli/test_kanban_no_pool_audit.py`

The feature suites above landed with the source commits and were **not executed by this mirror pass**; passing claims in the source commit messages are not re-verified here. Full-series `git am` application is a structural check only.

## Applying

Apply as part of the full ordered series from the documented merge-base with `git am -3 --empty=keep` (README **Applying**). These patches depend on earlier core files and are not certified standalone. To port to a newer upstream, re-implement the Code files above and keep the listed tests green.

## Gotchas

- The Board alone changes assignments; these patches only propose. Process-local backoff and cursor maps are bounded to 1024 entries. A hub older than the recovery endpoint is tolerated by the backoff.
- Patch numbers follow fork-log ordinals; privacy substitutions use example identities.

## Plugin?

No: these change core modules (gateway, hermes_cli, agent, state, CI scripts) that a plugin cannot hook.
