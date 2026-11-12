# Explicit withdrawal receipts across gateway replicas

Patches: **2403**.

## Behavior

An owner publishes a withdrawal receipt only for a new explicit archive withdrawal, never inferred from status; malformed or non-mapping envelopes and stale (reopened) withdrawals are silent; recipients are captured at archive time independently of later link cleanup and never recovered from mutable bindings. A released shepherd cannot reenter as an origin, a deferred persist rechecks chat exclusion, and an old claim/ack cannot settle a new event.

## How it works

`kanban_withdrawal_notice.publish/envelope` build the content-free envelope inside the collector snapshot; `kanban_withdrawal_recipients.snapshot/published` capture identities in the archive write transaction; `kanban_remote_notice_state.state` supplies the shared event identity for arrival, claims and stale projections.

## Code

`hermes_cli/kanban_withdrawal_notice.py`, `kanban_withdrawal_recipients.py`, `kanban_db_archive.py`, `kanban_origin_ledger.py`; `tui_gateway/kanban_remote_notice_state.py`, `kanban_remote_notices.py`, `kanban_remote_observations.py`, `remote_index_projection.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/tui_gateway/test_remote_withdrawal_transport.py` and `test_remote_withdrawal_guards.py`.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

Multi-gateway delivery is tested with fakes, not live replicas.

## Plugin

Core change; no plugin required.
