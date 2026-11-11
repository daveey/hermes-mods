# Kanban final admission gate excludes the claim's own grant echo

Patches:

- `patches/2199-daveey-kanban-admission-the-final-cap-gate-excludes-.patch`

The Board grants a lease by writing the card `running` with a new run uid before the claim transaction; the sync pull can apply that echo in between. The final cap gate then counted the claim's own unused grant as another run (six real runs read 7/7) and the claim CAS lost to the echoed `running` status.

## Behavior

- The final cap gate and the pre-commit recount exclude exactly one row: this claim's own unused grant echo, proven inside the claim's write transaction. Any other running row still counts; seven real runs still refuse.
- The CAS adopts that row (status ready/review, or the proven echo run) and adopts the pending-sync/echo run row instead of losing.
- A claimed event gains `hub_admission {active_before, active_after, excluded_run_uid}`; refusal logs include the counts.
- Every predicate must hold or nothing is excluded: unconsumed gate with a granted lease, primary board mode, file identity (dev, inode), row running with no claim lock, holder equals this gateway, unexpired lease, current run uid equal to the grant, unlocked and not ended; more than one match fails closed.

## How it works

`kanban_grant_echo.own_grant_echo` builds a `GrantEcho`; `_bind_echo` attaches it to the gate; `machine_metrics.active_card_runs(exclude=, detail=)` re-proves it on its own read connection.

## Code

`hermes_cli/kanban_grant_echo.py` (new), `kanban_admission.py`, `kanban_db_sync.py`, `machine_metrics.py`.

## Tests

Tests are the coverage supplied with the patches. They were **not executed in this mirror**.

`tests/hermes_cli/test_kanban_hub_admission_grant_echo.py` (about 18 tests with a real lease client and a fake Board service: six locals plus own echo admit one, room under the cap, seven refuse, unrelated echoes still counted, identity predicates, CAS recheck, replayed echo not adopted, simultaneous claimers, unreadable board, duplicated inode).

## Applying

Apply the listed patches in order as part of the full ordered replay described in [README.md](README.md) (`git am -3 --empty=keep`); no extra configuration unless named above.

## Gotchas

The generic occupancy counter is deliberately unchanged. A foreign gateway's lockless echo is not excluded.

## Plugin

None.
