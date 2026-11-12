# Qualified historical terminal carry recovery

Patches: **2335–2339**.

## Behavior

A retired task owner is not treated as completed work. `hermes_cli.kanban_terminal_carry.register` is an explicit operator API that, given an exact qualified card, a preserved owner SQLite database and its SHA256, verifies the original completion proof and writes one local repair receipt (not a task completion or origin binding). Retained rows with a matching receipt are annotated stale with `terminal_carry_verified` rather than erased; proof loss, a newer generation, stale Board shadow or wrong identity returns them to an unknown obligation.

## How it works

Registration requires a human session in the origin state database, an unambiguous qualified creation subscription, an exact shepherd link, and the original `observe_owner_task`/`_verified_terminal` result; goal-mode needs task-bound judge receipts (2336). Evidence is read immutably (nonempty WAL refused). Current-board generation fields and the portable completion UID are compared (numeric event IDs are never remapped). Follow-ups: receipt decoding hardened (2337), nested JSON `RecursionError` and Board schema-skew `IndexError` retain unknown rather than aborting projection (2338), and live current databases use the guarded readers while immutable evidence does not (2339). Receipts live under the origin profile home's `kanban/terminal-carry/*.json`; duplicates are ambiguous and release nothing.

## Code

`hermes_cli/kanban_terminal_carry.py`, `kanban_session_activity.py`; design note `docs/terminal-carry-recovery.md`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/hermes_cli/test_kanban_terminal_carry.py` covers exact-identity release, wrong/ambiguous identity, missing owner runs/events, generation mismatch, proof loss, fresh-row controls, snapshot carry integration, malformed receipts, WAL policy, path traversal, storage failure after release, reopen supersession and guarded readers.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

This is trusted local-home administrative authority: a file hash binds evidence bytes but does not authenticate who authored them, so establish snapshot provenance independently. Deleting the receipt withdraws the release. Never substitute a Board replica as evidence.

## Plugin

Core change; no plugin required.
