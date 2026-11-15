# Conservative unloaded-host notice gates

Patches: **4266–4268** (4352 snapshot).

## Behavior

Independent host subpasses can skip only when structural emptiness is proven unchanged. Pending, busy, held and unknown inputs retain their retry paths. Live-session changes and due times rearm work without requiring a database write.

Foreign-profile notice scans borrow kept read-only stores and acquire writers only for writes or refreshes.

## How it works

Each subpass owns a key containing the live set and its structural inputs. Pre-work stamps cannot bless a later commit, exceptions discard earlier stamps, and a successful empty return is not a delivery receipt. Card-change fanout remains hot.

A scoped profile census is reused only within one host pass. Store identity, data_version and total_changes fence empty proofs. Guarantee deadlines use effective_due, including code-change recovery, and the earlier stamped deadline still bounds skipping. Delegation periodic recovery remains due. Remote-wake fixtures terminate at the host-pass boundary.

## Code

- `tui_gateway/kanban_notice_session_store.py`
- `tui_gateway/kanban_unloaded.py`
- `tui_gateway/kanban_unloaded_gates.py`
- `tui_gateway/notice_guarantee.py`
- `tui_gateway/reap_notices.py`

## Tests

**None executed in this documentation pass.** The following test paths were read from the assigned normalized patches; they are shipped coverage, not fresh pass results. A renamed path is listed as it appears in its patch. Implementation reports and mutation-oriented test names are not evidence of a mirror rerun.

- `tests/tui_gateway/test_h2_gate_mutations.py`
- `tests/tui_gateway/test_h2_kept_notice_readers.py`
- `tests/tui_gateway/test_h2_killed_probe.py`
- `tests/tui_gateway/test_h2_profile_census.py`
- `tests/tui_gateway/test_kanban_remote_wake.py`
- `tests/tui_gateway/test_kanban_unloaded_gates.py`

## Applying

Use the **full ordered 4352 snapshot recipe** in [core/README.md](README.md#applying), including its replay bridges and **mandatory post-replay repair overlay and repaired-tree verification**. These ordinals are documentation groupings, not independent cherry-pick sets. Do not apply only this feature's patches or omit the repair overlay.

## Gotchas

Read/probe failures run the recovery path rather than proving emptiness. Positive killed-child rows remain hot. No delivery-success inference is made from an empty list. Shipped mutation-oriented tests are not a mutation run performed by this mirror.

## plugin?

No. This changes core behavior and cannot be installed as a standalone plugin.
